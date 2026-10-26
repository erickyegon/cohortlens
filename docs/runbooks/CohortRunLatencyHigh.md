# Runbook: CohortRunLatencyHigh

Fires when p95 run duration exceeds 3 s over 10 min (≥20 runs, held 5 min).

1. Compare p95 by revision.
2. Find which child span dominates (`cohort.execute`?).
3. Config/code change on new revision → roll back.
4. No deploy, time inside `cohort.execute` → check indexes and recent migrations; open fix PR and escalate.
