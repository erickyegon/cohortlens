# Cohort engine

Definition model (Pydantic) → validation → SQLAlchemy Core compiler → CTE chain (index_events → demographics → observation_window → one CTE per rule → final) → per-step counts → suppression → baseline.

## Definition model (`api/app/cohort_engine/models.py`)

A cohort definition is typed data validated before anything else runs. The module imports no database driver, so validating a definition can never execute SQL (a test enforces this). The reference cohort in `api/tests/fixtures/reference_cohort.json` is the spec §7 definition expressed in this model.

| Part | Fields |
|---|---|
| `name` | 1–200 characters |
| `index_event` | `domain` (`condition` or `drug`), `concept_ids` (1+ positive ints, de-duplicated), `include_descendants` (default true), `first_occurrence_only` (default true), optional `date_range` (`start`/`end`, either may be open) |
| `demographics` | `age_min`, `age_max` (0–130, optional), `sex` (`F`, `M`; default both) |
| `observation` | `prior_days`, `post_days` (0–36,500, default 0) |
| `rules` (0–10) | `label`, `type` (`include`/`exclude`), `domain`, `concept_ids`, `include_descendants`, `window` (`start_days`, `end_days`, offsets from the index date, -36,500…36,500, start ≤ end), `min_occurrences` (1–100, default 1) |

Conventions:
- Windows are offsets from the index date: negative is before, `0` is the index date itself. "Any time before" is `ANY_TIME_BEFORE` (-36,500 days, a century), not a short constant, because real patient histories are long.
- Unknown keys are errors, so a typo (`inclde_descendants`) cannot silently drop a criterion.
- Numbers are strict integers (`"5"` and `1.5` are rejected); concept ids must be positive.
- `parse_definition(raw)` returns a `CohortDefinition` or raises `DefinitionError` with `errors = [{"field": "rules.1.window.end_days", "message": "..."}]`: every problem at once, as dotted field paths the UI can mark, never echoing submitted text such as names or labels.
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
