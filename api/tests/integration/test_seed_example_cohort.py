"""Seed integration test: the reference cohort reproduces its documented attrition counts.

Runs against a Postgres 16 database that has the committed seed loaded
(`make verify-seed` starts a throwaway container and sets COHORTLENS_TEST_DSN).
Skipped when no DSN is provided so `pytest` stays runnable without Docker.
"""
import os
import pathlib

import pytest

psycopg = pytest.importorskip("psycopg")

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
