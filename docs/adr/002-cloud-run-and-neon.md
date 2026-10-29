# ADR-002: Cloud Run and Neon over Render

Status: accepted · Date: 2026-10-28

## Context
CohortLens needs a live URL, a Postgres database that holds the OMOP seed, a deploy path with a human approval before production, and a rollback that takes seconds. The budget is a few dollars a month at most (a $5 budget alert is set), and the project is a demo on synthetic data.

## Decision
- **Cloud Run** runs the API and the web container, in `us-east5`, next to the database. Revisions are immutable and traffic can be moved between them, so a rollback is a traffic shift, not a rebuild (the rollback design is recorded in [ADR-004](004-rollback-by-traffic-shift.md)).
- **Neon** hosts Postgres 18 (AWS US East 2, project `cohortlens`) with two branches, `production` and `dev`. CI, the local Compose stack and the tests run Postgres 18 too, so the engine is tested on the version it runs on.
- **Render was the alternative and was rejected** for two reasons on its free tier, and Cloud Run won on a third:
  - Free web services **sleep after 15 minutes idle and take about a minute to wake**. A sleeping service would trigger false latency alerts and break the latency target.
  - Free Postgres **expires after 30 days**, so the database would be offline during peer review.
  - Cloud Run was chosen for its **built-in rollback by traffic shift between revisions** (see [ADR-004](004-rollback-by-traffic-shift.md)).
- **Deploys from GitHub Actions use workload identity federation**, so there are no long-lived key files.
- **One Google Cloud project holds both environments** (dev and prod services side by side).

## Accepted risk: dev and prod share one Google Cloud project
`roles/run.admin` is granted project-wide, so the dev deploy identity (`cohortlens-deploy-dev`, no approval gate) could change, re-route or delete the prod Cloud Run services, or change their IAM. The product owner reviewed this in the progress audit and **accepted it** for this demo.

Reasoning:
- The worst case is breaking or hijacking the prod *services*. It cannot read prod data: each runtime account reads only its own environment's two secrets, and the deploy accounts read none.
- The data is synthetic and prod is a demo, so an outage or a bad revision costs time, not privacy.
- Separate projects (the setup script is parameterised by `PROJECT_ID`) or per-service IAM conditions would remove the risk, at the price of a second project and billing link and a more complex setup for a one-person demo.

Mitigations:
- **Prod is protected by the approval gate.** Production deploys run from the GitHub `production` environment, which needs a human approval, and promote the **same image digest** that ran in dev. (The gate is built in E4; until it exists, nothing deploys to prod.)
- Rollback is a traffic shift to a previous revision, so a bad revision is undone without a rebuild.
- The on-call agent may roll back at most once per service per hour, and only from an action allowlist (stated in `.claude/agents/sre.md` and recorded in [ADR-005](005-oncall-agent-guardrails.md)).

Revisit this decision if the service ever holds real data or has users beyond the demo.

## Consequences
- One setup script, one project, one set of quotas and one budget alert.
- A mistake in the dev pipeline can affect prod services. The approval gate protects prod *deploys*, not the prod *services* from the dev identity.
- Postgres 18 is pinned in CI, so a Neon version change is a deliberate change.
