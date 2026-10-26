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
