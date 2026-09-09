"""create silver.job_identity_map

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-09

silver.job_identity_map is SHARED dedup-identity data (PLAN.md's
two-zone rule, same pattern as dedup.job_blocking_keys (0008)): which
cluster a posting belongs to is the same for every user, so it carries
no user_id and has no row-level security.

Written only by the migration/owner role, via the `cluster-jobs`
pipeline CLI subcommand (core.dedup.write_job_identity_map) — never by
a live per-user request, and (unlike every other dedup write path)
never updated after insert: PLAN.md Step 10 requires job_group_id to
never change once assigned, so this table is insert-only by design (see
write_job_identity_map's ON CONFLICT DO NOTHING).

Keyed by (source_name, source_job_id) rather than job_key because that
pair is exactly silver__job_posting's own natural key (job_key is its
surrogate hash of the same two columns) — this is PLAN.md's own named
schema for this table, and keeping the natural key avoids depending on
dbt's surrogate-key macro from a migration-owned table.

match_method has a fourth value, 'manual', beyond PLAN.md's original
three ('exact', 'fuzzy', 'singleton') — Step 9's dedup.pair_labels
(migration 0010) lets a human directly confirm a match independent of
blended_score, and that evidence needs its own match_method so it's
distinguishable from an ordinary threshold-cleared fuzzy match. See the
Step 10 plan's "Step 9 is done and merged" scope note.

job_search_app inherits SELECT access on this table via migration 0010's
ALTER DEFAULT PRIVILEGES rule — every table job_search_owner creates in
the silver schema automatically grants SELECT to job_search_app,
surviving dbt's drop/recreate cycle. The table itself is written only by
the migration/owner role, via the `cluster-jobs` pipeline CLI subcommand;
job_search_app has read-only access and no write permission.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS silver")

    op.create_table(
        "job_identity_map",
        sa.Column("source_name", sa.Text(), primary_key=True),
        sa.Column("source_job_id", sa.Text(), primary_key=True),
        sa.Column("job_group_id", sa.Text(), nullable=False),
        sa.Column("match_method", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(), nullable=False),
        sa.Column(
            "matched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "is_manual_override",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.CheckConstraint(
            "match_method IN ('exact', 'fuzzy', 'manual', 'singleton')",
            name="ck_job_identity_map_match_method",
        ),
        schema="silver",
    )
    op.create_index(
        "ix_job_identity_map_job_group_id",
        "job_identity_map",
        ["job_group_id"],
        schema="silver",
    )


def downgrade() -> None:
    op.drop_table("job_identity_map", schema="silver")
    # Never drop the `silver` schema — dbt also owns objects in it
    # (silver__job_posting, job_engagement_terms). See migration 0007's
    # downgrade for the same lesson.
