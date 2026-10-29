# Runbook: CohortRunErrorRateHigh

Fires when failed/timed-out runs exceed 5% over 5 min (≥10 runs, held 2 min).

1. Split error ratio by revision.
2. Check top error events and 3 example traces.
3. Recent deploy and previous revision healthy → roll back.
4. Database unreachable on all revisions → escalate only.
5. Verify recovery for 10 minutes; write postmortem.

**Where to look:** the Grafana dashboard "CohortLens core journey" (folder CohortLens), dashboard panels "Error rate by revision" and "Logs: failures and timeouts"; a trace id from a failing log line opens the trace in Tempo. Roll back with `bash infra/gcp/rollback.sh` (see `docs/deployment.md`); the agent's guardrails allow one rollback per service per hour.
