# ADR-004: Rollback by traffic shift

Status: accepted · Date: 2026-10-29 (written after the decision, from the code and the real rollbacks)

## Context
A bad release must be undone in seconds, by a person or by the on-call agent, without a build. Cloud Run revisions are immutable and a service can send its traffic to any existing revision, which is why Cloud Run was chosen over Render (ADR-002).

## Decision
A rollback is **`update-traffic --to-revisions=<target>=100`** on one service, run by `infra/gcp/rollback.sh` through the **Rollback** workflow (by hand) or the on-call agent. Nothing is built, deployed or deleted and no IAM changes. In order, the script:
1. refuses unless the service has **exactly one revision serving all traffic**;
2. checks the **target** before anything moves: it is tagged and smoke tested on its own URL, then the tag is removed (an older revision may be one that failed its own checks);
3. moves the traffic;
4. runs the smoke test on the service URL (the reference cohort through the web proxy); a failure is reported, not hidden;
5. comments on the incident issue: from revision, to revision, who started it, a link to the run, and whether the smoke test passed.

Rollbacks have their own concurrency group, so a queued deploy can never replace a waiting rollback. **A prod rollback runs in the protected `production` environment and waits for approval**, like a promotion; the dev deploy account cannot reach prod.

## Alternatives not chosen
- **Redeploy the previous image.** Slower (a new revision, a new smoke cycle, possibly a migration job), and it does not return to the exact revision that was healthy.
- **Revert the commit and let the pipeline deploy.** The right fix in the end, but minutes, not seconds; the agent's revert-PR tool is not built.
- **A load balancer with blue/green pools.** More infrastructure than a demo needs.

## Consequences
- **A rollback pins the traffic to a named revision.** The next dev deploy or prod promotion puts the new revision back in charge (`update-traffic --to-latest`); to go forward sooner, run Rollback again with the newer revision. The pinning also matters for anything that deploys a new revision afterwards: the S1 drill's second run got 0% traffic for its new revision until the script routed to it explicitly (PR #77).
- **Code and schema must stay compatible across one release** (expand then contract), because a rolled-back revision meets the newer schema.
- **The target must be healthy, and the script cannot know that.** A person chooses it; the agent may only pick a revision whose version has at least 50 successful runs in the last 7 days (ADR-005).
- **Evidence:** a real dev rollback ([run 40727516793](https://github.com/erickyegon/cohortlens/actions/runs/40727516793)), and the agent's rollback in drill S1 ([run 40894738766](https://github.com/erickyegon/cohortlens/actions/runs/40894738766)). No prod rollback has been run.
- **Cosmetic:** the comment's "started by" shows the account of the token that started the workflow, which for an alert-driven run is the owner's (audit deviation 29).
