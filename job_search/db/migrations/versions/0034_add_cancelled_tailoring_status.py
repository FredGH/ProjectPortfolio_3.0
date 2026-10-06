"""allow tailoring.tailored_cv.status = 'cancelled' (Step 17 follow-up)

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-05

A user can now cancel a running tailoring run; the run is kept with status
`cancelled` (no document, no orphans).
"""

from __future__ import annotations

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

_TABLE = "tailored_cv"
_NAME = "ck_tailored_cv_status"


def upgrade() -> None:
    """Replace the status check so it also accepts `cancelled`."""
    op.drop_constraint(_NAME, _TABLE, schema="tailoring", type_="check")
    op.create_check_constraint(
        _NAME,
        _TABLE,
        "status IN ('generating', 'needs_review', 'approved', 'failed', "
        "'cancelled')",
        schema="tailoring",
    )


def downgrade() -> None:
    """Turn cancelled runs into failed ones, then restore the old check."""
    op.execute(
        "UPDATE tailoring.tailored_cv SET status = 'failed' "
        "WHERE status = 'cancelled'"
    )
    op.drop_constraint(_NAME, _TABLE, schema="tailoring", type_="check")
    op.create_check_constraint(
        _NAME,
        _TABLE,
        "status IN ('generating', 'needs_review', 'approved', 'failed')",
        schema="tailoring",
    )
