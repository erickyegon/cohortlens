# Runbook: CohortRunErrorRateHigh

Fires when failed/timed-out runs exceed 5% over 5 min (≥10 runs, held 2 min).

1. Split error ratio by revision.
2. Check top error events and 3 example traces.
3. Recent deploy and previous revision healthy → roll back.
4. Database unreachable on all revisions → escalate only.
5. Verify recovery for 10 minutes; write postmortem.
