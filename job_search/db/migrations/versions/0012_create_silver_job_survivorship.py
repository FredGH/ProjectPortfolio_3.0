"""create silver.job_survivorship

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-09

silver.job_survivorship is SHARED per-cluster survivorship data
(PLAN.md Step 11, DECISIONS.md §2.7/§5): which source's description/
apply-link/display-title wins for a job_group_id is the same for every
user, so no user_id, no RLS — same two-zone reasoning as job_identity_
map (0011).

Unlike job_identity_map, this table is UPSERTed, not insert-only:
job_group_id itself is the only value PLAN.md requires to be immutable
once assigned (DECISIONS.md §2.6) — which source currently "wins"
survivorship is free to change as new source data arrives for an
existing cluster.

Written only by the migration/owner role, via the `compute-
survivorship` pipeline CLI subcommand (core.dedup.
write_job_survivorship) — never by a live per-user request. Read by
dbt's gold.dim_job model as a plain source().

job_search_app inherits SELECT here automatically via migration 0010's
`ALTER DEFAULT PRIVILEGES FOR ROLE job_search_owner IN SCHEMA silver`
rule (see 0010's and 0011's docstrings) — no explicit grant needed in
this migration.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_survivorship",
        sa.Column("job_group_id", sa.Text(), primary_key=True),
        sa.Column("winning_description", sa.Text(), nullable=True),
        sa.Column("apply_source_name", sa.Text(), nullable=False),
        sa.Column("apply_source_job_id", sa.Text(), nullable=False),
        sa.Column("apply_job_url", sa.Text(), nullable=False),
        sa.Column("apply_title_for_display", sa.Text(), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="silver",
    )


def downgrade() -> None:
    op.drop_table("job_survivorship", schema="silver")
