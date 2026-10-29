"""Issue #9: small-cell suppression (NFR-4): 0 stays 0, 1..10 become "<11", 11 and above stay exact."""
import ast
import dataclasses
import json
import pathlib

import pytest

from app.cohort_engine.attrition import StepCount
from app.cohort_engine.suppression import (
    SMALL_CELL_LABEL,
    SMALL_CELL_THRESHOLD,
    PublicStepCount,
    suppress_count,
    suppress_steps,
)

MODULE = pathlib.Path(__file__).resolve().parents[2] / "app" / "cohort_engine" / "suppression.py"


# ---- Given a count of 0, then it is returned as 0 -----------------------------------------------------------------

def test_a_count_of_zero_is_returned_as_zero():
    assert suppress_count(0) == 0
    assert isinstance(suppress_count(0), int)


# ---- Given counts 1 through 10, then they are returned as "<11" ---------------------------------------------------

@pytest.mark.parametrize("count", range(1, 11))
def test_counts_one_through_ten_are_returned_as_less_than_eleven(count):
    assert suppress_count(count) == "<11"


# ---- Given a count of 11 or more, then the exact value is returned --------------------------------------------------

@pytest.mark.parametrize("count", [11, 12, 99, 100, 5724, 2_147_483_647])
def test_counts_of_eleven_or_more_are_returned_exactly(count):
    assert suppress_count(count) == count


def test_the_threshold_and_label_are_the_documented_ones():
    assert SMALL_CELL_THRESHOLD == 11 and SMALL_CELL_LABEL == "<11"


# ---- invalid input fails loudly instead of leaking or hiding a number ---------------------------------------------

@pytest.mark.parametrize("bad", [-1, -11])
def test_a_negative_count_is_rejected(bad):
    with pytest.raises(ValueError):
        suppress_count(bad)


@pytest.mark.parametrize("bad", [True, False, 1.0, 5.5, "7", None, [3]])
def test_only_real_integers_are_accepted(bad):
    with pytest.raises(TypeError):
        suppress_count(bad)


# ---- the attrition waterfall ----------------------------------------------------------------------------------------

STEPS = [StepCount("index", "First index event", 22), StepCount("demographics", "Age and sex", 21),
         StepCount("rule_1", "No ACE inhibitor", 15), StepCount("rule_2", "Hypertension", 12),
         StepCount("rule_3", "No prior heart failure", 10), StepCount("rule_4", "Nobody left", 0),
         StepCount("rule_5", "A single patient", 1)]


def test_every_step_is_suppressed_in_order_keeping_keys_and_labels():
    result = suppress_steps(STEPS)
    assert [(s.key, s.label, s.persons) for s in result] == [
        ("index", "First index event", 22), ("demographics", "Age and sex", 21), ("rule_1", "No ACE inhibitor", 15),
        ("rule_2", "Hypertension", 12), ("rule_3", "No prior heart failure", "<11"), ("rule_4", "Nobody left", 0),
        ("rule_5", "A single patient", "<11")]


def test_the_exact_small_numbers_do_not_appear_anywhere_in_the_serialised_response():
    body = json.dumps([dataclasses.asdict(s) for s in suppress_steps(STEPS)])
    assert '"persons": 10' not in body and '"persons": 1}' not in body and '"persons": 1,' not in body
    assert '"persons": "<11"' in body


def test_an_empty_waterfall_is_an_empty_list():
    assert suppress_steps([]) == []


# ---- Given any API response, then it contains no person-level identifiers --------------------------------------------

def test_a_public_step_holds_only_a_key_a_label_and_a_count():
    assert [f.name for f in dataclasses.fields(PublicStepCount)] == ["key", "label", "persons"]


def test_suppressing_never_mutates_the_exact_counts():
    before = [dataclasses.astuple(s) for s in STEPS]
    suppress_steps(STEPS)
    assert [dataclasses.astuple(s) for s in STEPS] == before


def test_the_module_is_pure_logic_with_no_database_or_sql_imports():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported = {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    imported |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not imported & {"sqlalchemy", "psycopg", "psycopg2", "fastapi"}


def test_a_public_step_cannot_be_changed_after_it_is_built():
    step = suppress_steps([StepCount("index", "First", 5)])[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        step.persons = 5



def test_a_run_is_recorded_with_a_public_baseline_only_never_the_exact_one(monkeypatch):
    """The stored baseline must be the suppressed `PublicBaseline`; a future change that passes the exact `Baseline`
    to `_record` must fail loudly instead of storing small cells."""
    import pytest

    from app import runs
    from app.cohort_engine.baseline import Baseline

    exact = Baseline(age_sex=[], comorbidities=[])
    with pytest.raises(TypeError):
        runs._record(None, "success", "trace", 1, [], exact)
