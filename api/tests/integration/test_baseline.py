"""Issue #10: the baseline table (age band x sex, top 10 comorbidities) for the final cohort.

Expected values were derived by hand from api/tests/fixtures/build_truth_fixture.py. The reference cohort's final
members are P1 P2 P3 P6 P9 P13 P15 P18 P20 P21, all indexed on 2020-06-01:
- ages: P3 58 F, P18 50 M (turns 50 on the index date) -> 50-59; P1 P9 P13 P15 P21 are F and 60, P2 M 65, P6 M 60,
  P20 M 69 (birthday not reached) -> 60-69: F 5, M 3
- conditions on or before the index date: essential hypertension (320128) for P1 P3 P6 P9 P13 P15 P18 P20 P21 = 9,
  malignant hypertension (317898, a child concept) for P2 = 1, heart failure (316139) for P21 only = 1 (recorded on
  the index date; P15's heart failure is a year later and does not count)
Index-only cohort (no demographics, no rules): 21 members (P22's observation ends the day before its index), by
age/sex: 40-49 F 1 (P17), 50-59 F 2 (P3, P19 who is 58 at her first index 2019-03-01) M 1 (P18), 60-69 F 9 M 8.
"""
import dataclasses
import json
import pathlib

from sqlalchemy import text

from app.cohort_engine.baseline import (
    AGE_BANDS, SEXES, Baseline, PublicBaseline, run_baseline, suppress_baseline)
from app.cohort_engine.compiler import build_plan
from app.cohort_engine.models import parse_definition

REFERENCE = json.loads((pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "reference_cohort.json")
                       .read_text(encoding="utf-8"))
BAND_NAMES = ["0-17", "18-29", "30-39", "40-49", "50-59", "60-69", "70-79", "80+"]


def baseline_of(engine, raw, after=None):
    """Run the baseline for `raw`; `after` may insert extra rows first (the transaction is rolled back)."""
    with engine.connect() as conn:
        if after:
            after(conn)
        result = run_baseline(conn, build_plan(parse_definition(raw)))
        conn.rollback()
    return result


def cells(baseline):
    return {(c.age_band, c.sex): c.persons for c in baseline.age_sex}


def index_only():
    return {"name": "t", "index_event": {"domain": "drug", "concept_ids": [1308216]}}


# ---- Given a completed run, then counts by age band and sex are returned ---------------------------------------------

def test_the_age_bands_and_sexes_are_the_documented_ones():
    assert [b[0] for b in AGE_BANDS] == BAND_NAMES
    assert SEXES == ("F", "M", "other")


def test_reference_cohort_counts_by_age_band_and_sex_equal_the_hand_derived_values(truth_engine):
    got = cells(baseline_of(truth_engine, REFERENCE))
    assert {k: v for k, v in got.items() if v} == {("50-59", "F"): 1, ("50-59", "M"): 1,
                                                     ("60-69", "F"): 5, ("60-69", "M"): 3}
    assert sum(got.values()) == 10


def test_every_band_and_sex_combination_is_present_even_when_empty(truth_engine):
    got = baseline_of(truth_engine, REFERENCE).age_sex
    assert [(c.age_band, c.sex) for c in got] == [(b, s) for b in BAND_NAMES for s in ("F", "M", "other")]
    assert cells(baseline_of(truth_engine, REFERENCE))[("80+", "other")] == 0


def test_age_is_measured_at_the_index_date_not_today(truth_engine):
    # P18 turns 50 exactly on the index date (50-59); P20 is 69 because his birthday (July 7) has not come yet
    got = cells(baseline_of(truth_engine, REFERENCE))
    assert got[("50-59", "M")] == 1 and got[("60-69", "M")] == 3 and got[("70-79", "M")] == 0


def test_a_person_with_several_index_rows_is_counted_once_at_their_earliest_surviving_index(truth_engine):
    raw = {**index_only(), "index_event": {"domain": "drug", "concept_ids": [1308216], "first_occurrence_only": False}}
    got = cells(baseline_of(truth_engine, raw))
    assert sum(got.values()) == 21  # distinct persons, not index rows (P19 has two lisinopril dates)
    assert got[("50-59", "F")] == 2  # P3 and P19 (58 at her earliest index, 2019-03-01)


def test_index_only_cohort_matches_the_hand_derived_grid(truth_engine):
    got = {k: v for k, v in cells(baseline_of(truth_engine, index_only())).items() if v}
    assert got == {("40-49", "F"): 1, ("50-59", "F"): 2, ("50-59", "M"): 1, ("60-69", "F"): 9, ("60-69", "M"): 8}


# ---- Given a completed run, then the ten most common comorbidities are returned --------------------------------------

def test_reference_cohort_comorbidities_are_counted_on_or_before_the_index_date(truth_engine):
    got = [(c.concept_id, c.name, c.persons) for c in baseline_of(truth_engine, REFERENCE).comorbidities]
    assert got == [(320128, "Essential hypertension", 9), (316139, "Heart failure", 1),
                   (317898, "Malignant essential hypertension", 1)]  # ties: lowest concept id first


def test_only_the_ten_most_common_comorbidities_are_returned_most_common_first(truth_engine):
    def twelve_extra(conn):
        for n in range(1, 13):
            conn.execute(text("insert into cdm.concept values (:c, :n, 'Condition', 'SNOMED', 'Clinical Finding',"
                              " 'S', :n, NULL)"), {"c": 900000 + n, "n": f"Extra condition {n}"})
            conn.execute(text("insert into cdm.condition_occurrence (condition_occurrence_id, person_id,"
                              " condition_concept_id, condition_start_date, condition_type_concept_id)"
                              " values (:i, 1, :c, '2019-01-01', 32020)"), {"i": 5000 + n, "c": 900000 + n})
        conn.execute(text("insert into cdm.condition_occurrence (condition_occurrence_id, person_id,"
                          " condition_concept_id, condition_start_date, condition_type_concept_id)"
                          " values (6000, 3, 900001, '2019-01-01', 32020)"))
    got = baseline_of(truth_engine, REFERENCE, after=twelve_extra).comorbidities
    assert [c.concept_id for c in got] == [320128, 900001, 316139, 317898, 900002, 900003, 900004, 900005, 900006,
                                           900007]
    assert [c.persons for c in got] == [9, 2, 1, 1, 1, 1, 1, 1, 1, 1]


def test_a_condition_recorded_after_the_index_date_is_not_a_comorbidity(truth_engine):
    counts = {c.concept_id: c.persons for c in baseline_of(truth_engine, REFERENCE).comorbidities}
    assert counts[316139] == 1  # P21 (on the index date) but not P15 (2021-01-01, after the index)


# ---- Given any baseline cell of 1 to 10, then it displays "<11" ------------------------------------------------------

def test_small_cells_are_masked_and_cells_of_eleven_or_more_stay_exact(truth_engine):
    def three_more_men(conn):  # three extra men born 1960-01-01 who pass every index-only step: M 60-69 = 8 + 3 = 11
        for p in (101, 102, 103):
            conn.execute(text("insert into cdm.person (person_id, gender_concept_id, year_of_birth, month_of_birth,"
                              " day_of_birth, race_concept_id, ethnicity_concept_id) values (:p, 8507, 1960, 1, 1,"
                              " 0, 0)"), {"p": p})
            conn.execute(text("insert into cdm.observation_period values (:p, :p, '2015-01-01', '2023-12-31', 32817)"),
                         {"p": p})
            conn.execute(text("insert into cdm.drug_exposure (drug_exposure_id, person_id, drug_concept_id,"
                              " drug_exposure_start_date, drug_exposure_end_date, drug_type_concept_id)"
                              " values (:p, :p, 19080128, '2020-06-01', '2020-06-01', 32818)"), {"p": p})
    exact = baseline_of(truth_engine, index_only(), after=three_more_men)
    assert cells(exact)[("60-69", "M")] == 11 and cells(exact)[("60-69", "F")] == 9
    public = {(c.age_band, c.sex): c.persons for c in suppress_baseline(exact).age_sex}
    assert public[("60-69", "M")] == 11  # exactly at the threshold: shown
    assert public[("60-69", "F")] == "<11" and public[("50-59", "F")] == "<11" and public[("40-49", "F")] == "<11"
    assert public[("80+", "M")] == 0  # an empty cell stays 0


def test_every_cell_of_the_reference_baseline_is_masked_or_zero(truth_engine):
    public = suppress_baseline(baseline_of(truth_engine, REFERENCE))
    assert {c.persons for c in public.age_sex} == {0, "<11"}
    assert {c.persons for c in public.comorbidities} == {"<11"}


# ---- aggregates only -------------------------------------------------------------------------------------------------

def test_the_baseline_types_hold_no_person_level_field():
    assert [f.name for f in dataclasses.fields(Baseline)] == ["age_sex", "comorbidities"]
    assert [f.name for f in dataclasses.fields(PublicBaseline)] == ["age_sex", "comorbidities"]
