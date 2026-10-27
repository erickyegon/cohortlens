"""Issue #8: the count after every step from ONE statement (docs/cohort-engine.md, "Attrition counts").

Expected values were derived by hand from api/tests/fixtures/build_truth_fixture.py and match docs/testing.md:
the reference cohort has 22 lisinopril users; 21 after age/sex (P17 is 49), 17 after observation (P5, P7, P22, P23
out), then 15 / 12 / 10 after its three rules (washout P8 P10, hypertension P11 P12 P19, heart failure P14 P16).
"""
import dataclasses
import json
import pathlib

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError

from app.cohort_engine.attrition import CohortTimeout, StepCount, run_attrition, run_statement
from app.cohort_engine.compiler import build_plan
from app.cohort_engine.models import parse_definition

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"
REFERENCE = json.loads((FIXTURES / "reference_cohort.json").read_text(encoding="utf-8"))


def definition(rules=None, index=None, demographics=None):
    index_event = {"domain": "drug", "concept_ids": [1308216], **(index or {})}
    return parse_definition({"name": "t", "index_event": index_event, "demographics": demographics or {},
                             "rules": rules or []})


def rule(label, type_, domain, concepts, start, end):
    return {"label": label, "type": type_, "domain": domain, "concept_ids": concepts,
            "window": {"start_days": start, "end_days": end}}


def attrition(engine, defn):
    with engine.connect() as conn:
        return run_attrition(conn, build_plan(defn))


# ---- Given an index and two rules, counts for index, demographics, observation, each rule, in order ---------------

def test_counts_for_index_demographics_observation_and_each_rule_come_back_in_order(truth_engine):
    defn = definition(rules=[rule("No prior heart failure", "exclude", "condition", [316139], -36500, -1),
                             rule("Hypertension before", "include", "condition", [320128], -36500, 0)])
    result = attrition(truth_engine, defn)
    assert [c.key for c in result] == ["index", "demographics", "observation", "rule_1", "rule_2"]
    assert [c.label for c in result][3:] == ["No prior heart failure", "Hypertension before"]
    assert all(isinstance(c, StepCount) for c in result)


# ---- Given the truth fixture, counts equal the documented values exactly ---------------------------------------------

def test_reference_cohort_counts_equal_the_documented_values_exactly(truth_engine):
    result = attrition(truth_engine, parse_definition(REFERENCE))
    assert [(c.key, c.persons) for c in result] == [
        ("index", 22), ("demographics", 21), ("observation", 17), ("rule_1", 15), ("rule_2", 12), ("rule_3", 10)]


def test_counts_are_distinct_persons_even_with_several_index_events_per_person(truth_engine):
    # 23 index rows for 22 persons when every lisinopril date counts (P19 has two)
    result = attrition(truth_engine, definition(index={"first_occurrence_only": False}))
    assert result[0].persons == 22


def test_a_step_that_nobody_reaches_is_reported_as_zero_not_omitted(truth_engine):
    nobody = rule("Never", "include", "condition", [999999], -36500, 36500)
    result = attrition(truth_engine, definition(rules=[nobody]))
    assert [(c.key, c.persons) for c in result] == [
        ("index", 22), ("demographics", 22), ("observation", 21), ("rule_1", 0)]


def test_counts_never_increase_from_one_step_to_the_next(truth_engine):
    counts = [c.persons for c in attrition(truth_engine, parse_definition(REFERENCE))]
    assert counts == sorted(counts, reverse=True)


# ---- one round trip ----------------------------------------------------------------------------------------------

def test_every_step_is_counted_in_a_single_cohort_statement(truth_engine):
    seen = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(truth_engine, "before_cursor_execute", record)
    try:
        attrition(truth_engine, parse_definition(REFERENCE))
    finally:
        event.remove(truth_engine, "before_cursor_execute", record)
    cohort_statements = [s for s in seen if "cdm." in s]
    assert len(cohort_statements) == 1
    assert cohort_statements[0].upper().count("WITH") == 1  # one CTE chain, each step defined once
    assert cohort_statements[0].count("index_events AS") == 1


# ---- Given a query exceeding the timeout, it is cancelled and reported as a timeout ----------------------------------

def test_a_query_that_exceeds_the_timeout_is_cancelled_and_raises_cohort_timeout(truth_engine):
    with truth_engine.connect() as conn:
        with pytest.raises(CohortTimeout):
            run_statement(conn, select(func.pg_sleep(5)), timeout_ms=100)


def test_the_connection_is_usable_after_a_timeout(truth_engine):
    with truth_engine.connect() as conn:
        with pytest.raises(CohortTimeout):
            run_statement(conn, select(func.pg_sleep(5)), timeout_ms=100)
        assert [c.persons for c in run_attrition(conn, build_plan(definition()))][0] == 22


def test_the_timeout_applies_to_the_transaction_only_and_defaults_to_ten_seconds(truth_engine):
    import inspect
    assert inspect.signature(run_attrition).parameters["timeout_ms"].default == 10_000
    with truth_engine.connect() as conn:
        before = conn.exec_driver_sql("show statement_timeout").scalar()
        run_attrition(conn, build_plan(definition()), timeout_ms=1234)
        conn.commit()  # a session-level SET would survive this; a transaction-local one must not
        assert conn.exec_driver_sql("show statement_timeout").scalar() == before


def test_an_operational_error_that_is_not_a_timeout_propagates_as_itself(truth_engine):
    # the backend is terminated mid-statement (SQLSTATE 57P01): a lost connection must not be reported as a timeout
    with truth_engine.connect() as conn:
        with pytest.raises(OperationalError) as caught:
            run_statement(conn, select(func.pg_terminate_backend(func.pg_backend_pid())), timeout_ms=5000)
    assert not isinstance(caught.value, CohortTimeout)

# ---- aggregates only ----------------------------------------------------------------------------------------------

def test_a_step_count_holds_only_a_key_a_label_and_a_number():
    assert [f.name for f in dataclasses.fields(StepCount)] == ["key", "label", "persons"]
