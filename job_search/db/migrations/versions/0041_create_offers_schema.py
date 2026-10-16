"""create offers schema (PLAN.md Step 21c)

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-16

offers.offer_received: offers a user has received, with the benchmark computed
at the time. Per user (RLS). `application_id` is a plain nullable column for now:
fct_application does not exist yet (Steps 20a / 21); the link is filled in once it
does.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the offers schema and its table."""
    op.execute("CREATE SCHEMA IF NOT EXISTS offers")
    op.execute("GRANT USAGE ON SCHEMA offers TO job_search_app")
    op.create_table(
        "offer_received",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False
        ),
        sa.Column("job_group_id", sa.Text(), nullable=True),
        sa.Column("application_id", UUID(as_uuid=True), nullable=True),
        sa.Column("company", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column(
            "received_on",
            sa.Date(),
            nullable=False,
            server_default=sa.text("CURRENT_DATE"),
        ),
        sa.Column("amount", sa.Numeric(), nullable=False),
        sa.Column("basis", sa.Text(), nullable=False),
        sa.Column("engagement_type", sa.Text(), nullable=False),
        sa.Column(
            "ir35_status", sa.Text(), nullable=False, server_default="undetermined"
        ),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("seniority", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("benchmark", JSONB(), nullable=True),
        sa.Column("tax_year", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("amount > 0", name="ck_offer_amount"),
        sa.CheckConstraint(
            "basis IN ('daily', 'hourly', 'annual')", name="ck_offer_basis"
        ),
        sa.CheckConstraint(
            "engagement_type IN ('contract', 'permanent')", name="ck_offer_engagement"
        ),
        sa.CheckConstraint(
            "ir35_status IN ('inside', 'outside', 'undetermined')", name="ck_offer_ir35"
        ),
        schema="offers",
    )
    op.create_index(
        "ix_offer_received_user",
        "offer_received",
        ["user_id", "created_at"],
        schema="offers",
    )
    op.execute("ALTER TABLE offers.offer_received ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY offer_received_isolation ON offers.offer_received "
        "USING (user_id = current_setting('app.current_user_id', true)::uuid)"
    )
    op.execute("GRANT SELECT, INSERT ON offers.offer_received TO job_search_app")


def downgrade() -> None:
    """Drop the table and the schema."""
    op.drop_table("offer_received", schema="offers")
    op.execute("DROP SCHEMA IF EXISTS offers")
