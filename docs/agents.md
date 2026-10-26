# Agent team and handoffs

Five agent roles do the work; one human owns product decisions, merges and production approval. Each role is a subagent definition in `.claude/agents/`. Issue labels carry work between roles.

| Role | Responsibilities | Reads | Produces | Not allowed to |
|---|---|---|---|---|
| Spec | Turn `spec.md` into issues with Given/When/Then criteria; keep the board current | `docs/spec.md`, the board | Issues labelled `ready` | Write application code |
| Test | Write failing tests from the acceptance criteria before the build | Issue, `docs/testing.md` | Test commits on the feature branch | Change application code to make tests pass |
| Builder | Implement the issue until tests pass; open the PR | Issue, `docs/architecture.md`, tests | PR with `Closes #N`, label `needs-review` | Merge; edit CI/deploy workflows |
| Reviewer | Check the PR against each criterion, architecture rules and telemetry privacy rules | PR diff, issue, docs | Review: approve or request changes | Push commits to the PR |
| SRE | Own workflows, runbooks, dashboards and alert rules; at runtime is the on-call agent | `docs/observability.md`, `docs/runbooks/` | Infra PRs, incident issues, rollbacks | Merge its own PRs; change its own guardrails |
| Human | Prioritise, merge, approve prod deploys | Everything | Merge/approval decisions | — |

## Labels
`ready` → `tests-written` → `needs-review` → `changes-requested` | `approved` → merged. `needs-human` marks an escalation. Two review rounds maximum before the human steps in.

## Story flow
1. Spec agent creates the issue, labels it `ready`.
2. Test agent opens `feat/US-n-…`, commits failing tests, labels `tests-written`.
3. Builder implements until `make test` passes, opens PR, labels `needs-review`.
4. Reviewer reviews each acceptance criterion by name.
5. Human merges; CI deploys to dev.
6. Human approves promotion to prod.

## Evidence trail
PR descriptions start with `Agent: <role>`; commits carry `Agent-Role:` trailers; `docs/process.md` holds one real walkthrough.
