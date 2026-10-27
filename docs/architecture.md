# Architecture

Status: living document. Sections marked _planned_ describe issues that are not built yet.

## Components

```
browser ──► web (nginx :8080, React build) ──/api──► api (FastAPI :8000) ──► Postgres 16
                                                            │                 ├─ schema cdm  (OMOP, read-only)
                                                            │                 └─ schema app  (cohorts, runs)
                                                            └─ OTLP ─► lgtm (local) or Grafana Cloud   _planned_
```

| Service | Image | Role |
|---|---|---|
| `db` | `postgres:16` | Loads the committed OMOP seed on first start; creates the two application roles |
| `migrate` | api image | One-shot `alembic upgrade head` for schema `app`, then exits |
| `api` | `api/Dockerfile` (python:3.12-slim, non-root) | Cohort definitions, feasibility runs, health endpoints |
| `web` | `web/Dockerfile` (node build → nginx) | UI; proxies `/api/` to `api` so there is no CORS |
| `lgtm` | `grafana/otel-lgtm` | Local Grafana/Tempo/Loki/Prometheus |

Start order is enforced by healthchecks: `db` (healthy only after first-start initialisation, checked over TCP) → `migrate` (completed successfully) → `api` (healthy) → `web`.

## Two schemas, two roles, two connections

The API holds **two separate database connections**, each with the least privilege it needs:

| Connection | Env var | Postgres role | Privileges | Used for |
|---|---|---|---|---|
| reader | `DATABASE_URL_READER` | `cohortlens_reader` | `SELECT` on schema `cdm` only; no access to `app` | Compiled feasibility queries, concept search, baseline table. Sets `statement_timeout = 10 s` on every session |
| app | `DATABASE_URL_APP` | `cohortlens_app` | Owns schema `app`; **no access to `cdm`** | Cohort CRUD, writing `cohort_run`, Alembic migrations |

Why two roles:
- A bug or injection in the query path cannot modify clinical tables or the app's own records: the reader cannot write anything, and the compiled SQL runs only over `cdm`.
- The app role cannot read patient-level rows at all, so code that persists run results cannot accidentally join them in.
- Each connection can be tuned separately (the reader has the query timeout; the app connection does not).

Consequences for the code:
- A run is **never one transaction across both roles**. The flow is: load the definition (app) → compile → execute counts (reader) → suppress → write `cohort_run` (app).
- If the reader fails (database unreachable, timeout) the run is still recorded as `failed` or `timeout` through the app connection, with the trace id, so the user sees a reference and run history stays complete. If the app connection itself is down, the API returns an error with the trace id and nothing is recorded.
- `cohort_run.step_counts` and `final_count` store the **already-suppressed** values (`"<11"` as a string), so small counts never reach the app schema, logs or telemetry.
- `/readyz` checks both connections: a `select count(*) from cdm.person` through the reader and a `select 1 from app.cohort_definition` through the app role.
- Only the db container's superuser (`postgres`) owns `cdm`; it is created by the seed and granted read-only to the reader in `db/init/20-grants.sql`. In the cloud the same two roles are created on the Neon branches.

Verified by `scripts/smoke_compose.sh`: reader can read `cdm`, cannot read `app`, cannot write `cdm`; the app role can write `app` and cannot read `cdm`.

## API

The API serves its routes **without** an `/api` prefix (`/healthz`, `/cohorts`, ...). The nginx proxy (and the Vite dev server) strips `/api`, so the public paths are `/api/healthz`, `/api/cohorts`, and so on.

| Method | Path (public) | Purpose | Success | Errors |
|---|---|---|---|---|
| `POST` | `/api/cohorts` | Create from a cohort definition (the request body is the definition itself) | `201`, `Location`, the cohort with `id` and `version` 1 | `422`, `413` |
| `GET` | `/api/cohorts` | List summaries (`id`, `name`, `version`, `created_at`, `updated_at`), most recently updated first | `200` | |
| `GET` | `/api/cohorts/{id}` | One cohort including its `definition` | `200` | `404` |
| `PUT` | `/api/cohorts/{id}` | Replace the definition; `version` increases by 1 | `200` | `404`, `422`, `413` |
| `DELETE` | `/api/cohorts/{id}` | Delete the cohort and its runs | `204` | `404` |

Rules that apply to every route:

- **Validation first.** The definition goes through `parse_definition` (docs/cohort-engine.md) before anything is stored, and the *validated, normalised* definition is what is saved. An invalid definition is a `422` and nothing is stored or changed.
- **Error body.** `{"error": "<message>", "trace_id": "<32 hex>"}`, plus `"errors": [{"field": "rules.1.window.end_days", "message": "..."}]` for a `422`. The `trace_id` is the same value as the `X-Trace-Id` response header, which every response carries (FR-12). Messages never echo submitted values, and database or connection details never appear in a body.
- **Not found.** An unknown id and a malformed id are both `404` (`Cohort not found`), never a `422` or a crash.
- **Limits.** A request body over 256 KiB (exactly 262,144 bytes is accepted, one more is not) is a `413`, rejected while streaming, before it is parsed; this also holds for chunked bodies without a `Content-Length`.
- **Database failures.** A connection-level failure (refused, timed out, shutting down, too many connections) is a `503` "The database is unavailable"; a statement or lock timeout is a `503` "The database took too long to respond"; any other database error (a bad statement, a missing table) is a plain `500`. Connections to an unreachable database give up after 5 seconds (`CONNECT_TIMEOUT_S`) so the user is not left waiting. No body ever contains connection details.
- **Logging.** Every failure (a 500 or a database error) writes one log line `request failed exception=<class> trace_id=<id>`: the exception class and the trace id only, never the message, which can carry SQL, parameters, hosts or credentials (NFR-3). That is what lets support go from the reference a user quotes to the failure.
- **Unknown routes and methods** use the same body (`{"error": "Not Found", "trace_id": ...}`).
- Only definitions are handled here; no endpoint returns patient-level rows (FR-11).

## Request flow: Run feasibility _(planned: run endpoint issue)_

1. `POST /api/cohorts/{id}/runs` → span `cohort.run`
2. `cohort.validate`: Pydantic validation of the stored definition
3. `cohort.compile`: SQLAlchemy Core builds one CTE chain with bound parameters (`docs/cohort-engine.md`)
4. `cohort.execute`: reader connection returns the count after every step in one round trip
5. `cohort.baseline`: second query over the final cohort
6. `cohort.suppress`: counts 1–10 become `"<11"`
7. App connection inserts `cohort_run`; the response carries `X-Trace-Id`

## Concept search

`GET /api/concepts?q=` (served as `/concepts`; nginx strips `/api`) feeds the typeahead. It reads the vocabulary through the **reader** connection only (a test runs it with no app database configured).

- **Results**: up to `limit` (default 20, 1 to 50) of `{concept_id, name, domain}` with `domain` spelled `condition` or `drug`, the spelling the cohort definition uses. Only condition and drug concepts are searchable (a definition cannot use any other domain), only valid ones, and only standard (`S`) or classification (`C`) concepts, because definitions use classes such as ATC groups as ancestors. `domain=condition|drug` narrows the search.
- **Matching**: case-insensitive substring of the concept name; ordered prefix matches first, then word starts, then the rest, shorter names first within a rank, then by name, then by id. A "word start" is a match right after a space, so `(lisinopril)` or `a/lisinopril` rank as ordinary mid-word matches. Case folding uses Python's `lower()` on the query and PostgreSQL's `lower()` on the name, which disagree for a few non-ASCII letters (Turkish dotted capital I, final sigma); the demo vocabulary is all ASCII.
- **Short queries**: after trimming, fewer than 2 characters returns `[]` and runs no query. No match is also `200` and `[]`, never an error. More than 100 characters, control characters (including NUL), an unknown `domain` or a bad `limit` are `422` with a field-level error (`q`, `domain`, `limit`) in the standard error body.
- **Safety**: every value is a bound parameter and `%`, `_` and `\` in the query are matched literally.
- **Speed**: 30 to 90 ms per search through the API on the 26k-concept seed once the connection is open (a sequential scan with a substring match; the worst case, a two-letter query at limit 50, is about 80 ms); the first request after start-up took about 350 ms in one measurement because it opens the connection. The 500 ms budget is enforced by a seed test (`make verify-seed`) on the best of three requests. A full OMOP vocabulary (millions of concepts) would need a trigram index (`pg_trgm`).

## Frontend

React 18 + Vite + TypeScript in `web/`, served by nginx, which proxies `/api` to the API (so there is no CORS and no API URL setting). It is deliberately minimal: working and tested, not polished. No router or UI library: `App.tsx` switches between views with state.

| File | Role |
|---|---|
| `api.ts`, `types.ts` | Typed client for the routes in the API section; an `ApiError` carries the status, the field errors and the trace id (from the body, or the `X-Trace-Id` header) |
| `form.ts` | The builder's editing state (numbers as text while typing), conversion to and from the API definition, client-side validation keyed by the API's dotted field paths, `moveRule` |
| `CohortList.tsx` | Saved cohorts with the last run's count (a suppressed count shows as `<11`) and time (fixed `YYYY-MM-DD HH:MM UTC` format), open and new |
| `Builder.tsx`, `ConceptPicker.tsx` | The builder: index event with concept typeahead, demographics, observation window, up to 10 rules that can be reordered, toggled include/exclude and removed; saves with `POST` (new) or `PUT` (existing) |

Behaviour worth knowing:

- **Validation.** The form mirrors the cheap server rules (age order, windows, required fields, the rule cap and the all-events cap) so the user sees a field-level message before saving, and **Save does nothing while a field has an error**. Whatever the server still rejects (`422`) is shown on the matching field by the same dotted path. A failure with a trace id is shown as `Something went wrong. Reference: <trace id>` (FR-12).
- **Reopening** restores every field. The API stores concept ids only, so a concept chosen in an earlier session is shown as `Concept <id>`; one picked in this session shows its name.
- **Tests.** Vitest + Testing Library with a stubbed `fetch` (`src/test-utils.ts`); `make test` and the CI `test` job run them. Browser end-to-end tests (Playwright, `make e2e`) are a separate, later item.

## Data

- `cdm`: nine OMOP tables (person, observation_period, visit_occurrence, condition_occurrence, drug_exposure, measurement, death, concept, concept_ancestor). Source and decision: `docs/adr/001-omop-subset-over-custom-schema.md`. Schema DDL: `data/etl/cdm_ddl.sql` (added in the truth-fixture PR, #28).
- `app`: `cohort_definition`, `cohort_run` (Alembic migration `0001`). Migrations are expand-then-contract (`docs/deployment.md`).
- Seed: `data/seed/omop_subset.sql.gz`, regenerated by `data/etl/synthea_to_omop.py`; loaded by the Postgres entrypoint, so nothing but Docker is needed to run the project.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_PASSWORD`, `COHORTLENS_READER_PASSWORD`, `COHORTLENS_APP_PASSWORD` | local-only defaults | Database credentials (override in `.env`) |
| `DB_PORT` | `15432` | Host port for Postgres (bound to 127.0.0.1) |
| `SERVICE_VERSION` | `dev` | Git SHA baked into the image; becomes `service.version` on telemetry |
