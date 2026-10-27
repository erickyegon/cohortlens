"""Database engines: a read-only engine for the cdm schema and a read-write engine for the app schema."""
import os
from functools import lru_cache

from sqlalchemy import create_engine

STATEMENT_TIMEOUT_MS = 10_000  # protects the database from runaway cohort queries
CONNECT_TIMEOUT_S = 5  # an unreachable database must fail fast so the user gets a 503 with a reference, not a hang


def _url(name: str) -> str:
    return os.environ[name].replace("postgresql://", "postgresql+psycopg://", 1)


@lru_cache(maxsize=1)
def reader_engine():
    return create_engine(
        _url("DATABASE_URL_READER"),
        pool_pre_ping=True,
        connect_args={"options": f"-c statement_timeout={STATEMENT_TIMEOUT_MS}", "connect_timeout": CONNECT_TIMEOUT_S},
    )


@lru_cache(maxsize=1)
def app_engine():
    return create_engine(
        _url("DATABASE_URL_APP"),
        pool_pre_ping=True,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_S},
    )
