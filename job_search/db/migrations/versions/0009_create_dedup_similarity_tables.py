"""enable pg_trgm; create dedup.job_similarity_features and
dedup.pair_title_scores

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-07

Both tables are SHARED job-pair-generation data (PLAN.md's two-zone
rule), same pattern as dedup.job_blocking_keys (0008): written by the
migration/owner role via pipeline CLI subcommands, read by dbt as
plain sources, no user_id, no RLS.

job_similarity_features is per-job (one row per silver__job_posting
row): a description SimHash fingerprint and Step 6's parse_salary
output, both expensive/impossible to compute in SQL. pair_title_scores
is per-CANDIDATE-PAIR (one row per dedup__candidate_pairs row): title
token-set-ratio, the one Step 8 signal that is inherently pairwise
(rapidfuzz has no SQL equivalent) rather than precomputable per job.

description_simhash is declared BIGINT (signed 64-bit) even though a
SimHash fingerprint is naturally unsigned 64-bit — the write path
(core.dedup.write_similarity_features) converts to Postgres's signed
two's-complement representation before every INSERT. This loses no
information for the Hamming-distance bit operations Step 8's dbt model
runs on it (verified: XOR + bit_count is representation-invariant).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "job_similarity_features",
        sa.Column("job_key", sa.Text(), primary_key=True),
        sa.Column("description_simhash", sa.BigInteger(), nullable=False),
        sa.Column("rate_annualised", sa.Numeric(), nullable=True),
        sa.Column("rate_currency", sa.Text(), nullable=True),
        sa.Column("salary_band", sa.Text(), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="dedup",
    )

    op.create_table(
        "pair_title_scores",
        sa.Column("job_key_a", sa.Text(), primary_key=True),
        sa.Column("job_key_b", sa.Text(), primary_key=True),
        sa.Column("title_token_set_ratio", sa.Numeric(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="dedup",
    )


def downgrade() -> None:
    op.drop_table("pair_title_scores", schema="dedup")
    op.drop_table("job_similarity_features", schema="dedup")
    # pg_trgm is left enabled — dropping a shared extension on downgrade
    # risks breaking other objects that may come to depend on it; see
    # migration 0007's lesson about not tearing down shared resources.
