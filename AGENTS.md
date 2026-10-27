# AGENTS.md — CohortLens

## What CohortLens is
CohortLens is a web app that tells a real-world-evidence scientist whether enough patients exist in an OMOP database to run a proposed study. A user defines a cohort, clicks **Run feasibility**, and sees the final count, an attrition waterfall and a baseline table. Stack: React/Vite/TypeScript, FastAPI, Postgres 18, OpenTelemetry → Grafana Cloud, Cloud Run + Neon. All data is synthetic.

## Commands
Docker Compose is the primary interface (`docker compose up --build`); `make` targets are optional shortcuts and need `make` installed.

| Command | What it does |
|---|---|
| `docker compose up --build` (or `make up`) | Start frontend, API, Postgres (auto-seeded), local telemetry |
| `make test` | All unit + integration tests (backend and frontend). Must pass before any PR |
| `make e2e` | Playwright end-to-end tests against the Compose stack |
| `make lint` | Ruff + ESLint |
| `make traffic ENV=…` | k6 core-journey load |
| `make chaos SCENARIO=s1` | Scripted chaos drill |
| `make seed-cloud` | Load the OMOP seed into Neon (human only) |

## Hard rules
1. **Parameterized SQL only.** Build queries with SQLAlchemy Core. Never concatenate user values into SQL.
2. **No patient-level data or secrets in logs, spans, metrics or API responses.** Aggregates only; counts 1–10 are returned as `"<11"`.
3. **Every PR closes an issue** (`Closes #N`) and starts with an `Agent: <role>` line; commits carry an `Agent-Role:` trailer.
4. **Never edit `.github/workflows/`, `infra/` or `oncall/guardrails.py` unless you are the SRE agent.**
5. **Never merge.** Only the human merges and approves prod deploys.
6. **Schema changes are expand-then-contract** so rollback is always safe.
7. **Tests never call the real Anthropic API**; use recorded responses.
8. Do not commit raw datasets, `.env` files or credentials.

## Before you start a task, read…
| Task | Read first |
|---|---|
| Any feature | `docs/spec.md`, `docs/architecture.md` |
| Your role and handoffs | `docs/agents.md`, `docs/process.md` |
| Writing tests | `docs/testing.md` |
| SQL or the cohort engine | `docs/cohort-engine.md` |
| Telemetry, dashboards, alerts | `docs/observability.md` |
| Deploys and rollbacks | `docs/deployment.md` |
| Responding to an alert | `docs/runbooks/` (one runbook per alert) |
| Why a decision was made | `docs/adr/` |

## Roles
Spec · Test · Builder · Reviewer · SRE — definitions in `.claude/agents/`, handoffs in `docs/agents.md`.
