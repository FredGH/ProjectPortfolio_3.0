"""add label to cv_truth_base and cv_truth_base_history

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-17

Adds an optional `label` a user can attach to a CV version when saving
(e.g. "Before I added the AI section") so they can tell versions apart
when browsing history — nullable everywhere, since most saves won't
set one. Present on both tables, mirroring every other column
core.cv.store's write path already keeps in lockstep between them.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cv_truth_base", sa.Column("label", sa.Text(), nullable=True))
    op.add_column("cv_truth_base_history", sa.Column("label", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("cv_truth_base_history", "label")
    op.drop_column("cv_truth_base", "label")
