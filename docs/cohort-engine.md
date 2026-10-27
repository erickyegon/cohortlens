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

Measured on the committed seed (5,724 persons, production indexes) in PostgreSQL 16, one statement per cohort:

| Definition | Last step | Every step counted in one statement |
|---|---|---|
| Reference cohort (6 steps) | 0.07 s | 0.03 s |
| 10 rules, `min_occurrences: 1`, broad concepts (an "Oral Product" drug class, "Clinical finding") | 0.2 s | 0.4 s |
| 10 rules, `min_occurrences: 2`, broad concepts | **4.4 s** | 2.5 s |
| 10 rules, `min_occurrences: 1`, every exposure an index event (about 266k index rows) | 2.6 s | **8.8 s** |
| 10 rules, `min_occurrences: 2`, every exposure an index event | **over 40 s** | over 40 s |

Realistic cohorts are far inside the 3 s p95 target (NFR-1); the bold cases are contrived but the model allows them, and the 10 s statement timeout is what protects the database. Cause: each `min_occurrences > 1` rule is a correlated `count(*)` per index row, and the planner underestimates the filtered rows and picks quadratic nested loops. Tried and rejected: a set-based `GROUP BY ... HAVING` join (the first case went from 4.4 s to over 40 s, the fourth from 2.6 s to 5.1 s), and `MATERIALIZED` CTEs (helps the third case, makes the fourth about 3.5 times slower). Tracked in issue #37.

