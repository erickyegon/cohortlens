"""Issue #37 on the committed seed (5,724 persons): the worst realistic cohort runs inside the latency budget.

Runs against a Postgres 18 database with the committed seed loaded (`make verify-seed` sets COHORTLENS_TEST_DSN).
The expected step counts were captured from the engine BEFORE the performance change (results must not change, only
the plan); the reference cohort's counts also match the SQL oracle (920 / 552 / 552 / 551 / 489 / 481 in engine order).

Worst case of criterion 1: 10 rules with `min_occurrences: 2` on broad concepts ("Oral Product" 36217214 drugs and
"Clinical finding" 441840 conditions), first occurrence only, every step counted in one statement. Before the change
this took 5.9-8.8 s on a developer machine.
"""
import json
import os
import pathlib
import time

import pytest
from sqlalchemy import create_engine

from app.cohort_engine.attrition import run_attrition
from app.cohort_engine.compiler import build_plan
from app.cohort_engine.models import parse_definition

DSN = os.environ.get("COHORTLENS_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="COHORTLENS_TEST_DSN not set (run `make verify-seed`)")

REFERENCE = json.loads((pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "reference_cohort.json")
                       .read_text(encoding="utf-8"))
ORAL_PRODUCT, CLINICAL_FINDING = 36217214, 441840
BUDGET_SECONDS = 3.0


@pytest.fixture(scope="module")
def engine():
    engine = create_engine(DSN.replace("postgresql://", "postgresql+psycopg://", 1))
    yield engine
    engine.dispose()


def broad_cohort(rule_count, min_occurrences):
    rules = []
    for n in range(rule_count):
        drug = n % 2 == 0
        rules.append({"label": f"rule {n}", "type": "include", "domain": "drug" if drug else "condition",
                      "concept_ids": [ORAL_PRODUCT if drug else CLINICAL_FINDING], "include_descendants": True,
                      "window": {"start_days": -36500, "end_days": 36500}, "min_occurrences": min_occurrences})
    return parse_definition({"name": "broad", "index_event": {"domain": "drug", "concept_ids": [ORAL_PRODUCT]},
                             "rules": rules})


def timed_counts(engine, defn, runs=4):
    """Counts and the best of `runs` wall-clock times.

    The first run warms the cache; a noisy CI neighbour must slow every run to fail.
    """
    best, counts = None, None
    for _ in range(runs):
        with engine.connect() as conn:
            start = time.perf_counter()
            counts = [c.persons for c in run_attrition(conn, build_plan(defn), timeout_ms=60_000)]
            elapsed = time.perf_counter() - start
        best = elapsed if best is None else min(best, elapsed)
    return counts, best


def test_ten_rules_needing_two_occurrences_on_broad_concepts_finish_under_three_seconds(engine):
    counts, seconds = timed_counts(engine, broad_cohort(10, 2))
    assert counts == [5577, 5577, 5577, 5376] + [5347] * 9  # captured before the change; results must not change
    assert seconds < BUDGET_SECONDS, f"{seconds:.2f} s"


def test_ten_rules_needing_one_occurrence_on_broad_concepts_stay_fast(engine):
    counts, seconds = timed_counts(engine, broad_cohort(10, 1))
    assert counts == [5577] * 4 + [5568] * 9
    assert seconds < BUDGET_SECONDS, f"{seconds:.2f} s"


def test_the_reference_cohort_chain_still_counts_in_under_half_a_second(engine):
    counts, seconds = timed_counts(engine, parse_definition(REFERENCE))
    assert counts == [920, 552, 552, 551, 489, 481]
    assert seconds < 0.5, f"{seconds:.2f} s"
