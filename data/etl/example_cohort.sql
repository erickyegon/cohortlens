-- Reference example cohort (docs/spec.md section 7) as plain SQL over the cdm schema.
-- Portable between Postgres 16 and DuckDB. Returns the attrition counts, one row per step.
-- Used by the seed integration test; the cohort engine must reproduce these numbers.
--
--   index      first lisinopril exposure (ingredient 1308216 + descendants)
--   obs        >= 365 days of observation before the index date
--   washout    no ACE-inhibitor exposure (ATC "ACE inhibitors, plain" 21601784 + descendants) in the 365 days before index
--   htn        essential hypertension (320128 + descendants) any time before or on the index date
--   no_hf      no heart failure (316139 + descendants) before the index date
--   age50      age >= 50 at the index date
WITH idx AS (
  SELECT d.person_id, MIN(d.drug_exposure_start_date) AS idx_date
  FROM cdm.drug_exposure d
  WHERE d.drug_concept_id IN (SELECT descendant_concept_id FROM cdm.concept_ancestor WHERE ancestor_concept_id = 1308216)
  GROUP BY d.person_id
),
obs AS (
  SELECT i.* FROM idx i WHERE EXISTS (
    SELECT 1 FROM cdm.observation_period o
    WHERE o.person_id = i.person_id
      AND o.observation_period_start_date <= i.idx_date - 365
      AND o.observation_period_end_date   >= i.idx_date)
),
washout AS (
  SELECT i.* FROM obs i WHERE NOT EXISTS (
    SELECT 1 FROM cdm.drug_exposure d
    WHERE d.person_id = i.person_id
      AND d.drug_concept_id IN (SELECT descendant_concept_id FROM cdm.concept_ancestor WHERE ancestor_concept_id = 21601784)
      AND d.drug_exposure_start_date BETWEEN i.idx_date - 365 AND i.idx_date - 1)
),
htn AS (
  SELECT i.* FROM washout i WHERE EXISTS (
    SELECT 1 FROM cdm.condition_occurrence c
    WHERE c.person_id = i.person_id
      AND c.condition_concept_id IN (SELECT descendant_concept_id FROM cdm.concept_ancestor WHERE ancestor_concept_id = 320128)
      AND c.condition_start_date <= i.idx_date)
),
no_hf AS (
  SELECT i.* FROM htn i WHERE NOT EXISTS (
    SELECT 1 FROM cdm.condition_occurrence c
    WHERE c.person_id = i.person_id
      AND c.condition_concept_id IN (SELECT descendant_concept_id FROM cdm.concept_ancestor WHERE ancestor_concept_id = 316139)
      AND c.condition_start_date < i.idx_date)
),
age50 AS (
  SELECT i.* FROM no_hf i JOIN cdm.person p ON p.person_id = i.person_id
  WHERE (EXTRACT(YEAR FROM i.idx_date) - p.year_of_birth)
        - CASE WHEN (EXTRACT(MONTH FROM i.idx_date), EXTRACT(DAY FROM i.idx_date)) < (p.month_of_birth, p.day_of_birth) THEN 1 ELSE 0 END >= 50
)
SELECT 1 AS step, 'first lisinopril exposure (index)' AS label, COUNT(*) AS persons FROM idx
UNION ALL SELECT 2, '>=365 days prior observation',          COUNT(*) FROM obs
UNION ALL SELECT 3, 'no ACE inhibitor in prior 365 days',     COUNT(*) FROM washout
UNION ALL SELECT 4, 'hypertension any time before index',     COUNT(*) FROM htn
UNION ALL SELECT 5, 'no prior heart failure',                 COUNT(*) FROM no_hf
UNION ALL SELECT 6, 'age >= 50 at index (final)',             COUNT(*) FROM age50
ORDER BY step;
