"""Issue #10, review round 1: duplicate conditions, a missing concept row, and every age-band edge.

Reviewer findings: no test made `count(distinct person_id)` matter, the outer join to cdm.concept was untested, and
the band edges outside 50-69 were never exercised (four mutants survived).
"""
import json
import pathlib

import pytest
from sqlalchemy import Integer, bindparam, select, text

from app.cohort_engine.baseline import age_band_case, run_baseline
from app.cohort_engine.compiler import build_plan
from app.cohort_engine.models import parse_definition

REFERENCE = json.loads((pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "reference_cohort.json")
                       .read_text(encoding="utf-8"))
INSERT_CONDITION = text("insert into cdm.condition_occurrence (condition_occurrence_id, person_id,"
                        " condition_concept_id, condition_start_date, condition_type_concept_id)"
                        " values (:i, :p, :c, :d, 32020)")


def comorbidities(engine, extra_rows):
    with engine.connect() as conn:
        for row in extra_rows:
            conn.execute(INSERT_CONDITION, row)
        result = run_baseline(conn, build_plan(parse_definition(REFERENCE))).comorbidities
        conn.rollback()
    return {c.concept_id: (c.name, c.persons) for c in result}


def test_a_person_with_the_same_condition_recorded_twice_counts_once(truth_engine):
    # P1 already has essential hypertension (2018-03-01); two more records before the index must not raise 9 to 11
    extra = [{"i": 7001, "p": 1, "c": 320128, "d": "2019-01-01"}, {"i": 7002, "p": 1, "c": 320128, "d": "2019-02-01"}]
    assert comorbidities(truth_engine, extra)[320128] == ("Essential hypertension", 9)


def test_a_condition_whose_concept_row_is_missing_is_listed_with_no_name(truth_engine):
    extra = [{"i": 7003, "p": 1, "c": 777777, "d": "2019-01-01"}]  # 777777 is not in cdm.concept
    assert comorbidities(truth_engine, extra)[777777] == (None, 1)


@pytest.mark.parametrize("age,band", [
    (0, "0-17"), (17, "0-17"), (18, "18-29"), (29, "18-29"), (30, "30-39"), (39, "30-39"), (40, "40-49"),
    (49, "40-49"), (50, "50-59"), (59, "50-59"), (60, "60-69"), (69, "60-69"), (70, "70-79"), (79, "70-79"),
    (80, "80+"), (110, "80+"),
])
def test_every_age_lands_in_the_documented_band_at_each_edge(truth_engine, age, band):
    with truth_engine.connect() as conn:
        got = conn.execute(select(age_band_case(bindparam("age", age, type_=Integer)))).scalar()
    assert got == band
