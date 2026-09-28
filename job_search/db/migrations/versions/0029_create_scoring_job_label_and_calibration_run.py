"""create scoring.job_label and scoring.calibration_run (PLAN.md Step 16)

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-28

Two tables for hand-labeling and recording calibration runs:

- job_label: per-user hand labels (strong/maybe/no) on individual jobs,
  used to fit scoring.weight (RLS, since a label is a personal judgment).
- calibration_run: append-only history of every fit run's inputs and
  result (RLS, per-user — unlike dedup.calibration_thresholds, which is
  shared, scoring weights are a personal preference, not a global fact).

Both tables were deliberately NOT created in migration 0028 (see its own
docstring) — reserved for this migration.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    """Enable RLS and add the standard app.current_user_id policy.

    Args:
        table: The unqualified table name, in the `scoring` schema.
    """
    op.execute(f"ALTER TABLE scoring.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_isolation ON scoring.{table} "
        "USING (user_id = current_setting('app.current_user_id', true)::uuid)"
    )


def upgrade() -> None:
    """Create scoring.job_label and scoring.calibration_run."""
    op.create_table(
        "job_label",
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False
        ),
        sa.Column("job_group_id", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column(
            "labeled_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("user_id", "job_group_id"),
        sa.CheckConstraint(
            "label IN ('strong', 'maybe', 'no')", name="ck_job_label_label"
        ),
        schema="scoring",
    )
    _rls("job_label")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON scoring.job_label TO job_search_app"
    )

    op.create_table(
        "calibration_run",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False
        ),
        sa.Column("fit_count", sa.Integer(), nullable=False),
        sa.Column("holdout_count", sa.Integer(), nullable=False),
        sa.Column("vector_similarity_weight", sa.Numeric(), nullable=False),
        sa.Column("reranker_weight", sa.Numeric(), nullable=False),
        sa.Column("skill_coverage_weight", sa.Numeric(), nullable=False),
        sa.Column("llm_fit_weight", sa.Numeric(), nullable=False),
        sa.Column("holdout_agreement", sa.Numeric(), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column("calibrated_by", sa.Text(), nullable=True),
        sa.Column(
            "calibrated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="scoring",
    )
    _rls("calibration_run")
    op.execute("GRANT SELECT, INSERT ON scoring.calibration_run TO job_search_app")
    op.execute(
        "GRANT USAGE, SELECT ON SEQUENCE scoring.calibration_run_id_seq "
        "TO job_search_app"
    )


def downgrade() -> None:
    """Drop both tables."""
    op.execute(
        "REVOKE USAGE, SELECT ON SEQUENCE scoring.calibration_run_id_seq "
        "FROM job_search_app"
    )
    op.execute("REVOKE SELECT, INSERT ON scoring.calibration_run FROM job_search_app")
    op.drop_table("calibration_run", schema="scoring")
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON scoring.job_label "
        "FROM job_search_app"
    )
    op.drop_table("job_label", schema="scoring")
