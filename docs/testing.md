# Testing

- `make test`: pytest unit + integration (Testcontainers, Postgres 16), telemetry leak test, Vitest, agent replay tests.
- `make e2e`: Playwright against Compose (separate CI job).
- Quality gates: 80% line coverage on `api/app/cohort_engine/`; Ruff + ESLint clean; no real Anthropic API calls.
- Truth fixture: ~20 hand-built patients with known expected counts for every criterion type (`api/tests/fixtures/truth_fixture.sql`).
