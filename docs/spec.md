# CohortLens — Product Specification

Status: draft v0.1 · Owner: human product owner · Last updated: 2026-10-26

## 1. Vision and problem statement

Before an observational study is funded, someone must answer a feasibility
question: how many patients meet the inclusion and exclusion criteria, and
where does the cohort shrink? Today that means hand-written SQL against an OMOP
database, re-run every time a criterion changes. It is slow, error-prone and
hard to share.

CohortLens is a web app that tells a real-world-evidence (RWE) scientist, in
seconds, whether enough patients exist in a database to run a proposed study.
The user defines a cohort in a form, clicks **Run feasibility**, and sees the
final count, an attrition waterfall and a baseline table.

All data is synthetic. No real patient information ever exists in the system.

## 2. Personas

- **RWE scientist (primary).** Epidemiologist, HEOR analyst or RWE scientist
  scoping a study protocol. Knows what an index event and a washout are; does
  not want to write SQL.
- **On-call engineer (secondary).** Owns the running service. Receives an
  alert, needs a trace-linked error reference and a runbook. At runtime this
  persona is served by the SRE/on-call agent.

## 3. User stories

| ID | Story |
|----|-------|
| US-1 | As an RWE scientist, I can search concepts by name so I can pick the index condition and criteria without knowing concept IDs. |
| US-2 | As an RWE scientist, I can define a cohort (index event, age range, observation window, include/exclude rules) and save it. |
| US-3 | As an RWE scientist, I get immediate validation errors for impossible definitions. |
| US-4 | As an RWE scientist, I can run feasibility for a saved cohort and see the final count and the count after each step. |
| US-5 | As an RWE scientist, I see a baseline table (age bands, sex, top comorbidities) for the final cohort. |
| US-6 | As an RWE scientist, I can reopen a saved cohort, edit a criterion, and re-run it. |
| US-7 | As an RWE scientist, I can see the run history of a cohort. |
| US-8 | As an RWE scientist, small counts are masked so no individual can be singled out. |
| US-9 | As an RWE scientist, when a run fails I see a reference ID I can give to support. |
| US-10 | As an on-call engineer, I am alerted when cohort runs fail or slow down, and the alert links to a runbook. |
| US-11 | As an on-call engineer, a faulty release is rolled back automatically and the incident is documented. |
| US-12 | As a developer, I can start the whole stack locally with one command. |

## 4. Functional requirements

| ID | Requirement | Story |
|----|-------------|-------|
| FR-1 | `GET /api/concepts?q=` returns concepts matching the text, for typeahead. | US-1 |
| FR-2 | Cohort CRUD: create, list, get, update (bumps version), delete. | US-2, US-6 |
| FR-3 | A cohort definition has: index event (domain, concept IDs, descendants flag, first-occurrence flag, date range), demographics (age range, sex), observation window (prior/post days) and up to 10 rules. | US-2 |
| FR-4 | Each rule has a label, include/exclude type, domain, concept IDs, descendants flag, a day window relative to the index date, and a minimum occurrence count. | US-2 |
| FR-5 | Validation rejects: age_min > age_max, empty concept lists, windows where start is after end, more than 10 rules. Rejection happens before any SQL runs. | US-3 |
| FR-6 | `POST /api/cohorts/{id}/runs` compiles the definition into one parameterized SQL query and returns the count after every step in a single round trip. | US-4 |
| FR-7 | Descendant expansion uses the concept ancestor table. | US-4 |
| FR-8 | Each run is persisted with status, step counts, final count, duration, trace ID and service version. | US-4, US-7 |
| FR-9 | The baseline table (age bands, sex, top 10 comorbidities) is computed for the final cohort. | US-5 |
| FR-10 | Any count from 1 to 10 is returned as `"<11"`. Zero stays `0`. | US-8 |
| FR-11 | No endpoint returns patient-level rows. | US-8 |
| FR-12 | Every response carries an `X-Trace-Id` header; failed runs show "Something went wrong. Reference: <trace id>". | US-9 |
| FR-13 | `GET /healthz` (liveness) and `GET /readyz` (readiness incl. database check). | US-10 |
| FR-14 | Queries are cancelled after a 10 s statement timeout. | US-4 |
| FR-15 | The UI provides a cohort list, a builder (with reorderable rules) and a results screen with waterfall chart, baseline table and run history. | US-2, US-4–7 |
| FR-16 | Alert rules on run error rate and p95 latency route prod incidents to the on-call agent. | US-10, US-11 |
| FR-17 | `make up` starts frontend, API, database (auto-seeded) and local telemetry stack. | US-12 |

## 5. Non-functional requirements

- **NFR-1** p95 cohort-run latency under 3 s on the demo dataset.
- **NFR-2** 99% of cohort runs succeed over a rolling 7 days (the SLO the alerts protect).
- **NFR-3** No patient-level data, query parameter values or secrets in telemetry.
- **NFR-4** Counts from 1 to 10 are suppressed as `"<11"`.
- **NFR-5** The full stack starts locally with one command.

## 6. Acceptance criteria (Given / When / Then)

**US-1 Concept search**
- Given the seeded vocabulary, when I type "hypertens", then I see matching concepts with ID, name and domain within 500 ms.
- Given a query with no matches, then I see an empty list, not an error.

**US-2 Define and save**
- Given a valid definition, when I click Save, then it appears in the cohort list and survives an application restart.
- Given a saved cohort, when I edit and save it, then its version increases by 1.

**US-3 Validation**
- Given age_min 80 and age_max 40, when I save, then I see a field-level error and nothing is persisted.
- Given a rule with an empty concept list, then I see a field-level error.
- Given 11 rules, then I see an error and the run is not attempted.

**US-4 Run feasibility**
- Given a saved cohort with an index event and two rules, when I click Run feasibility, then the final count and a count for each step are shown within 3 seconds.
- Given a step with 1 to 10 patients, then it displays "<11".
- Given the database is unreachable, then I see an error with a trace reference and the run is recorded as failed.

**US-5 Baseline**
- Given a completed run, then I see counts by age band and sex and the ten most common comorbidities, all subject to suppression.

**US-6 Edit and re-run**
- Given a cohort with a previous run, when I change a criterion and re-run, then a new run is added and the earlier run is kept in history.

**US-7 History**
- Given a cohort with three runs, then the history lists them newest first with status, final count and duration.

**US-8 Suppression**
- Given a count of 0, then it displays 0. Given counts 1–10, then "<11". Given 11 or more, the exact value.
- Given any API response, then it contains no person-level identifiers.

**US-9 Failure reference**
- Given a failed run, then the UI shows the trace ID and the same ID is in the `X-Trace-Id` header and the stored run.

**US-10 Alerting**
- Given at least 10 runs in 5 minutes and more than 5% failing for 2 minutes in prod, then CohortRunErrorRateHigh fires with a runbook URL.
- Given at least 20 runs in 10 minutes and p95 above 3 s for 5 minutes in prod, then CohortRunLatencyHigh fires.

**US-11 Automated rollback**
- Given a prod alert with agent=enabled and a regression that began within 30 minutes of a deploy, then the agent shifts traffic to the previous healthy revision, verifies recovery, and posts a postmortem on the incident issue.
- Given a database outage affecting all revisions, then the agent escalates and takes no rollback action.

**US-12 One command**
- Given a clean clone with Docker installed, when I run `make up`, then the UI is reachable at http://localhost:8080 with seeded data and no manual steps.

## 7. Reference example cohort

The demo and the truth fixture are built around a **new-user design**, which is the standard feasibility pattern and keeps the index date inside observed data:

| Element | Definition |
|---|---|
| Index event | First lisinopril exposure (ingredient 1308216, with descendants) |
| Observation | At least 365 days of observation before the index date |
| Washout | No ACE inhibitor exposure (class concept with descendants) in the 365 days before the index date |
| Include | Essential hypertension (320128, with descendants) in the 365 days before the index date |
| Exclude | Heart failure (316139, with descendants) any time before the index date |
| Demographics | Age 65 or older at the index date |

The product must be able to express every element above using the cohort definition model in FR-3/FR-4. A deliberately infeasible counter-example is documented in `docs/cohort-engine.md`.

## 8. Glossary

- **OMOP CDM** — Observational Medical Outcomes Partnership Common Data Model, a standard relational schema for health data.
- **Concept** — a standardized clinical term with an integer ID (condition, drug, measurement…).
- **Concept ancestor** — table linking each concept to all its descendants; used to expand "hypertension" to its child concepts.
- **Index event** — the event that defines when a patient enters the cohort (e.g. first hypertension diagnosis). The date is the index date.
- **Washout** — a period before the index date in which a criterion must (or must not) occur.
- **Attrition** — the number of patients removed by each successive criterion.
- **Small-cell suppression** — masking small counts to prevent re-identification.
- **Revision** — an immutable Cloud Run deployment that can receive traffic.
