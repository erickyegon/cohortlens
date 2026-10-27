# Observability

Traces, metrics and JSON logs share one trace ID; allowlist span processor and redacting log processor; OTLP export to Grafana Cloud (local: lgtm).

_Details added during phase 5._

## Load generation must use realistic definitions

A run that exceeds the 10 s statement timeout is recorded with status `timeout` and counts as a failed run, so **timeouts count toward the error-rate alert** (`CohortRunErrorRateHigh`: failed or timed-out runs above 5% of runs over 5 minutes). The load generator (`loadgen/`, `make traffic`) must therefore only use realistic cohort definitions: the reference cohort and variations of it that finish in well under a second on the demo dataset.

Never use the pathological shapes documented in `docs/cohort-engine.md` (Performance): many rules over broad concepts, or `first_occurrence_only: false` with a broad index concept. Even within the cap they can take several seconds or time out, and a drill that trips the alert on its own traffic would page the on-call agent for a problem the load generator created. Chaos scenarios that want slow or failing runs inject them deliberately (see the chaos drills) and say so in their runbook.
