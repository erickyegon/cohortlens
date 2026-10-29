"""cohort_run.baseline: the suppressed baseline of a run

Revision ID: 0002
Revises: 0001
Expand-only migration (expand-then-contract): adds one nullable column, drops and alters nothing, so the previous
revision keeps working against the new schema (it never writes or reads the column). Runs made before this migration
have no baseline (NULL). The column holds the baseline AFTER small-cell suppression, never exact counts.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cohort_run", sa.Column("baseline", postgresql.JSONB, nullable=True), schema="app")


def downgrade() -> None:
    op.drop_column("cohort_run", "baseline", schema="app")
