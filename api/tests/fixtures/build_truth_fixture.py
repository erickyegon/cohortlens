"""Generate api/tests/fixtures/truth_fixture.sql: 20 hand-built patients with known expected counts.

    python api/tests/fixtures/build_truth_fixture.py

Every patient exists to exercise exactly one criterion or boundary of the reference cohort
(docs/spec.md section 7). Expected counts are written down independently in EXPECTED below and in
docs/testing.md, then asserted against the SQL oracle (data/etl/example_cohort.sql) by the tests.
Concept ids follow OMOP naming and most match the seed vocabulary; ids for combination and clinical drugs
(e.g. 974200) are placeholders, and the concept/ancestor rows are a hand-picked subset.
"""
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
DDL = (HERE.parents[2] / "data" / "etl" / "cdm_ddl.sql").read_text(encoding="utf-8")

# concept_id, name, domain, vocabulary, class, standard, code
CONCEPTS = [
    (8507, "MALE", "Gender", "Gender", "Gender", "S", "M"),
    (8532, "FEMALE", "Gender", "Gender", "Gender", "S", "F"),
    (9202, "Outpatient Visit", "Visit", "Visit", "Visit", "S", "OP"),
    (1308216, "lisinopril", "Drug", "RxNorm", "Ingredient", "S", "29046"),
    (19080128, "lisinopril 10 MG Oral Tablet", "Drug", "RxNorm", "Clinical Drug", "S", "314076"),
    (19080129, "lisinopril 20 MG Oral Tablet", "Drug", "RxNorm", "Clinical Drug", "S", "314077"),
    (974200, "hydrochlorothiazide 12.5 MG / lisinopril 20 MG Oral Tablet", "Drug", "RxNorm", "Clinical Drug", "S", "999999"),
    (1341927, "enalapril", "Drug", "RxNorm", "Ingredient", "S", "3827"),
    (40165762, "enalapril maleate 10 MG Oral Tablet", "Drug", "RxNorm", "Clinical Drug", "S", "198188"),
    (21601784, "ACE inhibitors, plain", "Drug", "ATC", "ATC 4th", "C", "C09A"),
    (320128, "Essential hypertension", "Condition", "SNOMED", "Clinical Finding", "S", "59621000"),
    (317898, "Malignant essential hypertension", "Condition", "SNOMED", "Clinical Finding", "S", "70272006"),
    (316139, "Heart failure", "Condition", "SNOMED", "Clinical Finding", "S", "84114007"),
    (4229440, "Chronic congestive heart failure", "Condition", "SNOMED", "Clinical Finding", "S", "88805009"),
    (3004249, "Systolic blood pressure", "Measurement", "LOINC", "Clinical Observation", "S", "8480-6"),
    (3004410, "Hemoglobin A1c/Hemoglobin.total in Blood", "Measurement", "LOINC", "Lab Test", "S", "4548-4"),
]
ANCESTORS = [  # ancestor, descendant, min, max
    (1308216, 19080128, 1, 1), (1308216, 19080129, 1, 1), (1308216, 974200, 1, 1),
    (1341927, 40165762, 1, 1),
    (21601784, 19080128, 2, 2), (21601784, 19080129, 2, 2), (21601784, 974200, 2, 2), (21601784, 40165762, 2, 2),
    (320128, 317898, 1, 1), (316139, 4229440, 1, 1),
]
for cid, *_ in CONCEPTS:  # every concept is its own ancestor at distance 0
    ANCESTORS.append((cid, cid, 0, 0))

LIS10, LIS20, COMBO, ENAL = 19080128, 19080129, 974200, 40165762
HTN, HTN_CHILD, HF, HF_CHILD = 320128, 317898, 316139, 4229440
IDX = "2020-06-01"
OBS_END = "2023-12-31"

# id: (sex, birth, obs_start, obs_end, [(drug_concept, date)], [(condition_concept, date)], death, note)
# obs_start may be a list of (start, end) tuples for patients with several observation periods (obs_end is then ignored).
P = {
    1: ("F", "1960-05-05", "2015-01-01", OBS_END, [(LIS10, IDX)], [(HTN, "2018-03-01")], None, "in cohort: baseline passer"),
    2: ("M", "1955-01-10", "2015-01-01", OBS_END, [(LIS20, IDX)], [(HTN_CHILD, "2017-03-01")], None, "in cohort: hypertension via descendant concept; lisinopril 20 mg"),
    3: ("F", "1962-03-03", "2015-01-01", OBS_END, [(COMBO, IDX)], [(HTN, "2018-03-01")], None, "in cohort: lisinopril via combination product"),
    4: ("M", "1958-02-02", "2015-01-01", OBS_END, [(ENAL, IDX)], [(HTN, "2018-03-01")], None, "out at index: enalapril only, never lisinopril"),
    5: ("F", "1960-05-05", "2020-02-10", OBS_END, [(LIS10, IDX)], [(HTN, "2020-03-01")], None, "out at observation: 112 days of prior observation"),
    6: ("M", "1960-05-05", "2019-06-02", OBS_END, [(LIS10, IDX)], [(HTN, "2019-07-01")], None, "in cohort: observation exactly 365 days before index (inclusive)"),
    7: ("F", "1960-05-05", "2019-06-03", OBS_END, [(LIS10, IDX)], [(HTN, "2019-07-01")], None, "out at observation: 364 days of prior observation"),
    8: ("M", "1960-05-05", "2015-01-01", OBS_END, [(ENAL, "2020-02-21"), (LIS10, IDX)], [(HTN, "2018-03-01")], None, "out at washout: enalapril 101 days before index"),
    9: ("F", "1960-05-05", "2015-01-01", OBS_END, [(ENAL, "2019-04-27"), (LIS10, IDX)], [(HTN, "2018-03-01")], None, "in cohort: enalapril 401 days before index is outside the washout"),
    10: ("M", "1960-05-05", "2015-01-01", OBS_END, [(ENAL, "2019-06-02"), (LIS10, IDX)], [(HTN, "2018-03-01")], None, "out at washout: enalapril exactly 365 days before index (inclusive)"),
    11: ("F", "1960-05-05", "2015-01-01", OBS_END, [(LIS10, IDX)], [], None, "out at hypertension: no hypertension"),
    12: ("M", "1960-05-05", "2015-01-01", OBS_END, [(LIS10, IDX)], [(HTN, "2020-06-02")], None, "out at hypertension: diagnosed the day after index"),
    13: ("F", "1960-05-05", "2015-01-01", OBS_END, [(LIS10, IDX)], [(HTN, IDX)], None, "in cohort: hypertension on the index date"),
    14: ("M", "1960-05-05", "2015-01-01", OBS_END, [(LIS10, IDX)], [(HTN, "2018-03-01"), (HF, "2019-01-01")], None, "out at heart failure: heart failure before index"),
    15: ("F", "1960-05-05", "2015-01-01", OBS_END, [(LIS10, IDX)], [(HTN, "2018-03-01"), (HF, "2021-01-01")], None, "in cohort: heart failure only after index"),
    16: ("M", "1960-05-05", "2015-01-01", OBS_END, [(LIS10, IDX)], [(HTN, "2018-03-01"), (HF_CHILD, "2019-01-01")], None, "out at heart failure: descendant concept (chronic CHF) before index"),
    17: ("F", "1970-06-02", "2015-01-01", OBS_END, [(LIS10, IDX)], [(HTN, "2018-03-01")], None, "out at age: 49 on index date (turns 50 the next day)"),
    18: ("M", "1970-06-01", "2015-01-01", OBS_END, [(LIS10, IDX)], [(HTN, "2018-03-01")], None, "in cohort: turns 50 exactly on the index date"),
    19: ("F", "1960-05-05", "2015-01-01", OBS_END, [(LIS10, "2019-03-01"), (LIS10, "2021-03-01")], [(HTN, "2020-01-01")], None, "out at hypertension: index is the FIRST lisinopril (2019-03-01), hypertension came later"),
    20: ("M", "1950-07-07", "2015-01-01", "2022-02-01", [(LIS10, IDX)], [(HTN, "2018-03-01")], "2022-02-01", "in cohort: dies after index"),
    21: ("F", "1960-05-05", "2015-01-01", OBS_END, [(LIS10, IDX)], [(HTN, "2018-03-01"), (HF, IDX)], None, "in cohort: heart failure on the index date is not 'before' index (strict <)"),
    22: ("M", "1960-05-05", "2015-01-01", "2020-05-31", [(LIS10, IDX)], [(HTN, "2018-03-01")], None, "out at observation: observation ends the day before index"),
    23: ("F", "1960-05-05", [("2015-01-01", "2019-12-31"), ("2020-02-20", OBS_END)], None, [(LIS10, IDX)], [(HTN, "2018-03-01")], None, "out at observation: gap between two observation periods, the one containing index is only 102 days old"),
}

# Independent expectation, step by step (see docs/testing.md).
EXPECTED = {"index": 22, "observation": 18, "washout": 16, "hypertension": 13, "no_heart_failure": 11, "age_50": 10}


def q(s):
    return "NULL" if s is None else "'" + str(s).replace("'", "''") + "'"


def main():
    out = ["-- GENERATED by build_truth_fixture.py; do not edit by hand.",
           "-- 20 hand-built patients; see docs/testing.md for the expected count of every criterion.",
           "DROP SCHEMA IF EXISTS cdm CASCADE;", DDL]
    for c in CONCEPTS:
        out.append("INSERT INTO cdm.concept VALUES (%d, %s, %s, %s, %s, %s, %s, NULL);" % (c[0], q(c[1]), q(c[2]), q(c[3]), q(c[4]), q(c[5]), q(c[6])))
    for a in ANCESTORS:
        out.append("INSERT INTO cdm.concept_ancestor VALUES (%d, %d, %d, %d);" % a)
    drug_id = cond_id = meas_id = obs_id = 0
    for pid, (sex, birth, obs_s, obs_e, drugs, conds, death, note) in P.items():
        y, m, d = (int(x) for x in birth.split("-"))
        gender = 8507 if sex == "M" else 8532
        out.append("-- P%d: %s" % (pid, note))
        out.append("INSERT INTO cdm.person VALUES (%d, %d, %d, %d, %d, '%s 00:00:00', 0, 38003564, 'P%d', %s, 'white', 'nonhispanic');" % (pid, gender, y, m, d, birth, pid, q(sex)))
        periods = obs_s if isinstance(obs_s, list) else [(obs_s, obs_e)]
        for start, end in periods:
            obs_id += 1
            out.append("INSERT INTO cdm.observation_period VALUES (%d, %d, '%s', '%s', 32817);" % (obs_id, pid, start, end))
        out.append("INSERT INTO cdm.visit_occurrence VALUES (%d, %d, 9202, '%s', '%s', 32817, 'ambulatory');" % (pid, pid, IDX, IDX))
        for concept, date in drugs:
            drug_id += 1
            out.append("INSERT INTO cdm.drug_exposure VALUES (%d, %d, %d, '%s', '%s', 32818, 30, %d, NULL);" % (drug_id, pid, concept, date, date, pid))
        for concept, date in conds:
            cond_id += 1
            out.append("INSERT INTO cdm.condition_occurrence VALUES (%d, %d, %d, '%s', NULL, 32020, %d, NULL);" % (cond_id, pid, concept, date, pid))
        if death:
            out.append("INSERT INTO cdm.death VALUES (%d, '%s', 32817);" % (pid, death))
        if pid in (1, 2, 3):  # a little measurement data so the table is exercised
            meas_id += 1
            out.append("INSERT INTO cdm.measurement VALUES (%d, %d, 3004249, '%s', 32817, %d, 'mm[Hg]', %d, '8480-6');" % (meas_id, pid, IDX, 120 + pid * 5, pid))
    (HERE / "truth_fixture.sql").write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    print("wrote truth_fixture.sql with", len(P), "patients; expected", EXPECTED)


if __name__ == "__main__":
    main()
