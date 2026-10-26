"""Issue #6: index event and demographics compiled to SQL, run against the truth fixture (docs/testing.md).

Expected numbers were worked out by hand from api/tests/fixtures/build_truth_fixture.py:
22 persons have a lisinopril exposure (everyone but P4, who has enalapril only); 12 of them are female and 10 male;
P17 is 49 on the index date (turns 50 the next day) and P18 turns 50 on it; P20 is 69 (birthday is July 7) and P2 is 65.
"""
import datetime as dt

import pytest
from sqlalchemy import text

from app.cohort_engine.compiler import build_plan
from app.cohort_engine.models import parse_definition

LISINOPRIL = 1308216
HYPERTENSION = 320128
ALL_LISINOPRIL = set(range(1, 24)) - {4}


def definition(index=None, demographics=None):
    index_event = {"domain": "drug", "concept_ids": [LISINOPRIL]}
    index_event.update(index or {})
    return parse_definition({"name": "t", "index_event": index_event, "demographics": demographics or {}})


def rows(engine, key, **kwargs):
    plan = build_plan(definition(**kwargs))
    with engine.connect() as conn:
        return [(r[0], r[1]) for r in conn.execute(plan.rows(key)).fetchall()]


def persons(engine, key, **kwargs):
    return sorted({p for p, _ in rows(engine, key, **kwargs)})


# ---- index event ---------------------------------------------------------------------------------------------

def test_first_occurrence_gives_each_person_once_at_their_earliest_date(truth_engine):
    result = rows(truth_engine, "index")
    assert len(result) == 22 and {p for p, _ in result} == ALL_LISINOPRIL
    assert dict(result)[19] == dt.date(2019, 3, 1)  # P19 has lisinopril on 2019-03-01 and 2021-03-01
    assert dict(result)[1] == dt.date(2020, 6, 1)


def test_every_exposure_is_an_index_event_when_first_occurrence_is_off(truth_engine):
    result = rows(truth_engine, "index", index={"first_occurrence_only": False})
    assert len(result) == 23 and len({p for p, _ in result}) == 22
    assert sorted(d for p, d in result if p == 19) == [dt.date(2019, 3, 1), dt.date(2021, 3, 1)]


def test_descendants_are_included_by_default_and_can_be_turned_off(truth_engine):
    # 1308216 is the ingredient; the exposures are clinical drugs below it, so no descendants means no events
    assert len(persons(truth_engine, "index")) == 22
    assert persons(truth_engine, "index", index={"include_descendants": False}) == []


def test_condition_index_with_and_without_descendants(truth_engine):
    # 22 patients have hypertension; P2 has only the child concept 317898 'malignant essential hypertension'
    cond = {"domain": "condition", "concept_ids": [HYPERTENSION]}
    with_children = persons(truth_engine, "index", index=cond)
    without = persons(truth_engine, "index", index={**cond, "include_descendants": False})
    assert len(with_children) == 22 and 11 not in with_children  # P11 has no hypertension at all
    assert 2 in with_children and 2 not in without and len(without) == 21


def test_combination_product_and_second_strength_are_found_through_the_ingredient(truth_engine):
    found = persons(truth_engine, "index")
    assert {2, 3} <= set(found)  # P2: 20 mg tablet, P3: hydrochlorothiazide / lisinopril combination


def test_date_range_is_inclusive_and_excludes_events_outside_it(truth_engine):
    on_day = persons(truth_engine, "index", index={"date_range": {"start": "2020-06-01", "end": "2020-06-01"}})
    assert on_day == sorted(ALL_LISINOPRIL - {19})  # P19's exposures are in 2019 and 2021
    assert persons(truth_engine, "index", index={"date_range": {"end": "2020-05-31"}}) == [19]


def test_date_range_is_applied_before_first_occurrence_is_chosen(truth_engine):
    result = rows(truth_engine, "index", index={"date_range": {"start": "2021-01-01"}})
    assert result == [(19, dt.date(2021, 3, 1))]  # the first lisinopril *in range*, not the first ever


def test_empty_result_is_an_empty_list_not_an_error(truth_engine):
    assert rows(truth_engine, "index", index={"date_range": {"start": "2030-01-01"}}) == []


# ---- demographics ---------------------------------------------------------------------------------------------

def test_age_minimum_is_computed_at_the_index_date_with_birthday_adjustment(truth_engine):
    assert persons(truth_engine, "demographics", demographics={"age_min": 50}) == sorted(ALL_LISINOPRIL - {17})
    assert persons(truth_engine, "demographics", demographics={"age_min": 51}) == sorted(ALL_LISINOPRIL - {17, 18})


def test_age_boundaries_around_a_birthday(truth_engine):
    assert persons(truth_engine, "demographics", demographics={"age_max": 49}) == [17]
    assert persons(truth_engine, "demographics", demographics={"age_min": 50, "age_max": 50}) == [18]


def test_age_is_not_just_a_difference_of_years(truth_engine):
    # P20 is born 1950-07-07 and starts on 2020-06-01: 69 until the birthday, not 70
    assert persons(truth_engine, "demographics", demographics={"age_min": 70}) == []
    assert persons(truth_engine, "demographics", demographics={"age_min": 66}) == [20]


def test_age_is_measured_at_each_persons_own_index_date(truth_engine):
    # P19 is born 1960-05-05 and its index is 2019-03-01: 58, even though a later exposure would make her 60
    assert 19 in persons(truth_engine, "demographics", demographics={"age_min": 58, "age_max": 58})


def test_sex_filter(truth_engine):
    assert len(persons(truth_engine, "demographics", demographics={"sex": ["F"]})) == 12
    assert len(persons(truth_engine, "demographics", demographics={"sex": ["M"]})) == 10
    assert len(persons(truth_engine, "demographics", demographics={"sex": ["F", "M"]})) == 22


def test_age_and_sex_combine(truth_engine):
    assert len(persons(truth_engine, "demographics", demographics={"age_min": 50, "sex": ["F"]})) == 11  # P17 out


def test_demographics_never_adds_people_and_keeps_the_index_date(truth_engine):
    index = dict(rows(truth_engine, "index"))
    for person, date in rows(truth_engine, "demographics", demographics={"age_min": 50}):
        assert index[person] == date


def test_missing_birth_month_and_day_assume_july_first(truth_engine):
    insert_person = text("insert into cdm.person (person_id, gender_concept_id, year_of_birth, race_concept_id,"
                         " ethnicity_concept_id) values (900, 8507, 1970, 0, 0)")
    insert_drug = text("insert into cdm.drug_exposure (drug_exposure_id, person_id, drug_concept_id,"
                       " drug_exposure_start_date, drug_exposure_end_date, drug_type_concept_id)"
                       " values (900, 900, 19080128, :d, :d, 32818)")
    plan = build_plan(definition(demographics={"age_min": 50, "age_max": 50}))
    with truth_engine.connect() as conn:
        trans = conn.begin()
        try:
            conn.execute(insert_person)
            conn.execute(insert_drug, {"d": dt.date(2020, 6, 30)})
            assert 900 not in [r[0] for r in conn.execute(plan.rows("demographics")).fetchall()]  # still 49
            conn.execute(
                text("update cdm.drug_exposure set drug_exposure_start_date = :d where drug_exposure_id = 900"),
                {"d": dt.date(2020, 7, 1)})
            assert 900 in [r[0] for r in conn.execute(plan.rows("demographics")).fetchall()]  # turns 50 on July 1
        finally:
            trans.rollback()


def test_a_cohort_without_demographic_limits_keeps_everyone(truth_engine):
    assert persons(truth_engine, "demographics") == persons(truth_engine, "index")


def test_reference_cohort_index_and_demographics(truth_engine):
    plan = build_plan(definition(demographics={"age_min": 50}))
    with truth_engine.connect() as conn:
        assert len(conn.execute(plan.rows("index")).fetchall()) == 22
        assert len(conn.execute(plan.rows("demographics")).fetchall()) == 21  # only P17 is under 50


@pytest.mark.parametrize("sex", [["F"], ["M"]])
def test_sex_filter_uses_the_person_table_only(truth_engine, sex):
    result = persons(truth_engine, "demographics", demographics={"sex": sex})
    female = {1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23}
    assert set(result) == (female if sex == ["F"] else ALL_LISINOPRIL - female)


# ---- review round 1 on PR #35 ---------------------------------------------------------------------------------

def test_age_is_per_index_row_when_a_person_has_several_index_events(truth_engine):
    # P19 (born 1960-05-05) has lisinopril on 2019-03-01 (age 58) and 2021-03-01 (age 60)
    def p19(demographics):
        result = rows(truth_engine, "demographics", index={"first_occurrence_only": False}, demographics=demographics)
        return sorted(d for p, d in result if p == 19)

    assert p19({"age_min": 60}) == [dt.date(2021, 3, 1)]
    assert p19({"age_min": 58, "age_max": 58}) == [dt.date(2019, 3, 1)]
    assert p19({"age_min": 59, "age_max": 59}) == []


def test_a_person_with_unknown_gender_never_matches_even_with_the_default_sex_list(truth_engine):
    insert_person = text("insert into cdm.person (person_id, gender_concept_id, year_of_birth, race_concept_id,"
                         " ethnicity_concept_id) values (901, 0, 1960, 0, 0)")
    insert_drug = text("insert into cdm.drug_exposure (drug_exposure_id, person_id, drug_concept_id,"
                       " drug_exposure_start_date, drug_exposure_end_date, drug_type_concept_id)"
                       " values (901, 901, 19080128, '2020-06-01', '2020-06-01', 32818)")
    plan = build_plan(definition())
    with truth_engine.connect() as conn:
        trans = conn.begin()
        try:
            conn.execute(insert_person)
            conn.execute(insert_drug)
            assert 901 in [r[0] for r in conn.execute(plan.rows("index")).fetchall()]
            assert 901 not in [r[0] for r in conn.execute(plan.rows("demographics")).fetchall()]
        finally:
            trans.rollback()


def test_without_descendants_a_clinical_drug_id_matches_exactly_that_concept(truth_engine):
    # 19080128 is 'lisinopril 10 MG Oral Tablet'; P2 has the 20 mg tablet and P3 the combination product
    only_10mg = persons(truth_engine, "index", index={"concept_ids": [19080128], "include_descendants": False})
    assert only_10mg == sorted(ALL_LISINOPRIL - {2, 3})


def test_several_concepts_are_combined_and_descendants_do_not_duplicate_rows(truth_engine):
    both = {"concept_ids": [LISINOPRIL, 1341927]}  # lisinopril and enalapril ingredients
    result = rows(truth_engine, "index", index=both)
    assert sorted(p for p, _ in result) == list(range(1, 24))  # everyone: P4 only has enalapril
    assert len(result) == 23  # still one row per person
    assert dict(result)[8] == dt.date(2020, 2, 21)  # P8's enalapril precedes the lisinopril: earliest of both
