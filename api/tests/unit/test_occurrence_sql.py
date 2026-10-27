"""Issue #37: a rule needing k occurrences stops counting after k matching records (docs/cohort-engine.md, Performance).

The change is to the plan only; results must not change (see test_occurrence_early_exit.py and the seed test).
"""
from sqlalchemy.dialects import postgresql

from app.cohort_engine.compiler import build_plan
from app.cohort_engine.models import parse_definition


def sql_of(min_occurrences):
    rule = {"label": "r", "type": "include", "domain": "drug", "concept_ids": [1308216],
            "window": {"start_days": 0, "end_days": 1000}, "min_occurrences": min_occurrences}
    defn = parse_definition({"name": "t", "index_event": {"domain": "drug", "concept_ids": [19080128]},
                             "rules": [rule]})
    compiled = build_plan(defn).step("rule_1").cte.compile(dialect=postgresql.dialect())
    return str(compiled), compiled.params


def test_a_rule_needing_several_occurrences_counts_at_most_that_many_records():
    sql, params = sql_of(3)
    assert "LIMIT" in sql.upper()
    assert 3 in params.values()  # the limit is a bound parameter equal to min_occurrences, not text in the SQL


def test_the_limit_is_a_bound_parameter_never_part_of_the_sql_text():
    sql, _ = sql_of(7)
    assert "LIMIT 7" not in sql.upper()


def test_a_rule_needing_one_occurrence_is_still_a_plain_exists_probe_without_a_limit():
    sql, _ = sql_of(1)
    assert "EXISTS" in sql.upper() and "LIMIT" not in sql.upper()


def test_the_occurrence_count_stays_correlated_to_the_index_row():
    sql, _ = sql_of(2)
    # the inner select must reference the previous step's person, not count every person's records
    assert "observation.person_id" in sql and sql.count("FROM observation") == 1
