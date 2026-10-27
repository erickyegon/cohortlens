"""Shared integration fixtures: a Postgres 16 container loaded with the truth fixture (23 hand-built patients)."""
import pathlib

import pytest
from sqlalchemy import create_engine
from testcontainers.community.postgres import PostgresContainer

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "truth_fixture.sql"


@pytest.fixture(scope="session")
def truth_engine():
    """SQLAlchemy engine on a throwaway Postgres with schema `cdm` and the truth fixture loaded."""
    with PostgresContainer("postgres:16") as pg:
        url = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql+psycopg://")
        engine = create_engine(url)
        with engine.begin() as conn:
            conn.exec_driver_sql(FIXTURE.read_text(encoding="utf-8"))
        yield engine
        engine.dispose()


# ---- API tests: one Postgres with the truth fixture (cdm) AND the migrated app schema ---------------------------

API_DIR = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def api_database_url():
    """Throwaway Postgres 16 with schema `cdm` (truth fixture) and schema `app` (Alembic migration 0001 applied).

    Both API connections point at it as the superuser: the two-role privilege model has its own tests
    (test_app_schema_and_roles.py).
    """
    import os
    import subprocess
    import sys

    with PostgresContainer("postgres:16") as pg:
        url = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
        with engine.begin() as conn:
            conn.exec_driver_sql(FIXTURE.read_text(encoding="utf-8"))
            conn.exec_driver_sql("create schema app")
        engine.dispose()
        result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=API_DIR,
                                env={**os.environ, "DATABASE_URL_APP": url}, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        yield url


@pytest.fixture()
def api_env(api_database_url, monkeypatch):
    """Point the API's two engines at the test database and start from empty app tables."""
    from app.db import session

    monkeypatch.setenv("DATABASE_URL_APP", api_database_url)
    monkeypatch.setenv("DATABASE_URL_READER", api_database_url)
    session.app_engine.cache_clear()
    session.reader_engine.cache_clear()
    engine = session.app_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql("delete from app.cohort_run")
        conn.exec_driver_sql("delete from app.cohort_definition")
    yield api_database_url
    session.app_engine().dispose()
    session.reader_engine().dispose()
    session.app_engine.cache_clear()
    session.reader_engine.cache_clear()


@pytest.fixture()
def client(api_env):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
