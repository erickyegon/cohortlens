"""Truth fixture: 20 hand-built patients with exact expected counts for every criterion type.

Loads api/tests/fixtures/truth_fixture.sql into a Postgres 16 Testcontainer and checks the reference
cohort SQL (the oracle the cohort engine must reproduce) against numbers written down independently
in the fixture generator and docs/testing.md.
"""
import pathlib
import re

import pytest

psycopg = pytest.importorskip("psycopg")
testcontainers_postgres = pytest.importorskip("testcontainers.postgres")

ROOT = pathlib.Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "api" / "tests" / "fixtures" / "truth_fixture.sql"
ORACLE = ROOT / "data" / "etl" / "example_cohort.sql"

# step-by-step expected counts: index, observation, washout, hypertension, no heart failure, age >= 50
EXPECTED_STEPS = [19, 17, 15, 12, 10, 9]
EXPECTED_FINAL_PERSONS = [1, 2, 3, 6, 9, 13, 15, 18, 20]
EXPECTED_DROPPED = {  # person_id -> step that removes them
    4: "index", 5: "observation", 7: "observation", 8: "washout", 10: "washout",
    11: "hypertension", 12: "hypertension", 19: "hypertension", 14: "heart failure", 16: "heart failure", 17: "age",
}


@pytest.fixture(scope="module")
def conn():
    with testcontainers_postgres.PostgresContainer("postgres:16") as pg:
        url = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        with psycopg.connect(url, autocommit=True) as c:
            c.execute(FIXTURE.read_text(encoding="utf-8"))
            yield c


def test_attrition_counts_match_hand_computed_expectations(conn):
    rows = conn.execute(ORACLE.read_text(encoding="utf-8")).fetchall()
    assert [r[2] for r in rows] == EXPECTED_STEPS


def test_final_cohort_members_are_exactly_the_expected_patients(conn):
    sql = ORACLE.read_text(encoding="utf-8")
    ctes = sql[: sql.index("SELECT 1 AS step")]
    persons = [r[0] for r in conn.execute(ctes + "SELECT person_id FROM age50 ORDER BY 1").fetchall()]
    assert persons == EXPECTED_FINAL_PERSONS


@pytest.mark.parametrize("person_id,step", sorted(EXPECTED_DROPPED.items()))
def test_each_boundary_patient_is_removed_at_the_expected_step(conn, person_id, step):
    sql = ORACLE.read_text(encoding="utf-8")
    ctes = sql[: sql.index("SELECT 1 AS step")]
    chain = ["idx", "obs", "washout", "htn", "no_hf", "age50"]
    labels = ["index", "observation", "washout", "hypertension", "heart failure", "age"]
    last_in = None
    for cte, label in zip(chain, labels):
        present = conn.execute(ctes + f"SELECT COUNT(*) FROM {cte} WHERE person_id = {person_id}").fetchone()[0]
        if present:
            last_in = label
        else:
            assert label == step, f"P{person_id} dropped at {label}, expected {step} (last present: {last_in})"
            return
    pytest.fail(f"P{person_id} was never removed")


def test_fixture_has_twenty_patients_and_no_patient_without_observation(conn):
    assert conn.execute("select count(*) from cdm.person").fetchone()[0] == 20
    assert conn.execute(
        "select count(*) from cdm.person p where not exists (select 1 from cdm.observation_period o where o.person_id = p.person_id)"
    ).fetchone()[0] == 0
    assert re.search(r"P1\d?:", FIXTURE.read_text(encoding="utf-8"))
