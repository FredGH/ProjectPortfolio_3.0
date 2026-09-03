"""create user_quota and shared_api_quota

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_quota",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app_user.id"),
            nullable=False,
        ),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("monthly_llm_spend_cap_usd", sa.Numeric(10, 2), nullable=False),
        sa.Column(
            "monthly_llm_spend_used_usd",
            sa.Numeric(10, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column("artefact_generation_cap", sa.Integer(), nullable=False),
        sa.Column(
            "artefact_generation_used",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("alert_cap", sa.Integer(), nullable=False),
        sa.Column("alert_used", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("user_id", "period_start", name="uq_user_quota_period"),
    )
    op.create_index("ix_user_quota_user_id", "user_quota", ["user_id"])

    op.execute("ALTER TABLE user_quota ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY user_quota_isolation ON user_quota
        USING (user_id = current_setting('app.current_user_id', true)::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON user_quota TO job_search_app")

    # Shared across users by design — it tracks the aggregate Adzuna
    # allowance, not any individual's data, so it carries no user_id and no
    # RLS policy (see the "shared tables explicitly marked" subtask).
    op.create_table(
        "shared_api_quota",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("resource_name", sa.Text(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("total_limit", sa.Integer(), nullable=False),
        sa.Column("total_used", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint(
            "resource_name", "period_start", name="uq_shared_quota_period"
        ),
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON shared_api_quota TO job_search_app")


def downgrade() -> None:
    op.drop_table("shared_api_quota")
    op.execute("REVOKE SELECT, INSERT, UPDATE ON user_quota FROM job_search_app")
    op.drop_table("user_quota")
