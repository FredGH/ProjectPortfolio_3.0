"""add tailoring.tailored_cv.tailor_backend (Step 17 follow-up)

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-05

Records which backend ran the Tailor: `claude`, `native` (Ollama on the
Mac) or `docker` (the compose Ollama service). NULL = a run made before the
selector existed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

_TABLE = "tailored_cv"
_NAME = "ck_tailored_cv_tailor_backend"


def upgrade() -> None:
    """Add the nullable `tailor_backend` column and its check."""
    op.add_column(
        _TABLE,
        sa.Column("tailor_backend", sa.Text(), nullable=True),
        schema="tailoring",
    )
    op.create_check_constraint(
        _NAME,
        _TABLE,
        "tailor_backend IN ('claude', 'native', 'docker')",
        schema="tailoring",
    )


def downgrade() -> None:
    """Drop the check and the column."""
    op.drop_constraint(_NAME, _TABLE, schema="tailoring", type_="check")
    op.drop_column(_TABLE, "tailor_backend", schema="tailoring")
