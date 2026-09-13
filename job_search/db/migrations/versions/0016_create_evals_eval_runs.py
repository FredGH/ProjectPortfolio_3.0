"""create evals.eval_runs

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-12

evals.eval_runs is SHARED data (PLAN.md's two-zone rule): how well a
prompt version performs against a golden set is the same fact for
every user of this project, so no user_id, no RLS — same two-zone
reasoning as dedup.calibration_thresholds.

Append-only, same reasoning as calibration_thresholds (0010): every
`run-evals` invocation adds a row rather than updating one, so history
is free and the most recent row per (task, provider) is "current" —
exactly what the regression comparison in run-evals needs.

Written only by the pipeline CLI (owner role) — no request-serving
access needed, unlike classification.category_review_labels (0014):
nothing in apps/api reads or writes this table.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS evals")
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("score", sa.Numeric(), nullable=False),
        sa.Column("case_count", sa.Integer(), nullable=False),
        sa.Column(
            "run_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="evals",
    )


def downgrade() -> None:
    op.drop_table("eval_runs", schema="evals")
    op.execute("DROP SCHEMA IF EXISTS evals")
