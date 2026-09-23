"""create silver.skill_extraction_run

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-23

Backs the skill-extraction batch runner (JOB-tbd, see docs/superpowers/
specs/2026-09-23-skill-extraction-batch-runner-design.md): one row per
UI-triggered extraction run, so its status survives an API restart. The
partial unique index on `status = 'running'` is what enforces "at most
one active run" — a second INSERT with status='running' fails with a
unique violation rather than needing an application-level lock.

job_search_app also gets INSERT on silver.job_skill_extraction and
silver.job_skill_raw here: those tables have been owner-role-write-only
since 0023 (only the pipeline CLI wrote them), but `run_loop` calls
`write_job_skills` from the API process, using the app-role engine.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_extraction_run",
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("sources", ARRAY(sa.Text()), nullable=True),
        sa.Column("countries", ARRAY(sa.Text()), nullable=True),
        sa.Column("total_pending", sa.Integer(), nullable=False),
        sa.Column(
            "extracted_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
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
            name="ck_skill_extraction_run_status",
        ),
        schema="silver",
    )
    op.create_index(
        "ux_skill_extraction_run_one_active",
        "skill_extraction_run",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
        schema="silver",
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON silver.skill_extraction_run "
        "TO job_search_app"
    )
    op.execute(
        "GRANT INSERT ON silver.job_skill_extraction, silver.job_skill_raw "
        "TO job_search_app"
    )


def downgrade() -> None:
    op.execute(
        "REVOKE INSERT ON silver.job_skill_extraction, silver.job_skill_raw "
        "FROM job_search_app"
    )
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE ON silver.skill_extraction_run "
        "FROM job_search_app"
    )
    op.drop_index(
        "ux_skill_extraction_run_one_active",
        table_name="skill_extraction_run",
        schema="silver",
    )
    op.drop_table("skill_extraction_run", schema="silver")
