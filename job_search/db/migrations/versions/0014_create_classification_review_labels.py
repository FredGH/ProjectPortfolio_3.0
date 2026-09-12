"""create classification.category_review_labels

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-12

classification.category_review_labels holds the human hand-check
verdicts JOB-170 (Step 11a's "Done when" — hand-check 100
classifications, require >=90% agreement) requires. It is SHARED data
(the same review facts for every user of this project), so no
user_id, no RLS — same two-zone reasoning as dedup.pair_labels (0010).

Unlike dedup.pair_labels, this table does NOT persist an "agrees"
boolean: `gold.dim_job.category`/`seniority_band` can change on a
later `classify-jobs` rerun (silver.job_category is UPSERTed, per
0013's docstring), so a stored agreement flag would silently go stale.
Agreement is instead computed live, by joining this table to
`gold.dim_job` at read time (see GET /classification/review-summary).

Written directly by the FastAPI request-serving layer (the new
Categorisation Review page), so — following dedup.pair_labels'
precedent (0010) — job_search_app gets SELECT/INSERT/UPDATE, not just
SELECT.

This migration ALSO grants job_search_app schema-level access to
`gold` for the first time: every gold table (dim_job, dim_company,
fct_market_demand, all created by dbt as job_search_owner) has so far
only ever been read by dbt itself. The new review endpoints are the
first request-serving code to read gold.dim_job directly, so this
grants USAGE on the schema and SELECT on every table currently in it,
plus `ALTER DEFAULT PRIVILEGES` — dbt's `table` materialization does
DROP+CREATE on every `dbt run`, which would otherwise silently wipe a
one-time GRANT (see 0010's docstring for the same reasoning, verified
live against this project's Postgres instance).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS classification")
    op.execute("GRANT USAGE ON SCHEMA classification TO job_search_app")

    # First request-serving read of `gold` — grant schema access now,
    # same pattern (and same reasoning) as 0010's silver/dedup grants.
    op.execute("GRANT USAGE ON SCHEMA gold TO job_search_app")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA gold TO job_search_app")
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE job_search_owner IN SCHEMA gold "
        "GRANT SELECT ON TABLES TO job_search_app"
    )

    op.create_table(
        "category_review_labels",
        sa.Column("job_group_id", sa.Text(), primary_key=True),
        sa.Column("reviewed_category", sa.Text(), nullable=False),
        sa.Column("reviewed_seniority_band", sa.Text(), nullable=False),
        sa.Column("reviewed_by", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "reviewed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "reviewed_category IN ("
            "'software_engineer', 'data_engineer', 'data_scientist', "
            "'ai_ml_engineer', 'analytics_engineer', 'platform_devops', "
            "'other')",
            name="ck_category_review_labels_reviewed_category",
        ),
        sa.CheckConstraint(
            "reviewed_seniority_band IN "
            "('junior', 'mid', 'senior', 'lead', 'principal')",
            name="ck_category_review_labels_reviewed_seniority_band",
        ),
        schema="classification",
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON classification.category_review_labels "
        "TO job_search_app"
    )


def downgrade() -> None:
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE ON classification.category_review_labels "
        "FROM job_search_app"
    )
    op.drop_table("category_review_labels", schema="classification")

    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE job_search_owner IN SCHEMA gold "
        "REVOKE SELECT ON TABLES FROM job_search_app"
    )
    op.execute("REVOKE SELECT ON ALL TABLES IN SCHEMA gold FROM job_search_app")
    op.execute("REVOKE USAGE ON SCHEMA gold FROM job_search_app")

    op.execute("REVOKE USAGE ON SCHEMA classification FROM job_search_app")
    # Safe to drop outright (unlike silver/dedup): this migration is the
    # only owner of the schema — no dbt model writes into `classification`.
    op.execute("DROP SCHEMA IF EXISTS classification")
