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

## Compilation
_Added with the SQL builder issues (#6 onward)._

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
