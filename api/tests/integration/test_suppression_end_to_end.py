"""Issue #9 on the truth fixture: exact engine counts in, masked counts out (docs/cohort-engine.md)."""
import json
import pathlib

from app.cohort_engine.attrition import run_attrition
from app.cohort_engine.compiler import build_plan
from app.cohort_engine.models import parse_definition
from app.cohort_engine.suppression import suppress_steps

REFERENCE = json.loads((pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "reference_cohort.json")
                       .read_text(encoding="utf-8"))


def test_reference_cohort_final_count_of_ten_is_masked_while_the_larger_steps_stay_exact(truth_engine):
    with truth_engine.connect() as conn:
        exact = run_attrition(conn, build_plan(parse_definition(REFERENCE)))
    assert [s.persons for s in exact] == [22, 21, 17, 15, 12, 10]  # hand-derived, see test_attrition_counts.py
    assert [s.persons for s in suppress_steps(exact)] == [22, 21, 17, 15, 12, "<11"]
