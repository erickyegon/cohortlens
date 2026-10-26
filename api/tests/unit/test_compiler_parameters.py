"""Issue #6: the compiled SQL uses bound parameters only (AGENTS.md hard rule 1; docs/cohort-engine.md).

No database needed: the statements are compiled with the PostgreSQL dialect and inspected.
"""
import ast
import pathlib
import re

import pytest
from sqlalchemy.dialects import postgresql

from app.cohort_engine.compiler import build_plan
from app.cohort_engine.models import parse_definition

COMPILER = pathlib.Path(__file__).resolve().parents[2] / "app" / "cohort_engine" / "compiler.py"
HOSTILE_NAME = "'; DROP TABLE cdm.person; --"


def definition(**index_overrides):
    index = {"domain": "drug", "concept_ids": [1308216], "date_range": {"start": "2015-01-01", "end": "2024-12-31"}}
    index.update(index_overrides)
    return parse_definition({
        "name": HOSTILE_NAME,
        "index_event": index,
        "demographics": {"age_min": 50, "age_max": 79, "sex": ["F"]},
    })


def compiled(plan, key):
    stmt = plan.rows(key).compile(dialect=postgresql.dialect())
    return str(stmt), stmt.params


@pytest.mark.parametrize("key", ["index", "demographics"])
@pytest.mark.parametrize("domain,concept", [("drug", 1308216), ("condition", 320128)])
def test_values_are_bound_parameters_not_sql_text(key, domain, concept):
    plan = build_plan(definition(domain=domain, concept_ids=[concept]))
    sql, params = compiled(plan, key)
    assert str(concept) not in re.sub(r"\bcdm\.\w+", "", sql)  # the id appears only as a parameter
    assert "2015-01-01" not in sql and "2024-12-31" not in sql
    flat = repr(list(params.values()))
    assert str(concept) in flat
    assert "%(" in sql or "POSTCOMPILE" in sql


def test_demographic_values_are_parameters_too():
    sql, params = compiled(build_plan(definition()), "demographics")
    assert "'F'" not in sql and "8532" not in sql
    values = list(params.values())
    assert [8532] in values  # female, as the OMOP gender concept, bound rather than written into the SQL
    assert 50 in values and 79 in values


def test_user_text_never_reaches_the_sql():
    # regression guard: the compiler must keep ignoring the definition's name and (later) rule labels
    plan = build_plan(definition())
    for key in ("index", "demographics"):
        sql, params = compiled(plan, key)
        assert "DROP TABLE" not in sql and HOSTILE_NAME not in repr(params)


def test_only_cdm_tables_are_read():
    sql, _ = compiled(build_plan(definition()), "demographics")
    qualified = set(re.findall(r"\bcdm\.(\w+)", sql))
    assert qualified <= {"person", "drug_exposure", "condition_occurrence", "concept_ancestor"}, qualified
    # every dotted name is either in schema cdm or one of our own CTEs: nothing from another schema
    prefixes = set(re.findall(r"\b([a-z_]+)\.[a-z_]+", sql))
    assert prefixes <= {"cdm", "index_events", "demographics"}, prefixes


def test_steps_are_ordered_and_labelled():
    plan = build_plan(definition())
    assert [s.key for s in plan.steps] == ["index", "demographics", "observation"]
    assert all(s.label for s in plan.steps)


def test_compiler_source_has_no_escape_hatches_for_raw_sql():
    """AST check (not a substring scan): no text()/literal_column(), no string formatting, no f-strings."""
    tree = ast.parse(COMPILER.read_text(encoding="utf-8"))
    banned_names = {"text", "literal_column", "literal", "exec_driver_sql"}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert not (banned_names & (names | attrs | imported)), banned_names & (names | attrs | imported)
    assert "literal_binds" not in names | attrs and "format" not in attrs
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr)], "f-strings are not allowed in the compiler"
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mod)
                and isinstance(n.left, ast.Constant) and isinstance(n.left.value, str)], "no %-formatting"


# ---- observation window and rules (issue #7) ------------------------------------------------------------------

def rules_definition():
    return parse_definition({
        "name": HOSTILE_NAME,
        "index_event": {"domain": "drug", "concept_ids": [1308216]},
        "observation": {"prior_days": 365, "post_days": 30},
        "rules": [
            {"label": "'; DROP TABLE cdm.person; -- label", "type": "exclude", "domain": "drug",
             "concept_ids": [21601784], "window": {"start_days": -365, "end_days": -1}},
            {"label": "Twice", "type": "include", "domain": "condition", "concept_ids": [320128],
             "include_descendants": False, "window": {"start_days": -36500, "end_days": 0}, "min_occurrences": 2},
        ],
    })


def test_steps_include_observation_and_one_keyed_step_per_rule_in_order():
    plan = build_plan(rules_definition())
    assert [s.key for s in plan.steps] == ["index", "demographics", "observation", "rule_1", "rule_2"]
    assert plan.step("rule_1").label.startswith("'; DROP TABLE")  # the label is display text only
    assert plan.step("rule_2").label == "Twice"
    assert plan.step("observation").label


@pytest.mark.parametrize("key", ["observation", "rule_1", "rule_2"])
def test_observation_and_rule_values_are_bound_parameters(key):
    sql, params = compiled(build_plan(rules_definition()), key)
    stripped = re.sub(r"\bcdm\.\w+", "", sql)
    for literal in ("21601784", "320128", "1308216", "365", "36500"):
        assert literal not in stripped, literal
    values = repr(list(params.values()))
    assert "365" in values and "30" in values


def test_rule_labels_and_names_never_reach_the_sql_or_its_parameters():
    plan = build_plan(rules_definition())
    for step in plan.steps:
        sql, params = compiled(plan, step.key)
        assert "DROP TABLE" not in sql and "DROP TABLE" not in repr(params)


def test_all_tables_read_by_rule_steps_are_cdm_tables():
    sql, _ = compiled(build_plan(rules_definition()), "rule_2")
    qualified = set(re.findall(r"\bcdm\.(\w+)", sql))
    assert qualified <= {"person", "drug_exposure", "condition_occurrence", "concept_ancestor", "observation_period"}
    assert {"observation_period", "condition_occurrence"} <= qualified
    prefixes = set(re.findall(r"\b([a-z_]+)\.[a-z_]+", sql))
    assert prefixes <= {"cdm", "index_events", "demographics", "observation", "rule_1", "rule_2"}, prefixes
