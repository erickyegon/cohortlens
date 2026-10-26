# Architecture

_Status: stub — filled during phase 3._

Components: web (React/nginx) → api (FastAPI) → Postgres (schemas `cdm` read-only, `app` read-write). Telemetry: OTLP → Grafana Cloud (local: lgtm container).

## TODO
- Component diagram
- Request flow for Run feasibility
- Schema ownership and roles
