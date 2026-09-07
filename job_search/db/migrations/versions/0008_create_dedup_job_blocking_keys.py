"""create dedup.job_blocking_keys

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-07

dedup.job_blocking_keys is SHARED job-pair-generation data (PLAN.md's
two-zone rule, same pattern as silver.job_engagement_terms (0007)): the
blocking key for a posting is the same for every user, so it carries no
user_id and has no row-level security.

Written only by the migration/owner role, via the
`compute-blocking-keys` pipeline CLI subcommand (core.dedup.
write_blocking_keys) — never by a live per-user request. Read only by
dbt (dedup__candidate_pairs, dedup__exact_duplicates, PLAN.md Step 7),
which also connects as owner (dbt/profiles.yml).

normalised_company/matching_title/country_iso/region are stored
separately from block_key (not just the concatenated key) so Step 8 can
reuse them directly (company trigram similarity, location match) without
re-deriving them from a packed string.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS dedup")

    op.create_table(
        "job_blocking_keys",
        sa.Column("job_key", sa.Text(), primary_key=True),
        sa.Column("normalised_company", sa.Text(), nullable=False),
        sa.Column("matching_title", sa.Text(), nullable=False),
        sa.Column("country_iso", sa.Text(), nullable=True),
        sa.Column("region", sa.Text(), nullable=True),
        sa.Column("block_key", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="dedup",
    )
    op.create_index(
        "ix_job_blocking_keys_block_key",
        "job_blocking_keys",
        ["block_key"],
        schema="dedup",
    )
    op.create_index(
        "ix_job_blocking_keys_normalised_company",
        "job_blocking_keys",
        ["normalised_company"],
        schema="dedup",
    )
    op.create_index(
        "ix_job_blocking_keys_content_sha256",
        "job_blocking_keys",
        ["content_sha256"],
        schema="dedup",
    )


def downgrade() -> None:
    op.drop_table("job_blocking_keys", schema="dedup")
    # Never drop the `dedup` schema here — a later step's dbt models (or
    # a sibling migration) may also own objects in it. See Step 5a's
    # migration 0007 for the same lesson learned the hard way.
