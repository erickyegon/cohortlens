"""Issue #7: observation window, and rules with day windows, occurrences and descendants (docs/cohort-engine.md).

All expected values were derived by hand from api/tests/fixtures/build_truth_fixture.py (index date 2020-06-01 for
almost everyone; 2020 is a leap year so 365 days before 2020-06-01 is 2019-06-02):
- observation: P5 112 days, P7 364, P6 exactly 365, P22 ends the day before the index, P23 has a gap (its second
  period is 102 days old), P20 is observed until 2022-02-01 (610 days after the index)
- washout: enalapril 101 days before (P8), exactly 365 (P10), 401 (P9)
- hypertension (320128, child 317898): none (P11), the day after the index (P12), on the index date (P13),
  after P19's first lisinopril
- heart failure (316139, child 4229440): before (P14, P16 via the child), on the index date (P21), after (P15)
"""
import datetime as dt
import json
import pathlib

import pytest

from app.cohort_engine.compiler import build_plan
from app.cohort_engine.models import parse_definition

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"
REFERENCE = json.loads((FIXTURES / "reference_cohort.json").read_text(encoding="utf-8"))
ALL_LISINOPRIL = set(range(1, 24)) - {4}  # 22 persons; P4 only has enalapril


def definition(rules=None, observation=None, demographics=None, index=None):
    index_event = {"domain": "drug", "concept_ids": [1308216]}
    index_event.update(index or {})
    return parse_definition({
        "name": "t", "index_event": index_event, "demographics": demographics or {},
        "observation": observation or {}, "rules": rules or [],
    })


def rule(label, type_, domain, concepts, start, end, **extra):
    return {"label": label, "type": type_, "domain": domain, "concept_ids": concepts,
            "window": {"start_days": start, "end_days": end}, **extra}


def persons(engine, defn, key):
    plan = build_plan(defn)
    with engine.connect() as conn:
        return sorted({r[0] for r in conn.execute(plan.rows(key)).fetchall()})


# ---- observation window --------------------------------------------------------------------------------------

def test_default_observation_requires_the_index_date_to_fall_inside_an_observation_period(truth_engine):
    # only P22 (observation ends the day before its index) is outside an observation period
    assert persons(truth_engine, definition(), "observation") == sorted(ALL_LISINOPRIL - {22})


def test_prior_observation_of_365_days_is_inclusive_at_the_boundary(truth_engine):
    kept = persons(truth_engine, definition(observation={"prior_days": 365}), "observation")
    assert 6 in kept  # exactly 365 days
    assert not {5, 7, 22, 23} & set(kept)  # 112, 364, ends before index, gap
    assert kept == sorted(ALL_LISINOPRIL - {5, 7, 22, 23})


def test_a_gap_in_observation_does_not_count_as_observed_time(truth_engine):
    # P23's second observation period started 102 days before the index; the first ended in 2019
    assert 23 in persons(truth_engine, definition(observation={"prior_days": 102}), "observation")
    assert 23 not in persons(truth_engine, definition(observation={"prior_days": 103}), "observation")


def test_post_index_observation_follows_the_same_period(truth_engine):
    # P20 is observed until 2022-02-01, 610 days after 2020-06-01
    assert 20 in persons(truth_engine, definition(observation={"post_days": 610}), "observation")
    assert 20 not in persons(truth_engine, definition(observation={"post_days": 611}), "observation")


def test_observation_only_removes_people(truth_engine):
    before = set(persons(truth_engine, definition(demographics={"age_min": 50}), "demographics"))
    after = set(persons(truth_engine, definition(demographics={"age_min": 50}, observation={"prior_days": 365}),
                        "observation"))
    assert after < before


# ---- rules: windows, include/exclude ---------------------------------------------------------------------------

def test_exclude_washout_removes_prior_exposure_inside_the_window_only(truth_engine):
    ace = rule("No ACE inhibitor in the prior year", "exclude", "drug", [21601784], -365, -1)
    kept = persons(truth_engine, definition(rules=[ace]), "rule_1")
    assert not {8, 10} & set(kept)  # enalapril 101 days before, and exactly 365 days before (inclusive)
    assert 9 in kept  # enalapril 401 days before is outside the window
    assert kept == sorted(ALL_LISINOPRIL - {22, 8, 10})  # P22 is removed by the observation step before it


def test_include_requires_a_match_inside_the_window_and_the_window_includes_the_index_date(truth_engine):
    htn = rule("Hypertension on or before index", "include", "condition", [320128], -36500, 0)
    kept = persons(truth_engine, definition(rules=[htn]), "rule_1")
    assert 13 in kept  # diagnosed on the index date: window end 0 is inclusive
    assert 2 in kept  # only the child concept 317898, found through descendants
    assert not {11, 12, 19} & set(kept)  # none; the day after; after P19's *first* lisinopril
    assert kept == sorted(ALL_LISINOPRIL - {22, 11, 12, 19})


def test_exclude_before_index_does_not_remove_events_on_or_after_the_index_date(truth_engine):
    hf = rule("No prior heart failure", "exclude", "condition", [316139], -36500, -1)
    kept = persons(truth_engine, definition(rules=[hf]), "rule_1")
    assert not {14, 16} & set(kept)  # 16 only has the child concept 4229440
    assert {15, 21} <= set(kept)  # after the index, and on the index date (window ends the day before)
    assert kept == sorted(ALL_LISINOPRIL - {22, 14, 16})


def test_a_window_that_looks_after_the_index_date(truth_engine):
    # heart failure on the index date or later: P15 (2021-01-01) and P21 (the index date itself)
    hf_after = rule("Heart failure afterwards", "include", "condition", [316139], 0, 36500)
    assert persons(truth_engine, definition(rules=[hf_after]), "rule_1") == [15, 21]


def test_descendants_can_be_turned_off_for_a_rule(truth_engine):
    child_only = rule("HF", "include", "condition", [316139], -36500, -1)
    assert persons(truth_engine, definition(rules=[child_only]), "rule_1") == [14, 16]
    exact = rule("HF", "include", "condition", [316139], -36500, -1, include_descendants=False)
    assert persons(truth_engine, definition(rules=[exact]), "rule_1") == [14]  # P16 only has the child concept


def test_min_occurrences_counts_the_records_inside_the_window(truth_engine):
    # P19 has lisinopril on 2019-03-01 (her index) and 2021-03-01, 731 days later; everyone else has one record
    twice = rule("Two exposures", "include", "drug", [1308216], 0, 1000, min_occurrences=2)
    assert persons(truth_engine, definition(rules=[twice]), "rule_1") == [19]
    too_short = rule("Two exposures", "include", "drug", [1308216], 0, 700, min_occurrences=2)
    assert persons(truth_engine, definition(rules=[too_short]), "rule_1") == []
    once = rule("One exposure", "include", "drug", [1308216], 0, 700, min_occurrences=1)
    assert persons(truth_engine, definition(rules=[once]), "rule_1") == sorted(ALL_LISINOPRIL - {22})


def test_an_exclude_rule_with_min_occurrences_only_removes_people_with_enough_records(truth_engine):
    twice = rule("Repeat use", "exclude", "drug", [1308216], 0, 1000, min_occurrences=2)
    assert persons(truth_engine, definition(rules=[twice]), "rule_1") == sorted(ALL_LISINOPRIL - {22, 19})


def test_rules_are_evaluated_against_each_index_row_when_a_person_has_several(truth_engine):
    # with every exposure as an index event, P19 has two rows; hypertension (2020-01-01) is after the first one only
    htn = rule("Hypertension before index", "include", "condition", [320128], -36500, 0)
    plan = build_plan(definition(rules=[htn], index={"first_occurrence_only": False}))
    with truth_engine.connect() as conn:
        rows = [(r[0], r[1]) for r in conn.execute(plan.rows("rule_1")).fetchall() if r[0] == 19]
    assert rows == [(19, dt.date(2021, 3, 1))]


def test_rules_chain_in_order_and_only_ever_remove_people(truth_engine):
    rules = [
        rule("Washout", "exclude", "drug", [21601784], -365, -1),
        rule("Hypertension", "include", "condition", [320128], -36500, 0),
        rule("No heart failure", "exclude", "condition", [316139], -36500, -1),
    ]
    defn = definition(rules=rules)
    sizes = [len(persons(truth_engine, defn, key)) for key in ("index", "demographics", "observation", "rule_1",
                                                               "rule_2", "rule_3")]
    assert sizes == [22, 22, 21, 19, 16, 14]
    assert sizes == sorted(sizes, reverse=True)


# ---- the reference cohort, end to end ---------------------------------------------------------------------------

def test_reference_cohort_matches_the_sql_oracle_on_the_truth_fixture(truth_engine):
    defn = parse_definition(REFERENCE)
    keys = [s.key for s in build_plan(defn).steps]
    assert keys == ["index", "demographics", "observation", "rule_1", "rule_2", "rule_3"]
    sizes = [len(persons(truth_engine, defn, key)) for key in keys]
    assert sizes == [22, 21, 17, 15, 12, 10]  # the engine's order; the oracle orders the steps differently
    # same final cohort as data/etl/example_cohort.sql / docs/testing.md
    assert persons(truth_engine, defn, "rule_3") == [1, 2, 3, 6, 9, 13, 15, 18, 20, 21]


@pytest.mark.parametrize("domain,concept,expected", [("drug", 19080128, True), ("condition", 320128, True)])
def test_both_domains_work_as_rule_domains(truth_engine, domain, concept, expected):
    r = rule("x", "include", domain, [concept], -36500, 36500)
    assert bool(persons(truth_engine, definition(rules=[r]), "rule_1")) is expected
