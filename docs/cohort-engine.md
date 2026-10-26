# Cohort engine

Definition model (Pydantic) → validation → SQLAlchemy Core compiler → CTE chain (index_events → demographics → observation_window → one CTE per rule → final) → per-step counts → suppression → baseline.

_Details are added during phase 2._

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
