"""Database engines: a read-only engine for the cdm schema and a read-write engine for the app schema."""
import os

from sqlalchemy import create_engine

STATEMENT_TIMEOUT_MS = 10_000  # protects the database from runaway cohort queries


def _url(name: str) -> str:
    return os.environ[name].replace("postgresql://", "postgresql+psycopg://", 1)


def reader_engine():
    return create_engine(
        _url("DATABASE_URL_READER"),
        pool_pre_ping=True,
        connect_args={"options": f"-c statement_timeout={STATEMENT_TIMEOUT_MS}"},
    )


def app_engine():
    return create_engine(_url("DATABASE_URL_APP"), pool_pre_ping=True)
