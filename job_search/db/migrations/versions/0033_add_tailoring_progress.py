"""add tailoring.tailored_cv.progress (Step 17 follow-up)

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-04

Live progress for a run that is still `generating`: the loop writes the
current phase, a human message and the finished-attempt history here, and
the review page polls it. NULL until the first update. The app role
already has table-level UPDATE on tailoring.tailored_cv (migration 0032).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the nullable `progress` column."""
    op.add_column(
        "tailored_cv",
        sa.Column("progress", JSONB(), nullable=True),
        schema="tailoring",
    )


def downgrade() -> None:
    """Drop the `progress` column."""
    op.drop_column("tailored_cv", "progress", schema="tailoring")
