"""Seed integration test: the reference cohort reproduces its documented attrition counts.

Runs against a Postgres 18 database that has the committed seed loaded
(`make verify-seed` starts a throwaway container and sets COHORTLENS_TEST_DSN).
Skipped only when no DSN is provided (a missing psycopg install fails loudly).
"""
import os
import pathlib

import psycopg
import pytest

DSN = os.environ.get("COHORTLENS_TEST_DSN")
SQL = pathlib.Path(__file__).resolve().parents[3] / "data" / "etl" / "example_cohort.sql"

# docs/spec.md section 7 and docs/cohort-engine.md
EXPECTED = [920, 920, 919, 837, 829, 481]

pytestmark = pytest.mark.skipif(not DSN, reason="COHORTLENS_TEST_DSN not set (run `make verify-seed`)")


@pytest.fixture(scope="module")
def conn():
    with psycopg.connect(DSN) as c:
        yield c


def test_reference_cohort_attrition_matches_documented_counts(conn):
    rows = conn.execute(SQL.read_text(encoding="utf-8")).fetchall()
    assert [r[2] for r in rows] == EXPECTED


def test_seed_size_and_tables(conn):
    persons = conn.execute("select count(*) from cdm.person").fetchone()[0]
    assert persons == 5724
    for table in ("observation_period", "visit_occurrence", "condition_occurrence", "drug_exposure",
                  "measurement", "death", "concept", "concept_ancestor"):
        assert conn.execute(f"select count(*) from cdm.{table}").fetchone()[0] > 0, table


def test_descendant_expansion_is_preserved(conn):
    # lisinopril ingredient must expand to its clinical drugs; hypertension must expand to itself at least
    n_drugs = conn.execute(
        "select count(*) from cdm.concept_ancestor where ancestor_concept_id = 1308216").fetchone()[0]
    assert n_drugs >= 5
    n_htn = conn.execute(
        "select count(*) from cdm.concept_ancestor where ancestor_concept_id = 320128").fetchone()[0]
    assert n_htn >= 1


def test_database_size_within_budget(conn):
    size_mb = conn.execute("select pg_database_size(current_database())/1024.0/1024.0").fetchone()[0]
    assert size_mb < 250, f"{size_mb:.0f} MB exceeds the 250 MB budget"


def test_a_broad_all_events_cohort_within_the_cap_finishes_inside_the_statement_timeout(conn):
    """#37 criterion 2, one example: "Oral Product" (36217214) as the index with every exposure an index row (145,970
    rows) and the maximum 3 rules with min_occurrences 1 over +-100-year windows finishes within the 10 s statement
    timeout (about 1.6 s on a developer machine). This is NOT the worst allowed shape: the cap bounds work per index
    row, not the size of the index set or the window width (see docs/cohort-engine.md). The 5,568 final persons were
    captured from the engine and independently re-derived by the reviewer with separate SQL."""
    from sqlalchemy import create_engine, func, select

    from app.cohort_engine.compiler import build_plan
    from app.cohort_engine.models import MAX_RULES_ALL_EVENTS, parse_definition

    oral_product, clinical_finding = 36217214, 441840
    rules = [{"label": f"rule {n}", "type": "include", "domain": "drug" if n % 2 == 0 else "condition",
              "concept_ids": [oral_product if n % 2 == 0 else clinical_finding],
              "window": {"start_days": -36500, "end_days": 36500}} for n in range(MAX_RULES_ALL_EVENTS)]
    defn = parse_definition({"name": "all events", "rules": rules, "index_event": {
        "domain": "drug", "concept_ids": [oral_product], "first_occurrence_only": False}})
    last = build_plan(defn).steps[-1].cte
    engine = create_engine(DSN.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.connect() as connection:
        connection.execute(select(func.set_config("statement_timeout", "10000", True)))
        persons = connection.execute(select(func.count(func.distinct(last.c.person_id)))).scalar()
    engine.dispose()
    assert persons == 5568


def test_concept_search_answers_hypertens_inside_the_typeahead_budget_on_the_seed(monkeypatch):
    """#13: searching "hypertens" on the seeded vocabulary (26k concepts) returns matches within 500 ms."""
    import time

    from fastapi.testclient import TestClient

    from app.db import session
    from app.main import app

    monkeypatch.setenv("DATABASE_URL_READER", DSN)
    session.reader_engine.cache_clear()
    with TestClient(app) as client:
        timings = []
        for _ in range(3):  # the first call opens the connection
            started = time.perf_counter()
            response = client.get("/concepts", params={"q": "hypertens"})
            timings.append(time.perf_counter() - started)
    session.reader_engine().dispose()
    session.reader_engine.cache_clear()
    results = response.json()
    assert response.status_code == 200 and results
    assert all("hypertens" in concept["name"].lower() for concept in results)
    assert any(concept["concept_id"] == 320128 for concept in results)  # Essential hypertension
    assert min(timings) < 0.5, timings


def test_a_full_run_of_the_reference_cohort_on_the_seed_is_inside_the_latency_budget(monkeypatch):
    """#12 criterion 1: counts for every step, suppression and the baseline within 3 s on the demo dataset (NFR-1).
    This times `execute_run` (the database work) on the best of three runs; the endpoint adds only the definition
    load and the run insert on the small app tables.
    The step counts are the documented engine-order counts (920, 552, 552, 551, 489, 481)."""
    import json
    import time

    from app.cohort_engine.models import parse_definition
    from app.db import session
    from app.runs import execute_run

    reference = json.loads((pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "reference_cohort.json")
                           .read_text(encoding="utf-8"))
    monkeypatch.setenv("DATABASE_URL_READER", DSN)
    session.reader_engine.cache_clear()
    timings = []
    for _ in range(3):  # the first run opens the connection
        started = time.perf_counter()
        outcome = execute_run(parse_definition(reference), 10_000)
        timings.append(time.perf_counter() - started)
    session.reader_engine().dispose()
    session.reader_engine.cache_clear()
    assert [step.persons for step in outcome.steps] == [920, 552, 552, 551, 489, 481]
    assert outcome.final_count == 481
    assert outcome.baseline.comorbidities[0].name == "Essential hypertension"
    assert outcome.baseline.comorbidities[0].persons == 481
    assert min(timings) < 3.0, timings
