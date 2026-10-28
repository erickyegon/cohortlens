"""Truth fixture: 23 hand-built patients with exact expected counts for every criterion type.

Loads api/tests/fixtures/truth_fixture.sql into a Postgres 18 Testcontainer and checks the reference
cohort SQL (the oracle the cohort engine must reproduce) against numbers written down independently
in the fixture generator and docs/testing.md.

Dependencies and Docker are required; a missing dependency or Docker daemon fails the run rather than
skipping it (see api/requirements-dev.txt).
"""
import pathlib
import re
import sys

import psycopg
import pytest
from testcontainers.community.postgres import PostgresContainer

ROOT = pathlib.Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "api" / "tests" / "fixtures" / "truth_fixture.sql"
ORACLE = ROOT / "data" / "etl" / "example_cohort.sql"

# step-by-step expected counts: index, observation, washout, hypertension, no heart failure, age >= 50
EXPECTED_STEPS = [22, 18, 16, 13, 11, 10]
EXPECTED_FINAL_PERSONS = [1, 2, 3, 6, 9, 13, 15, 18, 20, 21]
EXPECTED_DROPPED = {  # person_id -> step that removes them
    4: "index", 5: "observation", 7: "observation", 22: "observation", 23: "observation",
    8: "washout", 10: "washout",
    11: "hypertension", 12: "hypertension", 19: "hypertension",
    14: "heart failure", 16: "heart failure", 17: "age",
}

# The drop-step test depends on the CTE names in data/etl/example_cohort.sql, in order.
CHAIN = [("idx", "index"), ("obs", "observation"), ("washout", "washout"),
         ("htn", "hypertension"), ("no_hf", "heart failure"), ("age50", "age")]


def oracle_ctes() -> str:
    sql = ORACLE.read_text(encoding="utf-8")
    return sql[: sql.index("SELECT 1 AS step")]


@pytest.fixture(scope="module")
def conn():
    with PostgresContainer("postgres:18") as pg:
        url = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        with psycopg.connect(url, autocommit=True) as c:
            c.execute(FIXTURE.read_text(encoding="utf-8"))
            yield c


def test_attrition_counts_match_hand_computed_expectations(conn):
    rows = conn.execute(ORACLE.read_text(encoding="utf-8")).fetchall()
    assert [r[2] for r in rows] == EXPECTED_STEPS


def test_final_cohort_members_are_exactly_the_expected_patients(conn):
    persons = [r[0] for r in conn.execute(oracle_ctes() + "SELECT person_id FROM age50 ORDER BY 1").fetchall()]
    assert persons == EXPECTED_FINAL_PERSONS


@pytest.mark.parametrize("person_id,step", sorted(EXPECTED_DROPPED.items()))
def test_each_boundary_patient_is_removed_at_the_expected_step(conn, person_id, step):
    ctes = oracle_ctes()
    for cte, label in CHAIN:
        present = conn.execute(ctes + f"SELECT COUNT(*) FROM {cte} WHERE person_id = {int(person_id)}").fetchone()[0]
        if not present:
            assert label == step, f"P{person_id} dropped at {label}, expected {step}"
            return
    pytest.fail(f"P{person_id} was never removed")


def test_every_patient_is_either_expected_final_or_expected_dropped(conn):
    persons = [r[0] for r in conn.execute("select person_id from cdm.person order by 1").fetchall()]
    assert len(persons) == 23
    assert set(persons) == set(EXPECTED_FINAL_PERSONS) | set(EXPECTED_DROPPED)
    for pid in persons:  # each patient is documented in the generated SQL
        assert re.search(rf"^-- P{pid}: .+", FIXTURE.read_text(encoding="utf-8"), re.M), pid


def test_every_patient_has_observation(conn):
    missing = conn.execute(
        "select count(*) from cdm.person p where not exists "
        "(select 1 from cdm.observation_period o where o.person_id = p.person_id)").fetchone()[0]
    assert missing == 0


def test_fixture_columns_match_the_etl_output_columns(conn):
    """Schema drift guard: the columns the ETL writes into the seed must be exactly the columns of the fixture DDL."""
    sys.path.insert(0, str(ROOT / "data" / "etl"))
    import synthea_to_omop

    tables = {r[0] for r in conn.execute(
        "select table_name from information_schema.tables where table_schema='cdm'").fetchall()}
    assert tables == set(synthea_to_omop.TABLES)
    for table, columns in synthea_to_omop.TABLES.items():
        actual = [r[0] for r in conn.execute(
            "select column_name from information_schema.columns where table_schema='cdm' and table_name=%s "
            "order by ordinal_position", (table,)).fetchall()]
        assert actual == columns, table
