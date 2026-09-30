"""drop silver.skill_extraction_run

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-30

extract-job-skills' run tracking moved to pipeline.stage_run (this
plan's Task 8) -- a true system-wide "one active run" lock requires
exactly one lock table, and this one's own partial-unique-index lock
would otherwise coexist with pipeline.stage_run's, letting two runs
go at once. No data migration: any run recorded here is historical
run bookkeeping only (the skill-extraction data itself, in
silver.job_skill_extraction/silver.job_skill_raw, is untouched and
was never stored in this table).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # job_search_app's INSERT grant on silver.job_skill_extraction/
    # job_skill_raw (from 0025) is unrelated to skill_extraction_run
    # itself -- write_job_skills still needs it and it is untouched
    # here. Only the run-tracking table and its own grant go.
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE ON silver.skill_extraction_run FROM job_search_app"
    )
    op.drop_index(
        "ux_skill_extraction_run_one_active", table_name="skill_extraction_run", schema="silver"
    )
    op.drop_table("skill_extraction_run", schema="silver")


def downgrade() -> None:
    op.create_table(
        "skill_extraction_run",
        sa.Column(
            "run_id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("sources", ARRAY(sa.Text()), nullable=True),
        sa.Column("countries", ARRAY(sa.Text()), nullable=True),
        sa.Column("total_pending", sa.Integer(), nullable=False),
        sa.Column("extracted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mapping_summary", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'cancelled', 'failed')",
            name="ck_skill_extraction_run_status",
        ),
        schema="silver",
    )
    op.create_index(
        "ux_skill_extraction_run_one_active", "skill_extraction_run", ["status"],
        unique=True, postgresql_where=sa.text("status = 'running'"), schema="silver",
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON silver.skill_extraction_run TO job_search_app")
