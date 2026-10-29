# ADR-003: Direct OTLP export to Grafana Cloud

Status: accepted · Date: 2026-10-29 (written after the decision, from the code and the E5 runs)

## Context
The dashboard, the alerts and the on-call agent all read the same signals, so the API's traces, metrics and logs must reach Grafana Cloud from Cloud Run. Cloud Run gives no host to run an agent on, the budget is a few dollars a month, and the project is a one-person demo on synthetic data. The same image runs in dev and prod, so the difference between them has to be configuration only.

## Decision
- **The API exports OTLP over HTTP straight to the Grafana Cloud OTLP gateway.** No collector and no sidecar. The endpoint is a repository variable (`GCP_OTLP_ENDPOINT`, not secret); the credential is one Secret Manager secret per environment, readable only by that environment's runtime account and never written into a file, an argument or the chat (`scripts/store_otlp_secret.sh` reads it from a hidden prompt).
- **Locally, the same code exports to the `lgtm` container in Compose**, by changing only the endpoint and headers. With no endpoint nothing is exported, but trace ids still exist.
- **Nothing leaves the process unless it is on an allowlist** (`api/app/telemetry/`): a span processor keeps a fixed set of attributes with values of their own shape, and a redacting filter runs before any log handler. A leak test feeds hostile input (an email in a cohort name, a bearer token in an exception message, query text) and checks the exported data.
- **Every process has its own `service.instance.id`** and every outcome series starts at zero, so Cloud Run instances do not overwrite each other's counters and a first failure is visible as a rate.

## Alternatives not chosen
- **An OpenTelemetry Collector or Grafana Alloy sidecar.** One more container per service to build, configure and pay for, and it would be throttled by Cloud Run in the same way as the API.
- **Google Cloud Monitoring and Trace, read from Grafana.** Two systems to keep in step, with the alert rules querying a different backend from the dashboard.

## Consequences
- **Cloud Run throttles CPU between requests,** so batched export could be late or dropped. The exporters flush every 2 s (spans) and 15 s (metrics). On dev and prod the signals arrived without changing CPU allocation (the owner's Grafana checks, `docs/progress-audit.md` section 11). The documented fix, if that changes, is CPU always allocated.
- **A new token needs a new revision.** A secret version is read when a revision starts, and a redeploy of an identical revision creates none. The first prod token was wrong (HTTP 401 every 15 s, requests unaffected), and the fix needed a revision created by hand outside the promote workflow (deviation 27 in the audit).
- **A wrong or missing token degrades telemetry, not the product:** the API keeps serving and logs the export failure.
- **Signals in flight are lost if an instance dies between flushes.** Accepted for a demo.
- **The endpoint and token tie the project to one Grafana Cloud stack.** Moving would mean new variables and a new secret, not new code.
