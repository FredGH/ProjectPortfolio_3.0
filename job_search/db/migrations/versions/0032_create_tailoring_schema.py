"""create tailoring schema (PLAN.md Step 17)

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-01

Backs docs/superpowers/specs/2026-10-01-step17-tailoring-design.md:

- tailoring.tailored_cv: one row per tailoring run (every run is a new
  row; the UI shows the latest per job). `content` holds the assembled
  TailoredDocument.
- tailoring.orphan_bullet: bullets the loop could not trace to the
  truth base, awaiting an explicit user decision.

Both tables are RLS-isolated per user (same policy pattern as
scoring.job_label, migration 0029): a tailored CV is built from one
user's employment history.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    """Enable RLS and add the standard app.current_user_id policy.

    Args:
        table: The unqualified table name, in the `tailoring` schema.
    """
    op.execute(f"ALTER TABLE tailoring.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_isolation ON tailoring.{table} "
        "USING (user_id = current_setting('app.current_user_id', true)::uuid)"
    )


def upgrade() -> None:
    """Create the tailoring schema and both tables."""
    op.execute("CREATE SCHEMA IF NOT EXISTS tailoring")
    op.execute("GRANT USAGE ON SCHEMA tailoring TO job_search_app")

    op.create_table(
        "tailored_cv",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False
        ),
        sa.Column("job_group_id", sa.Text(), nullable=False),
        sa.Column("truth_base_version", sa.Integer(), nullable=False),
        sa.Column("target_title", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="generating"),
        sa.Column("content", JSONB(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stretch", JSONB(), nullable=True),
        sa.Column("tailor_model", sa.Text(), nullable=True),
        sa.Column("tailor_prompt_version", sa.Text(), nullable=True),
        sa.Column("critic_model", sa.Text(), nullable=True),
        sa.Column("critic_prompt_version", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
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
        sa.CheckConstraint(
            "status IN ('generating', 'needs_review', 'approved', 'failed')",
            name="ck_tailored_cv_status",
        ),
        schema="tailoring",
    )
    op.create_index(
        "ix_tailored_cv_user_job_created",
        "tailored_cv",
        ["user_id", "job_group_id", "created_at"],
        schema="tailoring",
    )
    _rls("tailored_cv")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON tailoring.tailored_cv TO job_search_app"
    )

    op.create_table(
        "orphan_bullet",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tailored_cv_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tailoring.tailored_cv.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("experience_index", sa.Integer(), nullable=True),
        sa.Column("bullet_index", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "claimed_refs",
            ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("issue", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("evidence_ref", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('orphan', 'unsupported')", name="ck_orphan_bullet_kind"
        ),
        sa.CheckConstraint(
            "section IN ('summary', 'experience')", name="ck_orphan_bullet_section"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'linked', 'rejected')",
            name="ck_orphan_bullet_status",
        ),
        schema="tailoring",
    )
    op.create_index(
        "ix_orphan_bullet_run",
        "orphan_bullet",
        ["tailored_cv_id"],
        schema="tailoring",
    )
    _rls("orphan_bullet")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON tailoring.orphan_bullet TO job_search_app"
    )


def downgrade() -> None:
    """Drop both tables and the schema."""
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE ON tailoring.orphan_bullet FROM job_search_app"
    )
    op.drop_index(
        "ix_orphan_bullet_run", table_name="orphan_bullet", schema="tailoring"
    )
    op.drop_table("orphan_bullet", schema="tailoring")
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE ON tailoring.tailored_cv FROM job_search_app"
    )
    op.drop_index(
        "ix_tailored_cv_user_job_created", table_name="tailored_cv", schema="tailoring"
    )
    op.drop_table("tailored_cv", schema="tailoring")
    op.execute("DROP SCHEMA IF EXISTS tailoring")
