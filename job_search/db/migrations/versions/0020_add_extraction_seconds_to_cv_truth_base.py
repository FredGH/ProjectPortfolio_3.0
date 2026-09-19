"""add extraction_seconds to cv_truth_base and cv_truth_base_history

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-17

Records how long a version's extraction pipeline took end-to-end
(Docling parse + LLM call, through the DB write) — nullable, since
only a version produced by `POST /cv/extract` has one; a
correction-pass save via `PUT /cv/truth-base` didn't run an
extraction, so its rows leave this null. Present on both tables,
mirroring `label` (0019) and every other column core.cv.store's write
path keeps in lockstep between them.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cv_truth_base", sa.Column("extraction_seconds", sa.Float(), nullable=True)
    )
    op.add_column(
        "cv_truth_base_history",
        sa.Column("extraction_seconds", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cv_truth_base_history", "extraction_seconds")
    op.drop_column("cv_truth_base", "extraction_seconds")
