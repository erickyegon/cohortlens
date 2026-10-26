# Cohort engine

Definition model (Pydantic) → validation → SQLAlchemy Core compiler → CTE chain (index_events → demographics → observation_window → one CTE per rule → final) → per-step counts → suppression → baseline.

_Details are added during phase 2._

## Worked examples

### Reference cohort: new-user lisinopril (feasible)
Defined in `docs/spec.md` §7. Index = first lisinopril exposure; ≥365 days prior observation; no ACE-inhibitor exposure in the prior 365 days; hypertension in the prior 365 days; no prior heart failure; age ≥ 65 at index.

Attrition on the 1,000-person development sample (counts are unsuppressed here because this is a development document about synthetic data; the API applies `"<11"` suppression):

| Step | Persons |
|---|---|
| First lisinopril exposure (index) | 404 |
| ≥365 days prior observation | 171 |
| No ACE inhibitor in prior 365 days | 72 |
| Hypertension in prior 365 days | 55 |
| No prior heart failure | 22 |
| Age ≥ 65 at index | **17** |

### Counter-example: an infeasible cohort
"Patients with a first hypertension diagnosis, age 40–80, ≥365 days prior observation, **starting lisinopril within 30 days of the index**, with no prior heart failure."

Attrition on the same 1,000-person sample:

| Step | Persons |
|---|---|
| First essential hypertension (index) | 755 |
| Age 40–80 at index | 535 |
| ≥365 days prior observation | 79 |
| Lisinopril within 30 days of index | 4 |
| No prior heart failure | **3** |

Why it fails: the data's observation starts in 2008, so most first hypertension diagnoses fall at the very start of observation. The 365-day lookback removes about 85% of patients (535 → 79), and requiring a lisinopril start in a 30-day window removes nearly all that remain. The lesson for users: anchoring on a *prevalent* diagnosis with a tight treatment window is fragile; anchoring on *treatment initiation* (new-user design) preserves patients. The product should make this visible through the attrition waterfall — it is exactly the insight CohortLens exists to surface.
