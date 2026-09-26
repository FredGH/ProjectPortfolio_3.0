"""add mapping_summary to silver.skill_extraction_run

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-26

A completed extraction run now maps its newly extracted skill strings to
ESCO automatically (core.skills.extraction_run.run_loop). This column holds
the one-line outcome the UI shows — e.g. "Mapped 272 new skill string(s) to
ESCO; 2,490 need review." — or why mapping did not run or failed. NULL for
a run that has not finished or predates this migration. job_search_app
already holds UPDATE on the table (0025), so no grant is needed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the nullable mapping_summary column."""
    op.add_column(
        "skill_extraction_run",
        sa.Column("mapping_summary", sa.Text(), nullable=True),
        schema="silver",
    )


def downgrade() -> None:
    """Drop the mapping_summary column."""
    op.drop_column("skill_extraction_run", "mapping_summary", schema="silver")
