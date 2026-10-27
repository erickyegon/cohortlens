"""SQLAlchemy Core descriptions of the application tables in schema `app` (created by Alembic migration 0001)."""
from sqlalchemy import Column, DateTime, Integer, MetaData, Table, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData(schema="app")

cohort_definition = Table(
    "cohort_definition", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("name", Text, nullable=False),
    Column("definition", JSONB, nullable=False),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
)
