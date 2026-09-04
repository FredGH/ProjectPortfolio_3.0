"""create target_company

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-03

target_company is SHARED reference data (docs/tenancy.md's "Adding a new
shared table"): which companies' ATS boards to poll is the same list for
every user, so this table carries no user_id and has no RLS policy — see
bronze.raw_jobs (0004) and shared_api_quota (0002) for the same pattern.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "target_company",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("ats_provider", sa.Text(), nullable=False),
        sa.Column("board_slug", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "ats_provider IN ('greenhouse', 'lever', 'ashby')",
            name="ck_target_company_ats_provider",
        ),
        sa.UniqueConstraint(
            "ats_provider", "board_slug", name="uq_target_company_board"
        ),
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON target_company TO job_search_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT, UPDATE ON target_company FROM job_search_app")
    op.drop_table("target_company")
