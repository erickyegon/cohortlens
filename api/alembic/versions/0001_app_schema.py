"""app schema: cohort_definition and cohort_run

Revision ID: 0001
Revises:
Expand-only migration (expand-then-contract): creates tables, drops nothing.
Schema app is provisioned by db/init/00-roles.sh.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cohort_definition",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("definition", postgresql.JSONB, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="app",
    )
    op.create_table(
        "cohort_run",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "cohort_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app.cohort_definition.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("step_counts", postgresql.JSONB),
        sa.Column("final_count", sa.Integer),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("trace_id", sa.Text),
        sa.Column("service_version", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="app",
    )
    op.create_index("ix_cohort_run_cohort_created", "cohort_run", ["cohort_id", "created_at"], schema="app")


def downgrade() -> None:
    op.drop_index("ix_cohort_run_cohort_created", table_name="cohort_run", schema="app")
    op.drop_table("cohort_run", schema="app")
    op.drop_table("cohort_definition", schema="app")
