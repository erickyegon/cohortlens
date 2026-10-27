# Process

How one story moves from issue to production, who does what, and where the evidence lives. Roles and handoffs are defined in [agents.md](agents.md); this document is the practical side.

## The loop

1. **Spec agent** creates the issue from `docs/spec.md` with Given/When/Then criteria and labels it `ready`; it appears on the [project board](https://github.com/users/erickyegon/projects/1).
2. **Test agent** opens branch `feat/<epic>-<slug>` or `feat/US-n-<slug>` and commits tests that encode the criteria.
3. **Builder agent** implements until `make test` passes and opens a PR that starts with `Agent: Builder` and contains `Closes #N`.
4. **Reviewer agent** checks the PR criterion by criterion. Maximum two rounds, then the human steps in (`needs-human`).
5. **Human** merges. CI deploys to dev automatically; the human approves promotion to prod.

## Conventions that make the trail verifiable

- Every PR description starts with an `Agent: <role>` line and says `Closes #N`; a docs-only PR with no issue says `No issue: <reason>` instead, and its commits name the PR or issue they follow up.
- Every commit carries an `Agent-Role:` trailer and a `Refs #N` / `Closes #N` line.
- **Merge with a merge commit, never squash or rebase-merge.** Squashing collapses a PR into one commit and drops the per-commit `Agent-Role:` trailers from `main`; they are part of the evidence trail.
- The human is the only one who merges and approves production deploys (AGENTS.md rule 5).
- **At most one level of stacking.** A PR may be stacked on one unmerged PR, never on a stack. Prefer a base of `main` whenever the PR does not need the other PR's code.
- **Base check before every handoff.** Before handing the human any PR to merge, run `gh pr view <n> --json baseRefName` and confirm the base is `main`; if it is not (for example because the PR it was stacked on has merged), retarget it with `gh pr edit <n> --base main` and confirm CI has run against the new base (re-run the checks if they predate the retarget, because a base change alone may not trigger them). Reason: PRs #40, #41 and #42 were once merged into their stacked base branches instead of `main`, so only #39 reached `main` and the work had to be recovered through a separate merge PR (#44). A stacked PR is merged only after its base is on `main`, base first, then retarget, then merge.
- **Pushing.** Push with `gh` as the credential helper (`git -c credential.helper= -c "credential.helper=!gh auth git-credential" push ...`); the default Windows credential manager can hang waiting for a prompt nobody sees.
- The Project board lists every issue and every PR, with the Status field set (`In review` while open, `Done` once merged).

## Why reviews are posted as comments

The project has one GitHub account (`erickyegon`) that acts as author, reviewer and merger. GitHub does not let an account approve or request changes on its own pull request, so the Reviewer agent's formal review is submitted as a **comment review** (`gh pr review --comment`) whose first line states the verdict (`APPROVE` or `REQUEST CHANGES`). The review lists each acceptance criterion by name with `MET` / `NOT MET` / `PARTIAL` and evidence, then the findings with severity. The human's merge is the actual approval. With a second account or a bot identity the same review would be submitted as a real approval, and nothing else in the process would change.

Each review is one comment that summarises both rounds; the fixes between rounds are separate commits on the PR branch, so the PR's commit list shows the round-trip.

## Worked example: issues #2, #3 and #4

Two pull requests ran the full loop. Both went through two Reviewer rounds before being handed to the human to merge.

### 1. Issue → tests → build: truth fixture ([#4](https://github.com/erickyegon/cohortlens/issues/4))

| Step | Evidence |
|---|---|
| Issue with Given/When/Then criteria | [#4 Truth fixture dataset](https://github.com/erickyegon/cohortlens/issues/4) |
| Branch | `feat/E1-truth-fixture` |
| Tests and fixture (Test agent) | [`003cee3`](https://github.com/erickyegon/cohortlens/commit/003cee3) — 20 patients, 14 tests |
| PR | [#28](https://github.com/erickyegon/cohortlens/pull/28) — `Agent: Test`, `Closes #4` |
| Reviewer round 1 | **Request changes**: a wrong day count in the doc (100 vs 101), no CI/`make test`, silent test skips, a vacuous assertion, missing boundary cases |
| Fix commit | [`57e0bdb`](https://github.com/erickyegon/cohortlens/commit/57e0bdb) — three new boundary patients, schema-drift test, `make test` runs pytest |
| Reviewer round 2 | **Approve**, with nits |
| Nit commit | [`83d5e7a`](https://github.com/erickyegon/cohortlens/commit/83d5e7a) |
| Formal review (both rounds) | [review on #28](https://github.com/erickyegon/cohortlens/pull/28#pullrequestreview-5657292545) |
| Merge (human, merge commit) | [`62ad012`](https://github.com/erickyegon/cohortlens/commit/62ad012) — the three branch commits and their `Agent-Role:` trailers are on `main` |

### 2. Issues → build → review: Compose stack and app schema ([#2](https://github.com/erickyegon/cohortlens/issues/2), [#3](https://github.com/erickyegon/cohortlens/issues/3))

| Step | Evidence |
|---|---|
| Issues | [#2 Compose stack with seeded Postgres](https://github.com/erickyegon/cohortlens/issues/2), [#3 Alembic app schema](https://github.com/erickyegon/cohortlens/issues/3) |
| Branch | `feat/E1-compose-stack` |
| Implementation (Builder agent) | [`b122588`](https://github.com/erickyegon/cohortlens/commit/b122588), [`4a91a29`](https://github.com/erickyegon/cohortlens/commit/4a91a29) |
| PR | [#29](https://github.com/erickyegon/cohortlens/pull/29) — `Agent: Builder`, `Closes #2, closes #3` |
| Reviewer round 1 | **Request changes**: no automated tests for #2/#3, PUBLIC `CONNECT`/`TEMP` on the database, passwords interpolated into SQL by a shell script, `lgtm` without a healthcheck, GitGuardian red |
| Fix commit | [`6ab3766`](https://github.com/erickyegon/cohortlens/commit/6ab3766) — 25 integration tests, hardened grants, quoted psql variables, healthchecks, non-root web image |
| Reviewer round 2 | **Approve** on code and tests; GitGuardian left to the human (documented local-only placeholder passwords) |
| Follow-up commit | [`fcbdc99`](https://github.com/erickyegon/cohortlens/commit/fcbdc99) |
| Formal review (both rounds) | [review on #29](https://github.com/erickyegon/cohortlens/pull/29#pullrequestreview-5657411791) |
| Merge (human, merge commit) | [`604a7d9`](https://github.com/erickyegon/cohortlens/commit/604a7d9) — merged after #28, as planned; GitGuardian incidents marked as false positives first |

### 3. Merge and deploy
- #28 merged as [`62ad012`](https://github.com/erickyegon/cohortlens/commit/62ad012), #29 as [`604a7d9`](https://github.com/erickyegon/cohortlens/commit/604a7d9), and this document's own PR #30 as [`2e0a893`](https://github.com/erickyegon/cohortlens/commit/2e0a893), all with merge commits; the issues closed automatically through `Closes #N`.
- Order mattered: #28 first, because #29 references files it adds (`data/etl/cdm_ddl.sql`, `api/requirements-dev.txt`).
- Note: #30 was merged before this table had its merge links, which is why this follow-up PR exists.
- **Still to come:** the dev deploy and the prod approval step. They arrive with the delivery epic (E4); CI (#16) is the next piece and will make the checks below required on `main`.

## Human decisions recorded along the way
- Merge exception: PRs #35 and #36 were merged by the agent (2026-10-27) at the product owner's explicit instruction; the rule that only the human merges (AGENTS.md rule 5) is otherwise unchanged. From #8 onward the human merges unless the product owner says otherwise in that turn.
- Dataset: Synthea generation instead of the Eunomia/SynPUF candidates, and the reference cohort definition (hypertension any time before index, age ≥ 50): [ADR-001](adr/001-omop-subset-over-custom-schema.md).
- GitGuardian: the two flagged values are local-only placeholder passwords already in the PR history; the two flagged values were marked as false positives in the GitGuardian dashboard by the account owner. Real database credentials live in Google Secret Manager (see [deployment.md](deployment.md)) and the placeholder values must never be used in any real environment.
