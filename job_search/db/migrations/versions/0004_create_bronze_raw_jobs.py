"""create bronze.raw_jobs

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-03

bronze.raw_jobs is SHARED job-posting data (PLAN.md's two-zone rule,
docs/tenancy.md): collected once, identical for every user. It carries no
user_id and has no row-level security. It is WRITTEN only by the
migration/owner role via the batch ingestion pipeline (dlt), never by a
live per-user API request — see core.ingestion.bronze.load_to_bronze,
which connects with the owner DSN, not the RLS-enforced app role.

It is READ by the app role, though: Task 7's GET /sources endpoint needs
a live, request-serving read of distinct source_name values, and (per the
project's general principle of keeping the owner/migration credential out
of request-serving code wherever possible) that read goes through
job_search_app, not the owner DSN — safe here because the table carries
no per-user data and no RLS to bypass.

`_dlt_load_id`/`_dlt_id` are dlt's own bookkeeping columns, created here
explicitly so dlt's load finds them already present rather than having to
evolve the schema itself.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS bronze")

    op.create_table(
        "raw_jobs",
        sa.Column("_dlt_load_id", sa.Text(), nullable=True),
        sa.Column("_dlt_id", sa.Text(), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("source_job_id", sa.Text(), nullable=False),
        sa.Column("job_url", sa.Text(), nullable=False),
        sa.Column("job_url_canonical", sa.Text(), nullable=False),
        sa.Column("entry_method", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("request_params", JSONB(), nullable=True),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("payload_sha256", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "entry_method IN ('api', 'manual', 'scraped')",
            name="ck_bronze_raw_jobs_entry_method",
        ),
        sa.UniqueConstraint(
            "source_name",
            "source_job_id",
            "payload_sha256",
            name="uq_bronze_raw_jobs_dedup",
        ),
        schema="bronze",
    )

    op.execute("GRANT USAGE ON SCHEMA bronze TO job_search_app")
    op.execute("GRANT SELECT ON bronze.raw_jobs TO job_search_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON bronze.raw_jobs FROM job_search_app")
    op.execute("REVOKE USAGE ON SCHEMA bronze FROM job_search_app")
    op.drop_table("raw_jobs", schema="bronze")
    op.execute("DROP SCHEMA IF EXISTS bronze")
