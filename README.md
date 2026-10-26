# CohortLens

Tells a real-world-evidence scientist, in seconds, whether enough patients exist in an OMOP database to run a proposed study.

> Status: phase 1 (spec and scaffolding). See [docs/spec.md](docs/spec.md).

## Problem
Feasibility counts for observational studies are hand-written SQL, slow to change and hard to share.

## Users
RWE scientists, epidemiologists and HEOR analysts scoping a study protocol.

## Core journey
Create a cohort → Run feasibility → final count, attrition waterfall, baseline table → save, reopen, edit, re-run.

## Expected behaviour
p95 run under 3 s; counts below 11 shown as "<11"; synthetic data only.

## Out of scope
Real patient data, accounts beyond a demo login, statistics beyond descriptive counts, free-text cohort entry (stretch).

## Quick start
```bash
docker compose up --build   # or: make up
```

- UI: http://localhost:8080 (nginx, proxies `/api`)
- API: http://localhost:8000/healthz, `/readyz`
- Grafana (local telemetry stack): http://localhost:3000
- Postgres: `127.0.0.1:15432` (database `cohortlens`)

First start loads the committed OMOP seed automatically; later starts reuse the `pgdata` volume. `bash scripts/smoke_compose.sh --clean` runs the stack smoke test from scratch (needs Docker; `make` is optional).

## Live URLs / demo video
TBD
