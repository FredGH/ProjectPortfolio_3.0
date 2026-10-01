"""create pipeline schema and pipeline.stage_run

Revision ID: 0030
Revises: 0029
Create Date: 2026-09-30

Backs the pipeline dashboard (docs/superpowers/specs/
2026-09-30-pipeline-dashboard-design.md): one generic run-tracking
table for every automated pipeline stage, not one table per stage.
The partial unique index on `status` is the same proven pattern as
0025's silver.skill_extraction_run, just scoped to the WHOLE table
instead of one stage's own table -- a second INSERT with
status='running', for ANY stage, fails with a unique violation. That
IS the system-wide "only one thing runs at a time" lock the design
calls for.

No RLS: this is operational metadata (which pipeline runs happened,
by whom), not user-owned content. `user_id` is a plain nullable
foreign key for per-user stages, filtered by the API layer, not by a
row-level policy -- matches scoring.job_chunk_embedding's own
no-RLS-but-nullable-scope precedent (0028).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS pipeline")
    op.execute("GRANT USAGE ON SCHEMA pipeline TO job_search_app")

    op.create_table(
        "stage_run",
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=True
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("params", JSONB(), nullable=False, server_default="{}"),
        sa.Column("progress_current", sa.Integer(), nullable=True),
        sa.Column("progress_total", sa.Integer(), nullable=True),
        sa.Column(
            "cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("result", JSONB(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'cancelled', 'failed')",
            name="ck_stage_run_status",
        ),
        schema="pipeline",
    )
    # Same proven pattern as 0025's silver.skill_extraction_run: a unique
    # index on `status`, filtered to `running` rows, permits at most one
    # such row -- but here across every stage, not one table per stage,
    # so this is the system-wide lock.
    op.create_index(
        "ux_stage_run_one_active",
        "stage_run",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
        schema="pipeline",
    )
    # For "this stage's last completed run" lookups (staleness, last-run
    # display) -- always filtered by stage (+ user_id for per-user ones).
    op.create_index(
        "ix_stage_run_stage_user_status",
        "stage_run",
        ["stage", "user_id", "status"],
        schema="pipeline",
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON pipeline.stage_run TO job_search_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT, UPDATE ON pipeline.stage_run FROM job_search_app")
    op.drop_index("ix_stage_run_stage_user_status", table_name="stage_run", schema="pipeline")
    op.drop_index("ux_stage_run_one_active", table_name="stage_run", schema="pipeline")
    op.drop_table("stage_run", schema="pipeline")
    op.execute("DROP SCHEMA IF EXISTS pipeline")
