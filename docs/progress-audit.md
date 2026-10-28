# Progress audit

Audit date: 2026-10-28 (UTC). Audited commit: `a567dbe` (`main`, after PR #53). Read-only: no code was changed.

**Changes since the audited commit.** PR [#57](https://github.com/erickyegon/cohortlens/pull/57) (the hardened Google Cloud setup script) was merged at 01:11 UTC (`cda9408`, CI green), so `main` now carries the hardened script and its 75 tests. **The Reviewer's round-2 review of #57 was posted only after the merge** (verdict APPROVE, none of the findings blocking; at audit time #57 had no reviews and #56 had only the round-1 REQUEST CHANGES), so the hardened script reached `main` before its round-2 approval. Five low-severity findings from that review (F10 to F14, section 5 item 2) are still open. Everything below describes the audited commit unless it says otherwise.
Compared against: the original project outline (the `.docx` is not in the repo, so it was used from outside it, together with the phase summary in the audit request), the course rubric (12 criteria, 24 points, 10 to pass), and what actually exists in the repository, history, pull requests, issues, the Project board, Actions runs and branch protection.

**How to read the evidence.** Every claim below is marked **verified** (I ran it or read the GitHub API in this audit), **link** (a checked artifact), or **unverified** (stated in a document but not backed by something I ran). Section 9 lists exactly what was run and what it printed.

---

## 1. Summary

| | Points (of 24) | Pass mark 10 |
|---|---|---|
| **Estimated score now** | **15** | **Pass, with a margin of 5** |
| If delivery (E4) is finished | 19 | |
| If observability and alerting (E5) are also finished | 22 | |
| If the on-call agent (E6) is also finished | 24 (ceiling) | |

**Likely final score if the remaining plan is finished: 21 to 24, most likely about 22.** The remaining 9 points are all in rows 8 to 12, and every one of them depends on things that do not exist yet: a deployed system, telemetry, an alert, an agent. The upper end needs the cloud deployment to be up through peer review (section 6).

What is solid: the engine, API, UI, tests, containers and CI are real and run. From a fresh clone today, `docker compose up --build` plus the smoke script passed, the reference cohort returned **920 → 552 → 552 → 551 → 489 → 481 (final count 481)** through the public proxy, 453 backend tests and 99 frontend tests passed, and lint, type-check and build were clean.

What is not done: nothing is deployed, there is no continuous delivery, and there is no telemetry, alerting or on-call agent (rows 8, 10, 11, 12 are **0**, row 9 is **1 of 3**). The README is stale and says "phase 1", the live-URL line says "TBD", and three of the five architecture decision records are empty skeletons.

Two things to know first: at the audited commit `main` carried the **weaker earlier version of the Google Cloud setup script** (PR #56 was merged before its review fixes were pushed); PR #57 has since brought in the hardened version; it was merged **before** its round-2 review (approved afterwards). And `verify-seed` was **flaky in my runs** (section 6).

---

## 2. Rubric scorecard

Board: <https://github.com/users/erickyegon/projects/1> (verified public). Repo: <https://github.com/erickyegon/cohortlens> (verified public).

| # | Criterion | Now | Max | Evidence | Gap to full marks |
|---|---|---|---|---|---|
| 1 | Problem description | **2** | 2 | [README.md](../README.md): Problem, Users, Core journey, Expected behaviour, Out of scope (verified present). | The README still says "Status: phase 1 (spec and scaffolding)" and "Live URLs / demo video: TBD", and has no scorecard map. A strict reviewer could dock this; it is a 30-minute fix. |
| 2 | Product specification | **2** | 2 | [docs/spec.md](spec.md) (stories US-1..12, FR-1..17, NFR-1..5, Given/When/Then). Public board (verified `public: true`, 56 items). 29 of 31 issues carry Given/When/Then criteria (verified). All 26 PRs start with an `Agent:` line and reference an issue (verified). | Evidence the agents *used* the tracker is thinner than planned: the labels show epic, agent and priority but **never a handoff** (no `tests-written` or `needs-review` labels were used), branch names are `feat/E2-...` not `feat/US-n-...`, and the board statuses are stale (section 7). The spec has drifted from decisions made since (section 4). Issues #52 and #54 lack Given/When/Then. |
| 3 | Agent team | **2** | 2 | Five definitions in [.claude/agents/](../.claude/agents) (spec, test, builder, reviewer, sre); [docs/agents.md](agents.md) (26 lines) and [docs/process.md](process.md) (loop, conventions, worked example). Evidence: 25 `Agent-Role:` trailers by role (builder 36, test 28, sre 8, spec 5, verified in history), and a Reviewer-agent review on 21 of 26 PRs (verified). | The Reviewer is run as a general-purpose subagent given `reviewer.md`, not as the registered `reviewer` agent type (honest note for the walkthrough). `docs/agents.md` is thin. The worked example covers only issues #2 to #4 and says the deploy step is "still to come". |
| 4 | Context management | **2** | 2 | [AGENTS.md](../AGENTS.md) (42 lines) links the modular docs; every linked file exists (verified). | Several linked docs are stubs: `docs/observability.md` is 11 lines, ADRs 002 to 005 are 9-line empty skeletons, the two runbooks are 8 to 9 lines. A reviewer who follows the links will find them. |
| 5 | Application | **2** | 2 | Verified in this audit: the stack comes up, the UI is served, the API works through the nginx proxy, a cohort was created, run (final 481), listed with its last run, and read back from history; data persists across a database restart (smoke). | The browser UI was **not re-driven in this audit** (previous Reviewers drove it live on PRs #51 and #53; link, not re-verified here). |
| 6 | Testing | **2** | 2 | Verified: backend **453 passed, 10 skipped**; frontend **99 passed**; `ruff`, ESLint, `tsc` and the web build clean; CI runs the same plus the seed tests. | "One command" is `make test`, and `make` is not installed on this Windows machine, so I ran its two commands directly. The 10 seed tests are skipped by `make test` and run by `verify-seed` (CI does both). **`verify-seed` was flaky: 3 of 7 runs failed** (section 6). The documented 80% coverage gate is **not enforced** anywhere (unverified claim). |
| 7 | Containerization | **2** | 2 | Verified: `scripts/smoke_compose.sh --clean` from a fresh clone built and started db, migrate, api, web and the local Grafana stack, seeded the database, checked role privileges and the proxy, and a saved cohort survived a database restart: **SMOKE OK**. | None for the rubric. |
| 8 | Deployment | **0** | 2 | None. No public URL, no Cloud Run service, no Artifact Registry repository (verified: only `billingbudgets.googleapis.com` is enabled on the project). Prepared: Google Cloud project with billing and a $5 budget (verified), Neon project with `production` and `dev` branches, both empty (verified), setup script `infra/gcp/setup.sh` (never run). | A live URL (or a recorded demo of the deployment). |
| 9 | CI/CD | **1** | 3 | CI on every PR: lint, test, build, smoke are **required checks** on `main`, enforced for admins (verified through the branch-protection API). 48 PR runs succeeded, 2 failed and were fixed before merge (verified). [ci.yml](../.github/workflows/ci.yml). | Auto-deploy to dev (+1), prod behind a manual approval gate promoting the same image (+1). There are no deploy workflows, no GitHub environments and no Actions variables or secrets (verified). |
| 10 | Observability | **0** | 2 | None. `api/app/telemetry/` is empty; no OpenTelemetry anywhere; logs are plain `logging` lines on failures only, not structured JSON; no metrics; the trace id is a random hex string, not a trace (verified by reading the code). The local Grafana container in Compose is running but nothing sends to it. | Traces, metrics and JSON logs sharing a trace id, a scrubber and leak test, a viewable Grafana dashboard. |
| 11 | Alerting on user impact | **0** | 1 | Only two runbook stubs exist. No alert rules. | One alert rule, as code. |
| 12 | On-call agent | **0** | 2 | None. `oncall/`, `chaos/`, `loadgen/` are empty (verified). | The whole of E6. |
| | **Total** | **15** | **24** | | |

---

## 3. Outline, phase by phase

Status: **done**, **partial**, **not started**. Links are to merged PRs and issues.

### Phase 1: spec and scaffolding: **done** (with stub docs)
| Item | Status | Evidence |
|---|---|---|
| Spec | done | [docs/spec.md](spec.md); issue [#1](https://github.com/erickyegon/cohortlens/issues/1) |
| Public board with about 24 issues | done | 31 issues, 17 closed (verified); board public. Handoff labels were not used (section 2, row 2). |
| AGENTS.md and docs skeleton | done | [AGENTS.md](../AGENTS.md); `docs/*.md`. ADRs 002 to 005 and several docs are still skeletons. |
| Agent definitions | done | `.claude/agents/` (5 files) |

### Phase 2: data and engine: **done**
| Item | Status | PR / issue |
|---|---|---|
| Seed generated and committed | done | ADR-001; 5,724 persons (outline target 5,000) |
| Truth fixture | done | [#28](https://github.com/erickyegon/cohortlens/pull/28), issue #4 |
| Definition model and validation | done | [#34](https://github.com/erickyegon/cohortlens/pull/34), issue #5 |
| SQL builder, rules with windows and descendants | done | [#35](https://github.com/erickyegon/cohortlens/pull/35), [#36](https://github.com/erickyegon/cohortlens/pull/36), issues #6 #7 |
| Attrition counts in one statement | done | [#39](https://github.com/erickyegon/cohortlens/pull/39), issue #8 |
| Suppression | done | [#40](https://github.com/erickyegon/cohortlens/pull/40) (via recovery PR [#44](https://github.com/erickyegon/cohortlens/pull/44)), issue #9 |
| Baseline table | done | [#41](https://github.com/erickyegon/cohortlens/pull/41), [#45](https://github.com/erickyegon/cohortlens/pull/45), issue #10 |
| Performance on broad concepts | done | [#42](https://github.com/erickyegon/cohortlens/pull/42), [#43](https://github.com/erickyegon/cohortlens/pull/43), issue #37 |
| Unit and integration tests | done | 453 backend tests (verified) |

### Phase 3: API, UI, Compose: **done**
| Item | Status | PR / issue |
|---|---|---|
| Compose stack, `make up` end to end | done (verified) | [#29](https://github.com/erickyegon/cohortlens/pull/29), issue #2 |
| Cohort CRUD | done | [#46](https://github.com/erickyegon/cohortlens/pull/46), issue #11 |
| Run endpoint and history | done | [#49](https://github.com/erickyegon/cohortlens/pull/49), issue #12 |
| Concept search | done | [#47](https://github.com/erickyegon/cohortlens/pull/47), issue #13 |
| Builder and list screens | done | [#51](https://github.com/erickyegon/cohortlens/pull/51), issue #14 |
| Results screen | done | [#53](https://github.com/erickyegon/cohortlens/pull/53), issue #15 |
| Vitest tests | done | [#50](https://github.com/erickyegon/cohortlens/pull/50) |
| Stretch: Playwright e2e | **not started** | issue [#32](https://github.com/erickyegon/cohortlens/issues/32); `make e2e` is a TODO that exits 1 |

### Phase 4: delivery: **partial**
| Item | Status | Evidence |
|---|---|---|
| CI on every PR | done | [#33](https://github.com/erickyegon/cohortlens/pull/33), issue #16; required checks (verified) |
| Neon project, dev and main branches | **partial** | Project `cohortlens` (Postgres 18, AWS US East 2) with branches `production` and `dev`, **both empty** (verified). No roles, schema or seed loaded. `make seed-cloud` is a TODO. |
| Google Cloud project and billing | **partial** | Project exists, billing linked, $5 budget created (verified). |
| Artifact Registry | **not started** | Only in the unrun script |
| Workload Identity Federation | **not started** | Only in the unrun script. At the audited commit `main` had the earlier, weaker version; the hardened one landed in [#57](https://github.com/erickyegon/cohortlens/pull/57) after it (never run against a real project). |
| Cloud Run services | not started | |
| Dev auto-deploy on merge | not started | issue [#17](https://github.com/erickyegon/cohortlens/issues/17) |
| Prod approval gate, same image promoted | not started | issue [#18](https://github.com/erickyegon/cohortlens/issues/18); no GitHub `production` environment exists (verified) |
| Rollback by traffic shift | not started | issue [#19](https://github.com/erickyegon/cohortlens/issues/19) |

### Phase 5: observability and alerts: **not started**
| Item | Status | Issue |
|---|---|---|
| OpenTelemetry traces, metrics, JSON logs with a shared trace id | not started | [#20](https://github.com/erickyegon/cohortlens/issues/20) |
| Allowlist scrubber and leak test | not started | [#21](https://github.com/erickyegon/cohortlens/issues/21) |
| Grafana Cloud export | not started | [#22](https://github.com/erickyegon/cohortlens/issues/22) |
| Dashboard and alert rules as code (`CohortRunErrorRateHigh`, `CohortRunLatencyHigh`) | not started | [#23](https://github.com/erickyegon/cohortlens/issues/23); runbook stubs only |
| k6 load generator | not started | `loadgen/` empty; `make traffic` points at a missing file. The realistic-definitions rule is already documented in `docs/observability.md`. |
| Stretch: burn-rate alert | not started | |

### Phase 6: on-call agent: **not started**
| Item | Status | Issue |
|---|---|---|
| Grafana webhook to `repository_dispatch` | not started | [#24](https://github.com/erickyegon/cohortlens/issues/24) |
| Agent tools (read and guarded write) | not started | [#25](https://github.com/erickyegon/cohortlens/issues/25) |
| Playbook, guardrails, replay tests | not started | [#26](https://github.com/erickyegon/cohortlens/issues/26) |
| Chaos scenarios S1, S2 (S3 stretch) and demo video | not started | [#27](https://github.com/erickyegon/cohortlens/issues/27); `make chaos` is a TODO |
| Stretch: free-text cohort entry | not started | |

---

## 4. Deviations from the plan

Documented means a link a reader can follow today.

| # | Deviation | Why | Documented? |
|---|---|---|---|
| 1 | **Dataset:** Synthea generated with our own ETL (seed 42, 5,724 persons), not reuse of the earlier project's pipeline or Eunomia | Candidates were measured; none gave a hypertension and lisinopril cohort of useful size | Yes: [ADR-001](adr/001-omop-subset-over-custom-schema.md) (accepted) |
| 2 | **Reference cohort:** new users of lisinopril, hypertension at any time before the index, age 50 or older; the hypertension-anchored version is kept as the documented infeasible counter-example | Synthea records chronic conditions once at onset, so short look-backs remove most patients | Yes: ADR-001, `docs/spec.md` §7, `docs/cohort-engine.md` |
| 3 | **Attrition step order** differs from the SQL oracle (index, demographics, observation, rules) | The chain order of the CTEs | Yes: `docs/cohort-engine.md` |
| 4 | **Validation cap:** with every exposure as an index event, at most 3 rules and `min_occurrences` 1 | Those shapes cannot finish inside the 10 s statement timeout; owner decision | Yes: `docs/cohort-engine.md` and issue [#37](https://github.com/erickyegon/cohortlens/issues/37). **No ADR.** |
| 5 | **Occurrence rules stop counting after k records** (`LIMIT k`); `enable_nestloop = off` measured and **not** adopted | Worst realistic case 6 to 9 s down to 0.7 s | Yes: `docs/cohort-engine.md`, comment on #37 |
| 6 | **Comorbidities under 11 are left out entirely**, with an always-present note, instead of shown as `<11` | A masked row still named the condition; owner decision | Yes: `docs/cohort-engine.md`. **The spec is stale:** US-5, FR-9 and FR-10 still describe the earlier behaviour. |
| 7 | **The baseline is returned with a run but not stored** | The schema tests pin migration 0001 and the `cohort_run` columns | Yes: `docs/architecture.md`. Spec FR-8 is silent. |
| 8 | **The 10 s statement timeout is per statement, not per run** (a run issues three statements) | FR-14 says "queries are cancelled after 10 s" | Yes: `docs/architecture.md` |
| 9 | **Postgres 18, not 16** | The Neon project was created on 18; CI, tests and Compose were moved to match (424 then 453 tests pass) | Yes: `docs/deployment.md`. **ADR-002 is empty.** |
| 10 | **Neon branches `production` and `dev`, region US East 2 (Ohio), Cloud Run region `us-east5`** (outline: `main` and `dev`) | Console defaults and latency | Only as script defaults. **Not in any doc or ADR.** |
| 11 | **Google Cloud set up by a bash script**, not Terraform | Fewer moving parts for a one-time setup (outline allowed "Terraform or scripts") | Partly: `docs/deployment.md`. Dashboards and alert rules are still planned as Terraform (not started). |
| 12 | **Stacked PRs and a recovery PR:** #40, #41 and #42 were merged into their stacked base branches, so only #39 reached `main`; PR #44 brought them in. The later rule is "at most one level of stacking; check the base is `main` before handoff" | A merge-order mistake | Yes: [docs/process.md](process.md) (#48) |
| 13 | **#35 and #36 were merged by the agent** at the owner's explicit instruction | Owner request | Yes: `docs/process.md` |
| 14 | **#56 was merged before its review fixes were pushed**; the hardened script followed in #57, which was itself **merged before its round-2 Reviewer review**, approved afterwards with five non-blocking findings | Both merges happened while a review was still in progress | This audit and the PR #57 body |
| 15 | **Frontend uses plain React and CSS bars**, not TanStack Query and Recharts | Minimal, tested, working | Partly: `docs/architecture.md` says no router or UI library but does not name the planned libraries |
| 16 | **The API serves routes without `/api`**; nginx and Vite strip the prefix | Keeps `/healthz` and `/readyz` at the root | Yes: `docs/architecture.md` |
| 17 | **Single-account "comment" reviews** by a Reviewer subagent; PR #51 was drafted before its tests (disclosed in the PR) | One GitHub account cannot approve its own PR | Yes: `docs/process.md`; the PR #51 body |
| 18 | **Trace id is a random hex string**, to be replaced by the OpenTelemetry trace id | OTel not built yet | A code comment only |

---

## 5. Remaining work, in priority order of points per hour

Estimates are the author's, including review cycles, for one person-equivalent of effort. "Needs you" means an account, billing, a browser approval, or a recording.

| Order | Item | Est. hours | Points it protects or gains | Depends on | Needs you |
|---|---|---|---|---|---|
| 1 | **Fix the README, empty ADRs, runbooks, spec drift, board** (section 7) | 3 | Protects rows 1, 2, 4 (up to 6 points at risk from presentation) | nothing | review |
| 2 | **Fix the open findings from the round-2 review of the setup script** (posted after #57 merged, APPROVE): F10 the branch-policy check must select `type == "branch"` (a tag policy named `main` passes today) and decide on `can_admins_bypass`; F11 test the jq expressions and verify an existing provider's issuer and mapping, not only its condition; F12 test the real gcloud "may not exist" and "not found or permission denied" messages; F13 compare the full condition in the plan test; F14 say "does not exist or cannot be read" | 1.5 | Safety of everything below | | review |
| 3 | **Dev deployment**: GitHub `production` environment, run `setup.sh --apply`, Neon roles and seed (`make seed-cloud`), Cloud Run services, `deploy-dev.yml`, post-deploy smoke ([#17](https://github.com/erickyegon/cohortlens/issues/17)) | 9 | **+2** (row 8) **+1** (row 9) | #2, Neon login (done) | **GitHub environment (browser), approve `--apply`**, set GitHub variables |
| 4 | **Prod gate and rollback**: promote the same image digest behind the `production` environment, rollback by traffic shift ([#18](https://github.com/erickyegon/cohortlens/issues/18), [#19](https://github.com/erickyegon/cohortlens/issues/19)) | 4 | **+1** (row 9, to 3) | #3 | you are the required reviewer |
| 5 | **Observability**: OTel traces, metrics, JSON logs with a shared trace id ([#20](https://github.com/erickyegon/cohortlens/issues/20)), scrubber and leak test ([#21](https://github.com/erickyegon/cohortlens/issues/21)), Grafana Cloud export ([#22](https://github.com/erickyegon/cohortlens/issues/22)), dashboard and the two alert rules as code ([#23](https://github.com/erickyegon/cohortlens/issues/23)), k6 load generator | 13 | **+2** (row 10) **+1** (row 11) | #3 deployed (for cloud export); local Grafana works earlier | **Grafana Cloud free account and token (browser)** |
| 6 | **On-call agent**: webhook to dispatch, tools, playbook, guardrails, replay tests, chaos S1 and S2, demo video ([#24](https://github.com/erickyegon/cohortlens/issues/24) to [#27](https://github.com/erickyegon/cohortlens/issues/27)) | 13 | **+2** (row 12) | #3, #5 | **Anthropic API key as a GitHub secret; recording the demo video** |
| 7 | Reliability: make `verify-seed` wait for real readiness, and stop timing assertions flaking | 1.5 | Protects row 6 | | |
| 8 | Baseline complementary suppression ([#54](https://github.com/erickyegon/cohortlens/issues/54)), `last_run` index ([#52](https://github.com/erickyegon/cohortlens/issues/52)) | 2 | Quality, not points | owner decision on #54 | decision |
| 9 | Stretch: Playwright e2e ([#32](https://github.com/erickyegon/cohortlens/issues/32)) | 3 | none | | |

Points per hour: item 3 is about 0.33, item 4 about 0.25, item 5 about 0.23, item 6 about 0.15. Item 1 is first because it is cheap and protects points already counted. Total remaining for all nine points: roughly 39 hours, against 24 hours planned for the original must-have scope.

---

## 6. Risks to submission

| Risk | Why it matters | Mitigation |
|---|---|---|
| **Nothing is deployed yet, and all of E4 to E6 is untested against the real cloud** | 9 of 24 points; the setup script has never run against a real project (only a fake `gcloud`) | Run `--apply` early, on the plan the owner has read; budget time for first-contact failures |
| **Deployment must stay up through peer review** | Neon compute sleeps; Cloud Run bills; the $5 budget only alerts, it does not stop spending | Keep `max-instances` low, load only on demand, link the recorded demo video as the backup the rubric allows |
| **`verify-seed` is flaky** | **Verified:** across 7 runs from the fresh clone, 3 failed (one "database system is shutting down" startup race; two with 1 and 2 failing tests whose names I did not capture) and the last 4 passed. The script waits `pg_isready` then sleeps 2 s, but the official image starts a temporary server, stops it and restarts. CI runs it too. | Wait for a real query to succeed; find and relax timing assertions that fail under load |
| **README out of date and no scorecard map** | Peer reviewers start there | Item 1 |
| **Empty or stub docs that AGENTS.md links to** | Reviewers follow links | Fill ADRs 002 to 005 and the runbooks as the work lands |
| **Doc claims that nothing enforces** | The "80% coverage gate" and "telemetry leak test" appear in `docs/testing.md`; neither exists (unverified claims) | Implement or remove |
| **The hardened setup script is on `main`, merged before its round-2 review (approved afterwards); five low-severity findings are open and it has never run for real** | The script decides who can deploy to production | Fix F10 to F14 before the first `--apply`; run the plan first; create the protected GitHub `production` environment first (the script refuses without it, and the Reviewer confirmed that against the real GitHub API: the environment does not exist yet and returns 404) |
| **Board is incomplete or stale** | Row 2 evidence | Section 7 |
| **Dev can affect prod** | One shared Google Cloud project; the dev deploy account (no approval gate) could change or delete prod Cloud Run services (documented in `docs/deployment.md`) | Owner decision: accept, separate project, or conditions |
| **Reviews are single-account comments** | Reviewers may ask where approval is | Explained in `docs/process.md`; the merge is the approval |
| **A credential was exposed outside the repo** | During setup, the Neon owner password was printed in a session transcript. **It was rotated on 2026-10-27.** A scan of the working tree and full history for credential patterns found nothing (verified). | None needed; noted for completeness |
| **Neon agent plugins installed in other tools** | `neon init` installed them at user level for several coding agents (an unrequested side effect) | Owner may remove them |
| **Peer reviews not started** | Required for submission (yours to do) | Schedule |

---

## 7. Board and issue hygiene

All verified from the GitHub API at audit time.

| Finding | Detail | Fix |
|---|---|---|
| Closed issue not Done | Issue **#15** is closed (by PR #53) but its card says "In progress" | Set to Done |
| Merged PRs shown "In review" | PRs **#53**, **#55**, **#56** are merged but cards read "In review" at audit time, although they had been set to Done earlier (cause unverified; possibly a board workflow) | Set to Done; check for a project automation |
| Not on the board | Issue **#54** | Add (Backlog) |
| Open PR at audit time | #57 was open and "In review" (correct then); it has since merged, so its card needs to move to Done | Set to Done |
| Open issues | 14 open: #17 to #27 (Ready), #32 and #52 (Backlog), #54 (not on board) | none beyond the above |
| Handoff labels unused | No `tests-written`, `needs-review` or `resolved-by-agent` labels were ever applied | Either use them or drop the claim from `docs/agents.md` and the walkthrough |
| Leftover branches | Remote: only `main` and `feat/E4-gcp-setup` (needed for #57) | none |
| Stale docs | README status line and live-URL line; ADRs 002 to 005 empty; runbooks stubs; `docs/spec.md` US-5, FR-9, FR-10 (comorbidity handling), FR-8 (baseline not stored); `docs/testing.md` coverage gate and leak-test claims; `docs/process.md` walkthrough stops before deployment; Makefile `e2e`, `chaos`, `seed-cloud` are TODOs that exit 1 | Item 1 and as features land |
| Pull requests without a Reviewer review | #30, #31, #38, #44 (docs or recovery) and **#57** (a code and security change: merged during its round-2 review, which was posted afterwards as APPROVE) | Acceptable for docs; #57's follow-up findings are in section 5, item 2 |

---

## 8. Submission checklist status

| Item | Status |
|---|---|
| README with problem, journey, quick start | **done** (problem, users, journey, expected behaviour, quick start verified) |
| README live URLs, demo video, scorecard map | **not done** ("TBD", no map) |
| Fresh-clone run | **done and verified today** (smoke OK; reference cohort 481) |
| Public board | **done** (public, 56 items); hygiene fixes pending |
| Process walkthrough | **partial** (`docs/process.md`, issues #2 to #4; no deploy step) |
| Live URLs (dev and prod) | **not done** |
| Dashboard screenshots | **not done** |
| Leak test | **not done** |
| Alert rules as code | **not done** |
| Incident issue closed by the agent | **not done** |
| Demo video | **not done** |
| Secrets clean | **done** (scan of tree and history found none; local-only default passwords in `compose.yaml` are documented placeholders; one exposed Neon credential was rotated) |
| Three peer reviews | **not started** (yours) |

---

## 9. What was run in this audit

From a **fresh clone** in a temporary folder (`main` at `a567dbe`):

| Check | Result |
|---|---|
| `bash scripts/smoke_compose.sh --clean` | **SMOKE OK**: services, seed (5,724 persons), role privileges, proxy, saved cohort survives a database restart |
| Stack up: `docker compose ps` | api, db, lgtm, web all running and healthy |
| Example cohort through the public proxy (`http://127.0.0.1:8080/api`): create, run, list, history, concept search | Created (201, version 1); run `success` in 1.7 s wall (1.6 s database, cold); steps **920, 552, 552, 551, 489, 481**; **final count 481**; baseline top conditions Essential hypertension 481, Stress 303, Prediabetes 301 with the suppression note; list shows `last_run` 481; history has the run with a stored trace id and `service_version`; concept search for "hypertens" returns 5 |
| `X-Trace-Id` through the proxy, including a 404 error body | Present on every response; the 404 body repeats the id |
| `pytest api/tests` (the first line of `make test`) | **453 passed, 10 skipped** (the seed tests), 7 min 22 s |
| `npm ci`, `npm test`, `npm run lint`, `tsc --noEmit`, `npm run build` | **99 passed** in 7 files; lint, type-check and build clean |
| `ruff check .` | clean |
| `bash data/etl/verify_seed.sh` (loads the seed into Postgres 18 and runs the seed tests) | Over 7 runs: **3 failed, 4 passed** (the last four consecutive runs passed, 10 passed each in 7 to 10 s) |
| `make test` as written | **Could not run**: `make` is not installed on this Windows machine; its two commands were run directly |
| Branch protection API | `lint`, `test`, `build`, `smoke` required; admins enforced; no required PR reviews |
| Actions runs (last 100) | 67: pull requests 48 success and 2 failure (fixed before merge), pushes 16 success and 1 cancelled (a superseded run) |
| Board (project 1) | public; 56 items; 38 Done, 11 Ready, 4 In review, 2 Backlog, 1 In progress; mismatches in section 7 |
| Credential scan of tree and history | none found |
| `gcloud services list --enabled` | only `billingbudgets.googleapis.com` among the relevant APIs |
| `neon branches list` | `production` (default) and `dev`, both ready |

**Unverified in this audit:** interaction with the browser UI (driven by earlier Reviewers on #51 and #53, not re-run here); the seed database size against the 250 MB budget; the Neon and Google Cloud resources beyond listing them; any Grafana content; the documented coverage gate; the cause of the stale board statuses.
