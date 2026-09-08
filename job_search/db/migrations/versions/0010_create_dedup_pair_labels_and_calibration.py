"""create dedup.pair_labels and dedup.calibration_thresholds

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-08

Both tables are SHARED job-pair data (PLAN.md's two-zone rule, same
pattern as dedup.job_blocking_keys (0008)): whether two postings are
the same job, and where the auto-match/auto-reject thresholds sit, are
the same facts for every user of this project — no user_id, no RLS.

Unlike earlier dedup.* tables (written only by pipeline CLI subcommands
via the owner role), these two are written directly by the FastAPI
request-serving layer (PLAN.md Step 9's review-queue and calibration
endpoints) — so, following target_company's precedent (migration
0005), job_search_app gets SELECT/INSERT/UPDATE, not just SELECT.

calibration_thresholds is append-only (no UPDATE path) — every
calibration run adds a row; the most recently calibrated_at row is
"current." This gives free history when Step 16 recalibrates later.

This migration ALSO grants job_search_app schema-level access to
`silver` and `dedup` for the first time — every prior table in both
schemas (silver.silver__job_posting, silver.job_engagement_terms,
dedup.job_blocking_keys, dedup.dedup__candidate_pairs, dedup.
dedup__similarity_scores, dedup.job_similarity_features, dedup.
pair_title_scores) has only ever been read by the owner role (dbt,
pipeline CLI subcommands) — job_search_app has never had USAGE on
either schema. Step 9's endpoints are the first request-serving code to
read silver__job_posting/dedup__similarity_scores directly, so this
migration grants USAGE on both schemas and SELECT on every table
currently in them.

Critically, this uses `ALTER DEFAULT PRIVILEGES FOR ROLE job_search_owner`,
not just one-time GRANTs on today's tables: dbt's `table` materialization
does DROP+CREATE on every `dbt run`, which silently wipes a one-time
GRANT on that exact table (Postgres does not carry privileges across a
DROP), while ALTER DEFAULT PRIVILEGES makes every *future* table
job_search_owner creates in these schemas automatically grant SELECT
to job_search_app — verified live in this session against this exact
Postgres instance (dropping and recreating a table after a one-time
GRANT does lose it; after ALTER DEFAULT PRIVILEGES, a freshly-created
table in the same schema has the grant already).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # job_search_app has never had access to either schema before this
    # migration — grant USAGE plus SELECT on every existing table, AND
    # a default-privileges rule so every FUTURE table job_search_owner
    # creates here (dbt rebuilds every table on every `dbt run`, which
    # would otherwise silently drop a one-time GRANT) keeps the grant.
    # Verified live against this exact Postgres instance: a table
    # dropped and recreated after a one-time GRANT loses it; after
    # ALTER DEFAULT PRIVILEGES, a freshly (re)created table has it
    # already.
    op.execute("GRANT USAGE ON SCHEMA silver TO job_search_app")
    op.execute("GRANT USAGE ON SCHEMA dedup TO job_search_app")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA silver TO job_search_app")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA dedup TO job_search_app")
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE job_search_owner IN SCHEMA silver "
        "GRANT SELECT ON TABLES TO job_search_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE job_search_owner IN SCHEMA dedup "
        "GRANT SELECT ON TABLES TO job_search_app"
    )

    op.create_table(
        "pair_labels",
        sa.Column("job_key_a", sa.Text(), primary_key=True),
        sa.Column("job_key_b", sa.Text(), primary_key=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column(
            "is_manual_override", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column("labeled_by", sa.Text(), nullable=True),
        sa.Column(
            "labeled_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "label IN ('match', 'not_match')", name="ck_pair_labels_label"
        ),
        schema="dedup",
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON dedup.pair_labels TO job_search_app")

    op.create_table(
        "calibration_thresholds",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("auto_match_threshold", sa.Numeric(), nullable=False),
        sa.Column("auto_reject_threshold", sa.Numeric(), nullable=False),
        sa.Column("measured_precision", sa.Numeric(), nullable=False),
        sa.Column("measured_recall", sa.Numeric(), nullable=False),
        sa.Column("labeled_pair_count", sa.Integer(), nullable=False),
        sa.Column("calibrated_by", sa.Text(), nullable=True),
        sa.Column(
            "calibrated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="dedup",
    )
    op.execute("GRANT SELECT, INSERT ON dedup.calibration_thresholds TO job_search_app")
    # calibration_thresholds.id is a SERIAL PK — INSERT with no explicit id
    # calls nextval() on its backing sequence, which needs its own GRANT;
    # table-level INSERT privilege does not imply sequence privilege.
    op.execute(
        "GRANT USAGE, SELECT ON SEQUENCE dedup.calibration_thresholds_id_seq "
        "TO job_search_app"
    )


def downgrade() -> None:
    op.execute(
        "REVOKE USAGE, SELECT ON SEQUENCE dedup.calibration_thresholds_id_seq "
        "FROM job_search_app"
    )
    op.execute(
        "REVOKE SELECT, INSERT ON dedup.calibration_thresholds FROM job_search_app"
    )
    op.drop_table("calibration_thresholds", schema="dedup")
    op.execute("REVOKE SELECT, INSERT, UPDATE ON dedup.pair_labels FROM job_search_app")
    op.drop_table("pair_labels", schema="dedup")

    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE job_search_owner IN SCHEMA dedup "
        "REVOKE SELECT ON TABLES FROM job_search_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE job_search_owner IN SCHEMA silver "
        "REVOKE SELECT ON TABLES FROM job_search_app"
    )
    op.execute("REVOKE SELECT ON ALL TABLES IN SCHEMA dedup FROM job_search_app")
    op.execute("REVOKE SELECT ON ALL TABLES IN SCHEMA silver FROM job_search_app")
    op.execute("REVOKE USAGE ON SCHEMA dedup FROM job_search_app")
    op.execute("REVOKE USAGE ON SCHEMA silver FROM job_search_app")
