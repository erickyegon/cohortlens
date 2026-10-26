# ADR-001: OMOP subset over a custom schema, and the dataset decision

Status: accepted · Date: 2026-10-26

## Context
CohortLens answers feasibility questions with SQL over the OMOP Common Data Model. We need a small, realistic, synthetic OMOP database that contains hypertension, lisinopril and heart failure at a density that gives a meaningful attrition waterfall (final example-cohort count in the hundreds), and that fits Neon's 0.5 GB free tier (target: under about 250 MB including indexes).

## Options evaluated
Each candidate was downloaded and measured with the same example-cohort logic.

| Candidate | Result | Verdict |
|---|---|---|
| Eunomia `synthea-metabolic_syndrome-10k` and `synthea-heart-10k` (CDM 5.3) | 10.7k persons each and nearly identical to each other. Only 5 condition concepts (coronary arteriosclerosis, stroke, atrial fibrillation, cardiac arrest, MI), 31 drug concepts that are all vaccines, empty `measurement`. **0** persons with hypertension, lisinopril or heart failure. Full vocabulary: 5.9M concepts, 75M ancestor rows. | Rejected: no chronic-disease drugs or conditions. Its vocabulary is reused for concept mapping. |
| Eunomia `GiBleed` (CDM 5.3) | 2,694 persons, acute-care conditions, no hypertension or lisinopril. | Rejected for the demo; kept as the source for the small test fixture. |
| `synpuf-1k` (CMS SynPUF, CDM 5.4) | 1,000 persons. Original hypertension-anchored cohort: 755 → 535 → 79 → 4 → **3**. New-user cohort: 404 → 171 → 72 → 55 → 22 → **17**. Trimmed vocabulary about 66 MB. | Rejected: too small; observation starts in 2008. |
| `synpuf-110k` (CMS SynPUF, CDM 5.3 only; 3.4 GB download) | 20,000-person seeded sample, new-user cohort: 8,196 → 3,387 → 1,495 → 81 → 18 → **14**. Trimmed size about **1.2 GB** (10.5M condition rows). Vocabulary mapping looks degraded: hypertension on only 13% of persons (75% in the 1k set), the NDFRT ACE-inhibitor class has no descendants, 49% of persons have a heart failure record. | Rejected: final count 14 and size about 5x over budget. |
| Generate our own with Synthea | See decision. | Chosen. |

## Decision
Generate the data with Synthea (master-branch-latest jar), **seed 42, population 5,000, Massachusetts**, and convert it with our own ETL (`data/etl/synthea_to_omop.py`) into the nine OMOP tables CohortLens uses. Concepts are mapped with the standard vocabulary (SNOMED to conditions, RxNorm to drugs, LOINC to measurements). The vocabulary comes from the Eunomia Synthea sets and is trimmed to every concept used in the data plus all of its ancestors, keeping only ancestor rows whose descendant is in the data. This preserves descendant expansion while cutting `concept_ancestor` from 75M rows to about 40k. Measurements are limited to blood pressure and HbA1c.

Result: 5,724 persons (5,000 requested plus those who died during the simulation), 112k conditions, 267k drug exposures, 325k visits, 201k measurements, 26k concepts; 72 MB as CSV, 10 MB as the gzipped seed. The reference cohort yields 920 → 481 patients (see `docs/cohort-engine.md`).

## Consequences
- The seed is reproducible: anyone with Java, Python and DuckDB can regenerate it. Runtime needs none of them: `docker compose up` loads `data/seed/omop_subset.sql.gz`.
- Synthea records chronic conditions once, at onset, so hypertension windows must be "any time before index". The 365-day variant is kept as the documented infeasible counter-example.
- The schema is a pragmatic subset of OMOP CDM columns (standard names and types), so the engine ports to a full CDM.
- Known data quirks: 667 visit rows and a handful of drug and measurement rows start after the death date (Synthea artefact, under 0.3%); the observation period is derived from the first and last dated record.
- Synthea's SNOMED "finding" codes (employment status, medication review due) are not Condition-domain concepts in OMOP and are dropped.
