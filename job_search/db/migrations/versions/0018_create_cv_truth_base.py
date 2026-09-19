"""create cv_truth_base and cv_truth_base_history

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-13

cv_truth_base (PLAN.md Step 13, JOB-202) holds one user's current CV
truth base. `user_id` carries a UNIQUE constraint — the DB-enforced
"exactly one base CV per user" PLAN.md asks for, so no application code
path (including a future import script) can create a second live row.

cv_truth_base_history is append-only: every version that has ever been
current — including the current one, mirrored here at write time — so
"which version was this artefact generated from" stays answerable, and
a correction-pass edit is traceable the same way a fresh extraction is
(see core.cv.store's docstring for the write path both share).

Both are per-user tables, so both get RLS enabled with a policy on the
`app.current_user_id` GUC — the exact two-statement pattern used by
app_user (0001) and user_quota (0002).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cv_truth_base",
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
            unique=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("extracted_markdown", sa.Text(), nullable=False),
        sa.Column("truth_base", JSONB(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.execute("ALTER TABLE cv_truth_base ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY cv_truth_base_isolation ON cv_truth_base
        USING (user_id = current_setting('app.current_user_id', true)::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON cv_truth_base TO job_search_app")

    op.create_table(
        "cv_truth_base_history",
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
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("extracted_markdown", sa.Text(), nullable=False),
        sa.Column("truth_base", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "user_id",
            "version",
            name="uq_cv_truth_base_history_version",
        ),
    )
    op.execute("ALTER TABLE cv_truth_base_history ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY cv_truth_base_history_isolation ON cv_truth_base_history
        USING (user_id = current_setting('app.current_user_id', true)::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT ON cv_truth_base_history TO job_search_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT ON cv_truth_base_history FROM job_search_app")
    op.drop_table("cv_truth_base_history")
    op.execute("REVOKE SELECT, INSERT, UPDATE ON cv_truth_base FROM job_search_app")
    op.drop_table("cv_truth_base")
