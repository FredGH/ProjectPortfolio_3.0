"""add tailoring.tailored_cv.usage (Step 17 follow-up)

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-05

Token counts and estimated cost per run: the loop records every LLM call
(Tailor and critic) here as the run goes. NULL until the first call
finishes. The app role already has table-level UPDATE on
tailoring.tailored_cv (migration 0032).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the nullable `usage` column."""
    op.add_column(
        "tailored_cv",
        sa.Column("usage", JSONB(), nullable=True),
        schema="tailoring",
    )


def downgrade() -> None:
    """Drop the `usage` column."""
    op.drop_column("tailored_cv", "usage", schema="tailoring")
