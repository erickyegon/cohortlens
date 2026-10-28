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

## What never leaves the process (issue #21, NFR-3)

- **Allowlist span processor** (`app/telemetry`): a span reaches an exporter with only the attributes `http.request.method`, `http.route` (the route *template*, never the URL), `http.response.status_code`, `cohortlens.outcome`, `cohortlens.rule_count`, `cohortlens.step_count` and `cohortlens.revision`, and only with short values. Every event (where an exception message would be), every link and every status description is dropped. Spans are made by hand; there is no auto-instrumentation, which would add URLs and database statements.
- **Redacting log filter**: before any handler (stdout or OTLP) sees a record, bearer tokens, `api_key`/`token`/`password`/`secret` assignments, provider-style keys, URLs with credentials (connection strings) and email addresses become `[REDACTED]`. An exception is logged as its class name; its message and traceback never are.
- **Leak test** (`api/tests/integration/test_telemetry_leaks.py`): runs the truth fixture through the real API with hostile input (a cohort named with an email and a bearer token, a failing run whose exception message carries a query, an email and a connection string, a timeout, validation errors, a concept search for an email), collects everything that would be exported (spans, log lines and records, metrics) and asserts that no person id key, source value or birth date of the fixture, none of the hostile strings and no token pattern appears anywhere.

The allowlist and the filter protect against the leaks nobody intended; they do not make it safe to put patient data in an allowed attribute. Add an attribute to the allowlist only if its name and values cannot carry data, and extend the leak test with it.

## Load generation must use realistic definitions

A run that exceeds the 10 s statement timeout is recorded with status `timeout` and counts as a failed run, so **timeouts count toward the error-rate alert** (`CohortRunErrorRateHigh`: failed or timed-out runs above 5% of runs over 5 minutes). The load generator (`loadgen/`, `make traffic`) must therefore only use realistic cohort definitions: the reference cohort and variations of it that finish in well under a second on the demo dataset.

Never use the pathological shapes documented in `docs/cohort-engine.md` (Performance): many rules over broad concepts, or `first_occurrence_only: false` with a broad index concept. Even within the cap they can take several seconds or time out, and a drill that trips the alert on its own traffic would page the on-call agent for a problem the load generator created. Chaos scenarios that want slow or failing runs inject them deliberately (see the chaos drills) and say so in their runbook.
