# Cohort engine

Definition model (Pydantic) → validation → SQLAlchemy Core compiler → CTE chain (index_events → demographics → observation_window → one CTE per rule → final) → per-step counts → suppression → baseline.

## Definition model (`api/app/cohort_engine/models.py`)

A cohort definition is typed data validated before anything else runs. The module imports no database driver, so validating a definition can never execute SQL (a test enforces this). The reference cohort in `api/tests/fixtures/reference_cohort.json` is the spec §7 definition expressed in this model.

| Part | Fields |
|---|---|
| `name` | 1–200 printable characters (NFC-normalised; control, zero-width and bidi characters are rejected) |
| `index_event` | `domain` (`condition` or `drug`), `concept_ids` (1–1000 positive ints up to 2,147,483,647, de-duplicated), `include_descendants` (default true), `first_occurrence_only` (default true), optional `date_range` (`start`/`end` as `YYYY-MM-DD` strings between 1900 and 2100, either may be open, end not before start) |
| `demographics` | `age_min`, `age_max` (0–130, optional), `sex` (`F`, `M`, de-duplicated; default both) |
| `observation` | `prior_days`, `post_days` (0–36,500, default 0) |
| `rules` (0–10, ordered) | `label` (1–100 printable characters, unique ignoring case), `type` (`include`/`exclude`), `domain`, `concept_ids`, `include_descendants`, `window` (`start_days`, `end_days`, offsets from the index date, -36,500…36,500, start ≤ end), `min_occurrences` (1–100, default 1) |

Semantics (what the compiler implements):
- **Windows** are whole-day offsets from the index date and **inclusive at both ends**. `0` is the index date, negative is before, positive is after. A rule matches if at least `min_occurrences` events of the domain fall inside its window; `exclude` removes matching patients, `include` keeps only them.
- **`date_range`** keeps only index events whose date is inside the range (inclusive); `first_occurrence_only` then takes each person's earliest remaining event. So "first lisinopril in 2021–2024" is the first one *in that range*, not the first ever.
- **Age** is whole years at the index date, adjusted for the birthday (a person who turns 50 on the index date is 50). If month or day of birth is missing, July 1 is assumed.
- **`observation`**: the person needs one observation period covering at least `prior_days` before the index date and `post_days` after it. A gap between periods does not count.
- **Order**: rules are evaluated and reported in list order; labels are the names in the attrition waterfall, which is why they must be unique.
- **Cost cap for `first_occurrence_only: false`.** Every matching record then becomes an index row (145,970 for the broad "Oral Product" drug class, 266,740 for every drug exposure on the demo seed) and each rule is probed once per row, which can outrun the 10 s statement timeout. `parse_definition` therefore allows at most 3 rules (`MAX_RULES_ALL_EVENTS`) and requires `min_occurrences` 1 on each, reporting `rules` and `rules.N.min_occurrences` errors together with any other field errors in one round. The cap is an input policy at `parse_definition`, not a limit of the compiler. Decision recorded in issue #37.
  **This cap limits the work per index row, not the total.** A broad example (3 rules, +-100-year windows, 145,970 index rows) takes about 1.6 s, but the reviewer found allowed shapes that take 3-6 s and some that still hit the timeout (an index event over all 334 drug concepts with 2-3 rules over narrow windows), because the index-set size and the window width are not limited. A run that exceeds the timeout is cancelled and reported as a timeout (`CohortTimeout`, mapped by the run endpoint in #12); a hard runtime guarantee would need a lower cap or a window-width limit, which is a product decision.
- With `first_occurrence_only: false` a person can have several index events; counts are always numbers of **distinct persons**, and a person stays if any of their index events passes a step.

Conventions:
- Windows are offsets from the index date: negative is before, `0` is the index date itself. "Any time before" is `ANY_TIME_BEFORE` (-36,500 days, a century), not a short constant, because real patient histories are long.
- Unknown keys are errors, so a typo (`inclde_descendants`) cannot silently drop a criterion.
- Numbers and flags are strict: `"5"`, `1.5`, `"yes"`, `1` for a boolean are all rejected, so a mistyped flag cannot silently flip a criterion.
- `parse_definition(raw)` returns a `CohortDefinition` or raises `DefinitionError` with `errors = [{"field": "rules.1.window.end_days", "message": "..."}]`: every problem at once, as dotted field paths the UI can mark, never echoing submitted text: names, labels and values are not in messages, and an unknown or non-string key is reported as `<unknown field>` under its parent path (for example `rules.1.window.<unknown field>`). If a list is too long its items are still validated, so one round of fixes suffices.
- Measurement criteria (value ranges) are out of scope for now; `measurement` is rejected as a domain until it has a model.

## Compilation (`compiler.py`, `schema.py`)

`build_plan(definition)` returns a `CohortPlan`: an ordered tuple of steps, each a CTE exposing `(person_id, index_date)`. `plan.rows(key)` is the `SELECT` for a step, including the whole CTE chain up to it. Everything is built with SQLAlchemy Core, so every value (concept ids, dates, ages, sex) is a bound parameter and no user text ever becomes SQL; tests enforce this and that the compiler source contains no raw-SQL escape hatches. The compiler never executes anything.

| Step key | Meaning |
|---|---|
| `index` | Events of the index domain whose concept matches (expanded through `concept_ancestor` when `include_descendants`), inside `date_range`, then one earliest event per person when `first_occurrence_only` (otherwise every distinct event date) |
| `demographics` | Index rows whose person is in the requested sex list and whose age **at that row's index date** is within `age_min`..`age_max` |
| `observation` | Index rows covered by **one** observation period that starts at least `prior_days` before the index date and ends at least `post_days` after it. Always present: with the default 0/0 the index date merely has to fall inside an observation period (a person whose observation ended the day before is dropped). A gap between periods does not count as observed time |
| `rule_1` … `rule_N` | One step per rule, in list order. A rule counts the person's records of the rule's concepts (descendants expanded when asked) dated within `[index_date + start_days, index_date + end_days]`, inclusive; `include` keeps rows with at least `min_occurrences` such records, `exclude` drops them. Evaluated separately for each index row |

Details:
- **Sex** is always an explicit filter on the OMOP gender concepts (8532 female, 8507 male); a person with another or unknown gender never matches, even with the default `["F", "M"]`.
- **Age** is `year(index) - year_of_birth`, minus one if the birthday has not been reached; a missing month or day of birth is replaced by July 1.
- **Counting rule for #8**: step rows are `(person_id, index_date)`; with `first_occurrence_only: false` a person can have several rows, so every count must be `COUNT(DISTINCT person_id)`. The steps are shareable CTEs, so one statement can return the count of every step without recomputing earlier ones.
- **`plan.rows()` returns person-level rows and is internal only.** The API must expose counts, never these rows (FR-11, NFR-3).
- **Executing a plan** is the caller's job and must use the read-only `cohortlens_reader` connection with its 10-second statement timeout (FR-14); the compiler itself is execution-agnostic.
- **`concept_ancestor` must contain self-rows** (each concept is its own ancestor at distance 0, as in OMOP); otherwise `include_descendants` on a leaf concept would match nothing. The seed and the truth fixture both have them.
- **Known difference from the SQL oracle**: for a person with a missing birth month or day the engine assumes July 1 while `data/etl/example_cohort.sql` does not handle NULL there. The seed and fixture always populate both, so the two agree on the demo data.
- Step keys are what the code uses (`index`, `demographics`, `observation`, `rule_1` … `rule_N`); labels are display text only and never go into SQL.
- **Occurrences** are records, not days: two exposures on the same day count as two. With `min_occurrences: 1` the rule is an `EXISTS` probe per index row; with more it is a correlated count.
- **`first_occurrence_only` is applied before the observation filter.** OHDSI Circe does it the other way round (observation first, then the earliest event that satisfies it). We take the person's first event in `date_range` and then ask whether they were observed around it, which is what a new-user design means: a person whose first-ever exposure lacks a washout is not rescued by a later one.
- The counting query (#8) is described under "Attrition counts" below; the baseline (#10) arrives later on top of the same chain. Note the engine reports its steps in chain order (index, demographics, observation, rules), which differs from the order of the SQL oracle in `data/etl/example_cohort.sql`; the final count is the same.


## Attrition counts (`attrition.py`)

`run_attrition(conn, plan, timeout_ms=10_000)` returns one `StepCount(key, label, persons)` per step, in chain order (`index`, `demographics`, `observation`, `rule_1` ... `rule_N`). It is **one statement**: `count_statement(plan)` unions a `COUNT(DISTINCT person_id)` per step over the plan's shared CTE chain, so every CTE is defined once and no step is recomputed. The position of each step is a bound parameter, and the statement returns only `(position, persons)`: no person-level row leaves the module.

- **Counts are distinct persons** (a person with several index events counts once per step), and a step nobody reaches is reported as `0`, never omitted.
- **Timeout**: the statement runs under a transaction-local `statement_timeout` (`set_config(..., true)` with a bound value, default 10 s, the same as the reader engine). When the database cancels it, `run_statement` rolls the transaction back and raises `CohortTimeout`; the run endpoint (#12) turns that into a run recorded with outcome `timeout`.
- **Suppression is not applied here.** `StepCount.persons` is the exact number; small-cell masking (`"<11"`, #9) happens before anything reaches the API.
- Truth-fixture values for the reference cohort, in engine order: 22, 21, 17, 15, 12, 10 (`test_attrition_counts.py`; the SQL oracle orders its steps differently but ends at 10).


## Small-cell suppression (`suppression.py`)

`suppress_count(n)` returns `0` for 0, the string `"<11"` for 1 to 10, and the exact number for 11 or more (`SMALL_CELL_THRESHOLD = 11`). `suppress_steps(step_counts)` turns the engine's exact `StepCount` list into `PublicStepCount(key, label, persons)`, the only shape the API may return for a waterfall; it has no person-level field. The module is pure logic (a test forbids database imports), and it rejects negative numbers (`ValueError`) and non-integers, booleans included (`TypeError`), rather than guess.

- **Exact counts never leave the engine.** `StepCount` is internal; the run endpoint (#12) and the baseline (#10) must pass everything through this module before serialising.
- **Known limitation: differencing.** When two adjacent steps are both 11 or more, their exact difference (the number a rule removed) can be 1 to 10 and is visible by subtraction. Masking each count is the rule the spec asks for (NFR-4); hiding derived differences would need complementary suppression, which is out of scope here and is a documented limitation for a synthetic-data demo.

## Baseline table (`baseline.py`)

`run_baseline(conn, plan, timeout_ms=10_000)` runs two aggregate statements over the final cohort (the plan's last step; each person once, at their **earliest surviving index date**) and returns an exact `Baseline`; `suppress_baseline` turns it into the `PublicBaseline` the API may return: every age/sex cell passes through `suppress_count` (1 to 10 become `"<11"`, 0 stays 0), and comorbidities with fewer than 11 patients are **left out entirely**, with a constant `comorbidity_note` ("Conditions with fewer than 11 patients are not shown.").

- **Age and sex**: age at the index date (same `age_at` rule as the demographics step) in the bands `0-17, 18-29, 30-39, 40-49, 50-59, 60-69, 70-79, 80+`, by sex `F`, `M` or `other` (any other gender concept). The grid is always complete, 8 bands by 3 sexes, with `0` for empty cells so the UI layout is stable. Persons are classified in a subquery and then grouped: person-level rows never leave the database.
- **Comorbidities**: conditions recorded **on or before the index date** (a condition on the index date counts, one after it does not), counted as distinct members, the ten most common first, ties broken by the lower concept id. The name comes from `cdm.concept` (`null` if the concept row is missing). The index condition itself is not excluded; for a hypertension cohort hypertension will lead the list, which is itself informative.
- Comorbidities are sorted by exact count and then those under 11 are dropped, which leaves the top conditions among those with at least 11 (a shown row always has an exact count of 11 or more, and the list can be shorter than ten or empty). The note is returned every time, so its presence says nothing about the cohort.
- Both statements go through the same statement timeout as the attrition counts (`CohortTimeout`).
- **Rare conditions are not named.** A condition with fewer than 11 patients is never listed (decision of the product owner, replacing an earlier design that showed masked rows), so a small cohort cannot be profiled through its comorbidity list. Remaining limitations (differencing): subtracting the visible age/sex cells from an exact attrition total still reveals the sum of the masked cells (described under small-cell suppression), and a shown exact comorbidity count against an exact cohort size reveals how many members lack that condition (a complement of 1 to 10 is possible in a small cohort of 11 or more).
- The `other` sex bucket exists for gender concepts other than female and male in the data; the definition model itself only allows `F` and `M`, so `other` people are always filtered out of cohorts today and the bucket is zero.

## Worked examples

Counts below are from the demo dataset (5,724 synthetic persons) and are unsuppressed because this is a development document about synthetic data; the API applies `"<11"` suppression. The SQL for the reference cohort is `data/etl/example_cohort.sql`.

### Reference cohort: new-user lisinopril (feasible)
Defined in `docs/spec.md` §7. Index = first lisinopril exposure; at least 365 days of prior observation; no ACE-inhibitor exposure in the prior 365 days; hypertension at any time on or before the index date; no prior heart failure; age 50 or older at index.

| Step | Persons |
|---|---|
| First lisinopril exposure (index) | 920 |
| ≥365 days prior observation | 920 |
| No ACE inhibitor in prior 365 days | 919 |
| Hypertension on or before index | 837 |
| No prior heart failure | 829 |
| Age ≥ 50 at index | **481** |

### Counter-example: an infeasible cohort
Same design, but hypertension is required **in the 365 days before the index date** and age must be **65 or older**.

| Step | Persons |
|---|---|
| First lisinopril exposure (index) | 920 |
| ≥365 days prior observation and no ACE inhibitor in prior 365 days | 919 |
| Hypertension in the 365 days before index | 242 |
| No prior heart failure | 241 |
| Age ≥ 65 at index | **6** |

Why it fails:

- **Chronic conditions are recorded once, at onset.** Synthea (like many EHR extracts) writes one hypertension diagnosis with its onset date and never repeats it. A patient diagnosed in 2005 and started on lisinopril in 2012 has no hypertension record inside a 365-day lookback, so the short window silently drops 677 of 919 patients (919 → 242). Prevalent chronic conditions need an "any time before index" window.
- **Treatment starts early.** The median age at first lisinopril is 53, so an age floor of 65 removes almost everyone who is left (241 → 6).

The lesson for users: the attrition waterfall exists to expose steps like these. A window that is clinically reasonable on paper can remove most of a cohort because of how the data records chronic disease.

### Earlier hypertension-anchored version
Anchoring on first hypertension diagnosis with a 30-day lisinopril start window was also infeasible on a Medicare-claims sample (3 of 1,000 patients), because first diagnoses cluster at the start of observation. Anchoring on treatment initiation (new-user design) avoids this.

## Performance (measured)

Measured on the committed seed (5,724 persons, the production indexes, `ANALYZE` run by the seed) in PostgreSQL 16 in Docker on a developer laptop, with **every step counted in one statement** (`run_attrition`), median of three runs unless noted. "Broad concepts" are "Oral Product" (36217214, drugs) and "Clinical finding" (441840, conditions); the 10 rules alternate between them with a window of +-100 years. Absolute times depend on the machine; the ratios are what matter. `test_seed_engine_performance.py` enforces the first three rows against the seed (`make verify-seed`).

| Definition | Before | After |
|---|---|---|
| Reference cohort (6 steps) | 0.05 s | 0.04 s |
| 10 rules, `min_occurrences: 1`, broad concepts, first occurrence only | 1.0 s | 0.6 s (same plan before and after; run-to-run noise) |
| 10 rules, `min_occurrences: 2`, broad concepts, first occurrence only | 6.2-8.8 s | **0.7 s** |
| 10 rules, `min_occurrences: 1`, every exposure an index event (266k index rows) | 16.7 s | 12.7 s (unchanged plan; run-to-run noise) |
| 10 rules, `min_occurrences: 2`, every exposure an index event | over 60 s (cancelled) | 20.9 s |

### Diagnosis (issue #37)

1. **Statistics and indexes are fine.** The seed ends with `ANALYZE;` (every `cdm` table shows `last_analyze` after a load) and all composite indexes exist. They are not what the plan uses for broad concepts: `EXPLAIN (ANALYZE, BUFFERS)` shows each rule probing `idx_drug_person` / `idx_condition_person` (person only), because a correlated look-up by person is far more selective than a look-up by a broad concept. The composite `(concept, person, date)` indexes serve narrow concepts and the concept search.
2. **Where the time goes.** For each rule the planner estimates one row per probe, then, for each of the ~5.4k index rows, fetches the person's ~50 records and probes `concept_ancestor` once per record (266,581 probes in one rule) before `count(*)` is compared with `min_occurrences`. 10 rules cost about 4.6 s in the plan.
3. **Expanding each rule's concept set once did not help.** A `MATERIALIZED` CTE per rule made the worst case 40 s (a nested loop against an unindexed CTE scan); a one-time `= ANY(array)` sub-select made it 35 s (the planner cannot hash a non-constant array); resolving the descendants in a separate query and passing a bound list got to 2.5 s but needs database access at compile time, so it was not adopted. A person-leading covering index `(person_id, concept, date)` made no difference (7.8 s) and was not added.
4. **What worked: stop counting at `min_occurrences`.** A rule needing k occurrences only has to know whether there are at least k, so the correlated count is taken over at most k matching records (`SELECT person_id ... LIMIT k`, k a bound parameter) instead of all of them. Results are identical (the truth-fixture tests are unchanged, and `test_occurrence_early_exit.py` pins the k-1 / k / more boundaries and that one person's records never satisfy another's rule); only the plan changes. `min_occurrences: 1` was already a plain `EXISTS` probe.

### Every exposure as an index event: capped, and the timeout is the backstop

With `first_occurrence_only: false` and a broad index concept every exposure becomes an index row (145,970 for the broad "Oral Product" class, 266,740 for every drug exposure on the demo seed), and each rule is probed once per row: a 10-rule cohort is roughly 2.7 million probes, 9-21 s depending on the machine. Windows are per index row, so the semantics cannot change. Decision (product owner, issue #37): `parse_definition` allows at most 3 rules, each with `min_occurrences` 1, when `first_occurrence_only` is false (see the definition model above); no further cap is planned. The 10 s statement timeout is the backstop: a run that exceeds it is cancelled and reported to the user as a timeout with a clear message (the run endpoint, #12).

Not adopted, for the record: `SET enable_nestloop = off` took the `min_occurrences: 1` every-exposure case from 9.1 s to 1.0 s on a reviewer's machine, but made `min_occurrences: 2` worse (12.7 s to 25 s); `work_mem` and `jit = off` changed nothing.

Note on the tests: on a fast machine the pre-change compiler also stays under 3 s for the first-occurrence worst case (2.7 s was measured on one), so `test_seed_engine_performance.py` is a regression guard with about 5x margin on the current code, not proof that the change was needed; the 6-9 s figures above are from the author's laptop.
