"""Issues #2 and #3: the database init scripts, the Alembic app schema and the two-role privilege model.

Starts a Postgres 16 container with the real files mounted into /docker-entrypoint-initdb.d (roles script,
committed OMOP seed, grants script), runs `alembic upgrade head` as the app role, then probes privileges
as each role. Needs Docker, `pip install -r api/requirements.txt` (alembic, sqlalchemy) and the dev requirements
(`api/requirements-dev.txt`, added by PR #28 together with the `make test` target; until both are merged run
`python -m pytest api/tests/integration/test_app_schema_and_roles.py` directly).
"""
import ast
import os
import pathlib
import subprocess
import sys
import time

import psycopg
import pytest
from psycopg import errors
from testcontainers.core.container import DockerContainer

ROOT = pathlib.Path(__file__).resolve().parents[3]
INIT = ROOT / "db" / "init"
SEED = ROOT / "data" / "seed" / "omop_subset.sql.gz"
MIGRATION = ROOT / "api" / "alembic" / "versions" / "0001_app_schema.py"

READER_PW, APP_PW, SUPER_PW = "reader_t3st", "app_t3st", "super_t3st"


def dsn(user, password, port):
    return f"postgresql://{user}:{password}@127.0.0.1:{port}/cohortlens"


def alembic(*args, port):
    env = {**os.environ, "DATABASE_URL_APP": dsn("cohortlens_app", APP_PW, port)}
    result = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT / "api", env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def port():
    container = (
        DockerContainer("postgres:16")
        .with_env("POSTGRES_PASSWORD", SUPER_PW).with_env("POSTGRES_DB", "cohortlens")
        .with_env("COHORTLENS_READER_PASSWORD", READER_PW).with_env("COHORTLENS_APP_PASSWORD", APP_PW)
        .with_volume_mapping((INIT / "00-roles.sh").as_posix(), "/docker-entrypoint-initdb.d/00-roles.sh", "ro")
        .with_volume_mapping(SEED.as_posix(), "/docker-entrypoint-initdb.d/10-omop_subset.sql.gz", "ro")
        .with_volume_mapping((INIT / "20-grants.sql").as_posix(), "/docker-entrypoint-initdb.d/20-grants.sql", "ro")
        .with_exposed_ports(5432)
    )
    with container:
        p = int(container.get_exposed_port(5432))
        deadline = time.time() + 180
        while True:  # the real server only accepts TCP connections after first-start initialisation finishes
            try:
                with psycopg.connect(dsn("postgres", SUPER_PW, p), connect_timeout=3) as c:
                    if c.execute("select count(*) from cdm.person").fetchone()[0]:
                        break
            except Exception:
                if time.time() > deadline:
                    raise
                time.sleep(2)
        alembic("upgrade", "head", port=p)
        yield p


def connect(user, password, port):
    return psycopg.connect(dsn(user, password, port), autocommit=True)


# ---- issue #3: app schema -------------------------------------------------------------------------------------

EXPECTED_COLUMNS = {
    "cohort_definition": {
        "id": ("uuid", "NO"), "name": ("text", "NO"), "definition": ("jsonb", "NO"), "version": ("integer", "NO"),
        "created_at": ("timestamp with time zone", "NO"), "updated_at": ("timestamp with time zone", "NO"),
    },
    "cohort_run": {
        "id": ("uuid", "NO"), "cohort_id": ("uuid", "NO"), "status": ("text", "NO"), "step_counts": ("jsonb", "YES"),
        "final_count": ("integer", "YES"), "duration_ms": ("integer", "YES"), "trace_id": ("text", "YES"),
        "service_version": ("text", "YES"), "created_at": ("timestamp with time zone", "NO"),
    },
}


@pytest.mark.parametrize("table", sorted(EXPECTED_COLUMNS))
def test_app_tables_have_the_documented_columns(port, table):
    with connect("cohortlens_app", APP_PW, port) as c:
        rows = c.execute(
            "select column_name, data_type, is_nullable from information_schema.columns "
            "where table_schema='app' and table_name=%s", (table,)).fetchall()
    assert {r[0]: (r[1], r[2]) for r in rows} == EXPECTED_COLUMNS[table]


def test_version_table_lives_in_the_app_schema_at_head(port):
    with connect("cohortlens_app", APP_PW, port) as c:
        assert c.execute("select version_num from app.alembic_version").fetchone()[0] == "0001"


def test_upgrade_downgrade_upgrade_cycle_works(port):
    alembic("downgrade", "base", port=port)
    alembic("upgrade", "head", port=port)
    assert "0001" in alembic("current", port=port)


def test_migration_is_expand_only():
    """Expand-then-contract: upgrade() may create things but never drop or alter them."""
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    upgrade = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade")
    called = {n.func.attr for n in ast.walk(upgrade) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert called, "upgrade() should call op.* functions"
    assert not {c for c in called if c.startswith("drop_") or c in ("alter_column", "rename_table")}
    assert not any("DROP" in (n.value.upper()) for n in ast.walk(upgrade) if isinstance(n, ast.Constant) and isinstance(n.value, str))


def test_foreign_key_cascades_runs_with_their_cohort(port):
    with connect("cohortlens_app", APP_PW, port) as c:
        cohort = c.execute("insert into app.cohort_definition(name, definition) values ('t', '{}') returning id").fetchone()[0]
        c.execute("insert into app.cohort_run(cohort_id, status) values (%s, 'success')", (cohort,))
        c.execute("delete from app.cohort_definition where id=%s", (cohort,))
        assert c.execute("select count(*) from app.cohort_run where cohort_id=%s", (cohort,)).fetchone()[0] == 0


# ---- issue #3: reader role -----------------------------------------------------------------------------------

def test_reader_can_select_cdm(port):
    with connect("cohortlens_reader", READER_PW, port) as c:
        assert c.execute("select count(*) from cdm.person").fetchone()[0] == 5724


@pytest.mark.parametrize("sql", [
    "select 1 from app.cohort_definition",
    "select 1 from app.alembic_version",
    "insert into app.cohort_definition(name, definition) values ('x', '{}')",
    "update cdm.person set year_of_birth = 1900",
    "delete from cdm.person",
    "create table cdm.x (i int)",
    "create table app.x (i int)",
    "create table public.x (i int)",
    "create temp table t (i int)",
    "create schema s",
])
def test_reader_is_denied(port, sql):
    with connect("cohortlens_reader", READER_PW, port) as c:
        with pytest.raises((errors.InsufficientPrivilege, errors.ReadOnlySqlTransaction)):
            c.execute(sql)


def test_reader_session_is_read_only_with_a_server_side_timeout(port):
    with connect("cohortlens_reader", READER_PW, port) as c:
        assert c.execute("show default_transaction_read_only").fetchone()[0] == "on"
        assert c.execute("show statement_timeout").fetchone()[0] == "10s"
        with pytest.raises(errors.QueryCanceled):
            c.execute("select pg_sleep(15)")


# ---- issue #3 / #2: app role ---------------------------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "select 1 from cdm.person limit 1",
    "create table cdm.x (i int)",
    "create table public.x (i int)",
    "create temp table t (i int)",
])
def test_app_role_is_denied(port, sql):
    with connect("cohortlens_app", APP_PW, port) as c:
        with pytest.raises(errors.InsufficientPrivilege):
            c.execute(sql)


def test_app_role_can_write_its_own_schema(port):
    with connect("cohortlens_app", APP_PW, port) as c:
        c.execute("insert into app.cohort_definition(name, definition) values ('ok', '{}')")
        assert c.execute("select count(*) from app.cohort_definition where name='ok'").fetchone()[0] == 1
        c.execute("delete from app.cohort_definition where name='ok'")


def test_nobody_else_can_connect(port):
    with connect("postgres", SUPER_PW, port) as c:
        c.execute("create role stranger login password 'stranger_pw'")
    with pytest.raises(psycopg.OperationalError, match="CONNECT privilege"):
        psycopg.connect(dsn("stranger", "stranger_pw", port))


# ---- issue #2: seed ------------------------------------------------------------------------------------------

def test_seed_loaded_automatically_on_first_start(port):
    with connect("postgres", SUPER_PW, port) as c:
        assert c.execute("select count(*) from cdm.person").fetchone()[0] == 5724
        assert c.execute("select count(*) from cdm.concept_ancestor where ancestor_concept_id = 1308216").fetchone()[0] >= 5
