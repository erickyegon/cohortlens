# CohortLens

Tells a real-world-evidence scientist, in seconds, whether enough patients exist in an OMOP database to run a proposed study.

> Status: all six phases are built, with the exceptions listed under the scorecard. Dev and prod are live (synthetic data only), CI/CD deploys dev on every merge and promotes the same image to prod behind an approval, and the on-call agent rolled back a faulty release in a drill on dev. The honest account of what is and is not done is [docs/progress-audit.md](docs/progress-audit.md). The product specification is [docs/spec.md](docs/spec.md).

## Live URLs and demo

| | Web UI | API |
|---|---|---|
| **Prod** | <https://cohortlens-web-prod-rqflqlsgzq-ul.a.run.app> | <https://cohortlens-api-prod-rqflqlsgzq-ul.a.run.app> (`/version`, `/readyz`) |
| **Dev** | <https://cohortlens-web-dev-rqflqlsgzq-ul.a.run.app> | <https://cohortlens-api-dev-rqflqlsgzq-ul.a.run.app> (`/version`, `/readyz`) |

At its last promotion prod served exactly the image digests dev served. The data is synthetic (5,724 persons); the reference cohort ends at **481** patients. Cloud Run scales to zero, so the first request after a quiet period can take a few seconds.

**Demo video:** not yet recorded (the link will be added here).

**Heads-up for reviewers: two tokens of the on-call agent expire around 28 November 2026.** The GitHub token that lets Grafana start the agent and the Grafana token the agent reads metrics with were both made for 30 days. After that the agent stops responding to alerts, quietly; everything else keeps working. How to renew them (five minutes, no code change): [docs/oncall.md, "Renewing the tokens"](docs/oncall.md#renewing-the-tokens-every-30-days).

### Screenshots

_To be added by the owner as files in `docs/img/` (names below), then shown here._

| File | What it shows |
|---|---|
| `docs/img/results-desktop.png` | Results screen for the reference cohort: final count, attrition waterfall, baseline |
| `docs/img/dashboard.png` | Grafana "CohortLens core journey" with data |
| `docs/img/alert-firing.png` | `CohortRunErrorRateHigh` firing during the drill |
| `docs/img/trace.png` | One trace in Grafana (trace id visible) |
| `docs/img/logs.png` | The log line carrying the same trace id |
| `docs/img/incident-78.png` | Incident #78, closed by the on-call agent |
| `docs/img/prod-approval.png` | The production approval gate on a promotion |

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

## Scorecard: where to find the evidence

The course rubric has 12 criteria (24 points, 10 to pass). Each row says where to look; [docs/progress-audit.md](docs/progress-audit.md) gives the verified evidence, the commands that were run and what is not done.

| # | Criterion | Where to look |
|---|---|---|
| 1 | Problem description | This README (Problem, Users, Core journey, Expected behaviour, Out of scope) |
| 2 | Product specification | [docs/spec.md](docs/spec.md) (stories, requirements, Given/When/Then); the public project board and the issues, each with acceptance criteria |
| 3 | Agent team | [.claude/agents/](.claude/agents) (spec, test, builder, reviewer, sre), [docs/agents.md](docs/agents.md), [docs/process.md](docs/process.md); every PR starts with an `Agent:` line and commits carry `Agent-Role:` trailers |
| 4 | Context management | [AGENTS.md](AGENTS.md) and the modular docs it links: [architecture](docs/architecture.md), [cohort engine](docs/cohort-engine.md), [testing](docs/testing.md), [deployment](docs/deployment.md), [observability](docs/observability.md), [on-call](docs/oncall.md), [ADRs](docs/adr/) |
| 5 | Application | Live URLs above; locally `docker compose up --build`; the cohort engine is [docs/cohort-engine.md](docs/cohort-engine.md) |
| 6 | Testing | `make test` ([docs/testing.md](docs/testing.md)): backend unit and integration tests (Postgres in a container), frontend tests, telemetry leak test, on-call replay tests; CI runs them on every PR |
| 7 | Containerization | `docker compose up --build` starts web, API, database (auto-seeded) and a local telemetry stack; `scripts/smoke_compose.sh --clean` checks it from scratch |
| 8 | Deployment | Cloud Run and Neon, [docs/deployment.md](docs/deployment.md), [ADR-002](docs/adr/002-cloud-run-and-neon.md); the live URLs above |
| 9 | CI/CD | [.github/workflows/](.github/workflows): CI with required checks, auto-deploy to dev on every merge, promotion of the same digest to prod behind the `production` approval, rollback workflow; rollback design in [ADR-004](docs/adr/004-rollback-by-traffic-shift.md) |
| 10 | Observability | Traces, metrics and JSON logs sharing one trace id, exported to Grafana Cloud: [docs/observability.md](docs/observability.md), [ADR-003](docs/adr/003-direct-otlp-to-grafana-cloud.md); dashboard `infra/grafana/dashboard.json` |
| 11 | Alerting on user impact | `infra/grafana/alert-rules.json` (error rate and p95 latency), rule tests in `infra/grafana/alert-tests.yml`, runbooks in [docs/runbooks/](docs/runbooks) |
| 12 | On-call agent | [docs/oncall.md](docs/oncall.md), [ADR-005](docs/adr/005-oncall-agent-guardrails.md), `oncall/`; drill S1 on dev, [audit section 12](docs/progress-audit.md): [agent run](https://github.com/erickyegon/cohortlens/actions/runs/40894738766), [resolved run](https://github.com/erickyegon/cohortlens/actions/runs/40896403031), [incident #78](https://github.com/erickyegon/cohortlens/issues/78) |

What is not done, stated plainly: the demo video, drill scenario S2, and an agent that can act on prod (by design it cannot). The audit lists the rest.

## Quick start
```bash
docker compose up --build
```

That is the single start command; it needs only Docker. `make up` is an optional shortcut for the same thing if you have `make` installed.

- UI: http://localhost:8080 (nginx, proxies `/api`)
- API: http://localhost:8000/healthz, `/readyz`
- Grafana (local telemetry stack): http://localhost:3000
- Postgres: `127.0.0.1:15432` (database `cohortlens`)

First start loads the committed OMOP seed automatically; later starts reuse the `pgdata` volume. `bash scripts/smoke_compose.sh --clean` runs the stack smoke test from scratch (needs Docker; `make` is optional).
