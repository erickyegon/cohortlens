"""Issue #37: exact `min_occurrences` boundaries and per-person correlation, on hand-built extra patients.

Three extra men (born 1960-01-01, observed 2015-2023) are added inside a rolled-back transaction. Each has a
lisinopril (19080128) index on 2020-06-01 and then LATER lisinopril records inside a 1..1000 day window:
person 201 has 2 later records, 202 has 3, 203 has 7. The fixture's own patients have no later records except P19,
who has one (2021-03-01 after her 2019-03-01 index). Counting stops after k records, so the boundaries k-1, k and
more than k, and the guarantee that one person's many records never satisfy another person's rule, are what this
file pins down.
"""
import datetime as dt

from sqlalchemy import text

from app.cohort_engine.compiler import build_plan
from app.cohort_engine.models import parse_definition

LATER = {201: 2, 202: 3, 203: 7}
START = dt.date(2020, 6, 1)


def definition(type_, k):
    rule = {"label": "r", "type": type_, "domain": "drug", "concept_ids": [1308216],
            "window": {"start_days": 1, "end_days": 1000}, "min_occurrences": k}
    return parse_definition({"name": "t", "index_event": {"domain": "drug", "concept_ids": [1308216]},
                             "rules": [rule]})


def add_people(conn):
    drug_id = 9000
    for person, later in LATER.items():
        conn.execute(text("insert into cdm.person (person_id, gender_concept_id, year_of_birth, month_of_birth,"
                          " day_of_birth, race_concept_id, ethnicity_concept_id) values (:p, 8507, 1960, 1, 1, 0, 0)"),
                     {"p": person})
        conn.execute(text("insert into cdm.observation_period values (:p, :p, '2015-01-01', '2023-12-31', 32817)"),
                     {"p": person})
        for n in range(later + 1):  # n = 0 is the index record
            drug_id += 1
            conn.execute(text("insert into cdm.drug_exposure (drug_exposure_id, person_id, drug_concept_id,"
                              " drug_exposure_start_date, drug_exposure_end_date, drug_type_concept_id)"
                              " values (:i, :p, 19080128, :d, :d, 32818)"),
                         {"i": drug_id, "p": person, "d": START + dt.timedelta(days=n * 10)})


def kept(engine, defn):
    plan = build_plan(defn)
    with engine.connect() as conn:
        add_people(conn)
        persons = sorted({r[0] for r in conn.execute(plan.rows("rule_1")).fetchall()})
        conn.rollback()
    return persons


def test_exactly_k_records_satisfy_a_rule_and_k_minus_one_do_not(truth_engine):
    assert kept(truth_engine, definition("include", 3)) == [202, 203]  # 2 later records is not enough, 3 is


def test_more_than_k_records_satisfy_the_rule_too(truth_engine):
    assert kept(truth_engine, definition("include", 2)) == [201, 202, 203]


def test_a_rule_needing_more_records_than_anyone_has_keeps_nobody(truth_engine):
    assert kept(truth_engine, definition("include", 8)) == []
    assert kept(truth_engine, definition("include", 7)) == [203]


def test_one_persons_many_records_never_satisfy_another_persons_rule(truth_engine):
    # person 201 has only 2 later records although 202 and 203 have 3 and 7 and the fixture has 20 more people
    assert 201 not in kept(truth_engine, definition("include", 3))
    assert kept(truth_engine, definition("include", 100)) == []


def test_exclude_with_k_records_removes_exactly_those_with_k_or_more(truth_engine):
    everyone = (set(range(1, 24)) - {4, 22}) | set(LATER)  # P4 has no lisinopril; P22's observation ended
    removed = everyone - set(kept(truth_engine, definition("exclude", 3)))
    assert removed == {202, 203}
