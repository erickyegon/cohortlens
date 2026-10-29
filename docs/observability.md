# Observability

Traces, metrics and JSON logs share one trace ID; allowlist span processor and redacting log processor; OTLP export to Grafana Cloud (local: lgtm).

## What the API emits (issue #20)

One request is one trace. The root span `http.request` gives the `X-Trace-Id` (the OpenTelemetry trace id, 32 hex characters) that every response carries and that the error bodies, stored runs and log lines repeat. A cohort run adds the child spans `cohort.run`, `validate`, `compile`, `execute`, `baseline` and `suppress` (`app/runs.py`).

| Signal | Name | Labels / fields |
|---|---|---|
| Metric | `cohortlens_cohort_runs_total` | `outcome` (`success`, `failed`, `timeout`: a closed set), `environment`, `revision` |
| Metric | `cohortlens_cohort_run_duration_seconds` (histogram, buckets 0.05 to 10 s, with a boundary at the 3 s alert threshold) | the same three labels |
| Log | one JSON line per record on stdout, and the same record over OTLP | `timestamp`, `level`, `logger`, `message`, `service`, `environment`, `revision`, `trace_id`, `span_id`, and `exception` (the class name only) |

`environment` is `DEPLOYMENT_ENVIRONMENT` and `revision` is `SERVICE_VERSION` (the git sha). Runs that never started (an unknown cohort, an invalid id) are not counted.

**Export.** Only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (OTLP over HTTP; the standard `OTEL_EXPORTER_OTLP_HEADERS` carries credentials). Compose sets it to the local `lgtm` container; with no endpoint nothing is exported, but trace ids still exist. The same code exports to Grafana Cloud by changing those two variables.

## Exporting to Grafana Cloud (issue #22)

The same code exports to Grafana Cloud when two things are set: the endpoint (not secret) and the credential (secret). Nothing in the image changes, so dev and prod run the same digest and differ only in this configuration.

**One-time setup, by the owner:**
1. **Create the token** in the Grafana Cloud portal: open your stack, then the **OpenTelemetry (OTLP)** connection page (it shows the OTLP endpoint and the instance ID), click **Generate now**, name it `cohortlens-otlp`, and create it. Copy the token: it is shown **once**. Do not paste it anywhere except the hidden prompt below.
2. **Store it in Secret Manager without it appearing anywhere.** In Git Bash, in the repository folder:
   `bash scripts/store_otlp_secret.sh --env dev --instance-id <instance id>`
   The script asks for the token with hidden input in the terminal (nothing is echoed, so nothing reaches the screen, the chat or the shell history; it refuses to run with tracing on, and refuses piped input unless `--stdin` is passed, which is for automation only: `echo <token> | script` would put the token in your shell history), builds the header the exporter reads (`Authorization=Basic%20<base64 of instance:token>`, URL-encoded as the OpenTelemetry specification requires), creates the secret `cohortlens-dev-otlp-headers` if needed, lets `cohortlens-run-dev` read it, and stores a new version. **Store both before step 3**: run it again with `--env prod` for `cohortlens-prod-otlp-headers`, because the endpoint variable in step 3 is shared by dev and prod, and a deploy that references a secret with no version fails its api revision (traffic stays on the old one). It prints neither the token nor the header. **Rotating:** run it again to add a new version, then redeploy or promote (instances read the secret when they start), and revoke the old token in Grafana Cloud.
3. **Set the endpoint** as a repository variable (it is a URL, not a secret): `gh variable set GCP_OTLP_ENDPOINT --body https://<stack>.grafana.net/otlp` (for this stack: `https://otlp-gateway-prod-us-east-2.grafana.net/otlp`). Dev deploys and prod promotions pass it to `infra/gcp/deploy.sh`, which gives it, and the secret, to the **api** service only (the web service and the migration job export nothing). Unset, nothing is exported.
4. **Deploy:** the next dev deploy picks it up (or run **Actions → Deploy dev → Run workflow**); for prod, approve the next promotion.

`DEPLOYMENT_ENVIRONMENT` (`dev` or `prod`) and `SERVICE_VERSION` (the git sha) label every signal, so dev and prod are told apart in one stack.

**To check it works** (Grafana Cloud, **Explore**): Prometheus `cohortlens_cohort_runs_total`; Tempo, search for `service.name = cohortlens-api`; Loki `{service_name="cohortlens-api"}`. Run a cohort in the UI, then paste the `Reference:` trace id of a failed run, or any `X-Trace-Id`, into Tempo's trace id search, and open the logs from the trace.

**Known risk, Cloud Run:** CPU is throttled between requests, so batched export from an idle instance may be delayed or dropped. The exporters flush every 2 s (spans) and 15 s (metrics). If signals arrive late or not at all, the fix is to run the api with CPU always allocated (`--no-cpu-throttling`), at a small cost; check Cloud Logging for `Failed to export` warnings from the api first.

## Dashboard and alert rules (issue #23)

`infra/grafana/` holds a dashboard ("CohortLens core journey") and two alert rules as Grafana's own JSON, with placeholders for the stack's data-source ids. `infra/grafana/apply.sh` fills those in and creates or updates everything through Grafana's HTTP API; running it again changes nothing that is already right. No Terraform.

| Rule | Fires when | For |
| --- | --- | --- |
| `CohortRunErrorRateHigh` | at least 10 runs in 5 minutes and more than 5% of them failed or timed out | 2 minutes |
| `CohortRunLatencyHigh` | at least 20 runs in 10 minutes and p95 run duration above 3 s | 5 minutes |

The minimum-runs guard is part of each query, so a quiet system returns no data, and no data means OK. Every alert carries `service`, `environment`, `agent=oncall` and a `runbook_url` (see `docs/runbooks/`). The behaviour is tested with `promtool test rules` (`infra/grafana/alert-tests.yml`, run by `api/tests/unit/test_grafana_promtool.py` with `promtool` or Docker; skipped if neither exists, so CI without Docker relies on the text checks): eight synthetic environments cover a busy and failing system, a healthy one, a quiet one that fails every run (fewer than 10 runs: silent), timeouts counted as errors, 3.8% errors (below 5%: silent), slow, fast, and quiet-slow (fewer than 20 runs: silent). Five realistic edits to the expressions are shown to fail them. Not covered by these scenarios: the `for` durations (the test rules have no `for`), and fine thresholds (the 5% threshold is proven only within a band, since the closest scenario is 3.8%).

Notes on how the rules behave:
- The alert queries are instant, evaluated at the current time over their own `[5m]` / `[10m]` range selectors. The rule group `cohortlens` is created with Grafana's default evaluation interval (1 minute), which the `for` durations assume; the provisioning API does not set it.
- Each API instance exports under its own `service.instance.id`, so instances of one revision never share a series; the alert queries `sum by (environment)` across them. All three outcome series are created at zero when the process starts, so they are in the first export (within 15 s), because `increase()` cannot see a counter that first appears with value 1; a failure inside an instance's first 15 seconds can still be missed, which the sustained `for` durations and the minimum-run guards tolerate. `service.instance.id` is also a default Loki index label (`service_instance_id`), so each Cloud Run instance is its own log stream; the dashboard's log query selects on `service_name` and `deployment_environment` only.
- `apply.sh` overwrites the dashboard on every run: edits made in the Grafana UI are lost, so change `dashboard.json` instead. Rules are updated in place.

**Service-account token.** In your stack (`https://<stack>.grafana.net`): Administration, Users and access, Service accounts, Add service account; role **Admin** (it must create folders, dashboards and alert rules); Add service account token; copy it once. Then, in a Git Bash window:

```bash
bash infra/grafana/apply.sh --url https://<stack>.grafana.net --dry-run
bash infra/grafana/apply.sh --url https://<stack>.grafana.net
```

The first line only prints the plan. The second asks for the token at a hidden prompt (nothing is echoed; it refuses tracing and piped input, and passes the token to curl on a pipe, so it is in no argument, file or history). Afterwards delete the service account or its token in Grafana if you do not want to keep it.

## What never leaves the process (issue #21, NFR-3)

- **Allowlist span processor** (`app/telemetry`): a span reaches an exporter with only the attributes `http.request.method`, `http.route` (the route *template*, never the URL), `http.response.status_code`, `cohortlens.outcome`, `cohortlens.rule_count`, `cohortlens.step_count` and `cohortlens.revision`, **and each key only with a value of its own shape**: the method must be one of a closed set (anything else is exported as `_OTHER`, because the method is client-controlled), the route must look like a template, the status an integer 100 to 599, the outcome one of the three outcomes, the counts small integers, the revision `dev` or a git sha. Being on the list is not enough. Every event (where an exception message would be), every link and every status description is dropped. Spans are made by hand; there is no auto-instrumentation, which would add URLs and database statements.
- **Redacting log filter**: before any handler (stdout or OTLP) sees a record, bearer tokens, `api_key`/`token`/`password`/`secret` assignments, provider-style keys, URLs with credentials (connection strings) and email addresses become `[REDACTED]`. An exception is logged as its class name; its message and traceback never are. The OTLP handler works on a **copy** of the record and drops any `extra=` fields (the stock handler would export them as attributes). Redaction matches secrets by their shape (`key=value` and `key: "value"` assignments including quoted values, bearer and basic credentials, JWTs, connection strings, emails); it does **not** catch a secret written as plain prose such as "the password is hunter2", which is why the application never logs request data in the first place. Redaction is bounded: a message beyond 8,192 characters is cut and marked `[TRUNCATED]` rather than scanned, and every pattern has bounded quantifiers.
- **Leak test** (`api/tests/integration/test_telemetry_leaks.py`): runs the truth fixture through the real API with hostile input (a cohort named with an email and a bearer token, a failing run whose exception message carries a query, an email and a connection string, a timeout, validation errors, a concept search for an email), collects everything that would be exported (spans, log lines and records, metrics) and asserts that no person id key, source value or birth date of the fixture, none of the hostile strings and no token pattern appears anywhere.

**What is not covered:** only the `cohortlens` logger goes through the filter and over OTLP. The uvicorn **access log** (request path and query string) is switched off in the container (`--no-access-log`), because it would print them to stdout, which reaches Cloud Logging, outside the filter. Uvicorn's own error logs and other libraries' logs go to stdout/stderr unfiltered but are not exported over OTLP, and the application never puts request data in them. The stdout handler formats a record before the OTLP handler copies and reduces it; that order is tested.

The allowlist and the filter protect against the leaks nobody intended; they do not make it safe to put patient data in an allowed attribute. Add an attribute to the allowlist only if its name and values cannot carry data, and extend the leak test with it.

## Load generation must use realistic definitions

A run that exceeds the 10 s statement timeout is recorded with status `timeout` and counts as a failed run, so **timeouts count toward the error-rate alert** (`CohortRunErrorRateHigh`: failed or timed-out runs above 5% of runs over 5 minutes). The load generator (`loadgen/`, `make traffic`) must therefore only use realistic cohort definitions: the reference cohort and variations of it that finish in well under a second on the demo dataset.

Never use the pathological shapes documented in `docs/cohort-engine.md` (Performance): many rules over broad concepts, or `first_occurrence_only: false` with a broad index concept. Even within the cap they can take several seconds or time out, and a drill that trips the alert on its own traffic would page the on-call agent for a problem the load generator created. Chaos scenarios that want slow or failing runs inject them deliberately (see the chaos drills) and say so in their runbook.
