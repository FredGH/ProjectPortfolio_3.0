"""add collection_channel to bronze.raw_jobs

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-04

collection_channel distinguishes the frozen-keyword-matrix "targeted"
channel from the wide-and-shallow "discovery" channel (PLAN.md Step 4a) —
the same job entry_method (0004) already does for a different axis.
NOT NULL with a server default of 'targeted' so every pre-existing row
(all of it collected before this column existed, all of it via keyword-
bound queries) is correctly backfilled without a data migration.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "raw_jobs",
        sa.Column(
            "collection_channel",
            sa.Text(),
            nullable=False,
            server_default="targeted",
        ),
        schema="bronze",
    )
    op.create_check_constraint(
        "ck_bronze_raw_jobs_collection_channel",
        "raw_jobs",
        "collection_channel IN ('targeted', 'discovery')",
        schema="bronze",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_bronze_raw_jobs_collection_channel", "raw_jobs", schema="bronze"
    )
    op.drop_column("raw_jobs", "collection_channel", schema="bronze")
