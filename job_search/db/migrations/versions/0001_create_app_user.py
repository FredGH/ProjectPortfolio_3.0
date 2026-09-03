"""create app_user

Revision ID: 0001
Revises:
Create Date: 2026-09-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "app_user",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("locale", sa.Text(), nullable=False, server_default="en-GB"),
    )

    # Every per-user table gets RLS enabled and a policy keyed on the fixed
    # `app.current_user_id` GUC — this table is the first of many that will
    # follow exactly this two-statement pattern (see Global Constraints).
    op.execute("ALTER TABLE app_user ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY app_user_isolation ON app_user
        USING (id = current_setting('app.current_user_id', true)::uuid)
        """
    )

    op.execute("GRANT USAGE ON SCHEMA public TO job_search_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON app_user TO job_search_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT, UPDATE ON app_user FROM job_search_app")
    op.drop_table("app_user")
