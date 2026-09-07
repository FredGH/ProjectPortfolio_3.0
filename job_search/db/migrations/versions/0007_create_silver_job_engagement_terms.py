"""create silver.job_engagement_terms

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-07

silver.job_engagement_terms is SHARED job-posting data (PLAN.md's
two-zone rule, same pattern as bronze.raw_jobs (0004) and target_company
(0005)): the engagement/IR35/rate classification of a posting is the same
for every user, so it carries no user_id and has no row-level security.

Written only by the migration/owner role, via the
`enrich-engagement-terms` pipeline CLI subcommand (core.enrichment.
engagement_terms) — never by a live per-user request. Read only by dbt
(silver__job_posting, PLAN.md Step 5a), which also connects as owner
(dbt/profiles.yml) — no job_search_app grant is needed here, unlike
target_company, which the API writes to directly.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS silver")

    op.create_table(
        "job_engagement_terms",
        sa.Column("job_key", sa.Text(), primary_key=True),
        sa.Column("engagement_type", sa.Text(), nullable=False),
        sa.Column("ir35_status", sa.Text(), nullable=False),
        sa.Column("engagement_vehicle", sa.Text(), nullable=False),
        sa.Column("rate_basis", sa.Text(), nullable=False),
        sa.Column("rate_currency", sa.Text(), nullable=True),
        sa.Column("rate_annualised_gbp", sa.Numeric(), nullable=True),
        sa.Column("rate_daily_gbp_equivalent", sa.Numeric(), nullable=True),
        sa.Column("contract_length_months", sa.Integer(), nullable=True),
        sa.Column("extension_likelihood", sa.Text(), nullable=False),
        sa.Column(
            "extracted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "engagement_type IN "
            "('permanent', 'contract', 'ftc', 'interim', 'unknown')",
            name="ck_job_engagement_terms_engagement_type",
        ),
        sa.CheckConstraint(
            "ir35_status IN "
            "('inside', 'outside', 'not_applicable', 'undetermined', 'unknown')",
            name="ck_job_engagement_terms_ir35_status",
        ),
        sa.CheckConstraint(
            "engagement_vehicle IN "
            "('umbrella', 'limited', 'paye', 'agency_paye', 'unknown')",
            name="ck_job_engagement_terms_engagement_vehicle",
        ),
        sa.CheckConstraint(
            "rate_basis IN ('annual', 'daily', 'hourly', 'unknown')",
            name="ck_job_engagement_terms_rate_basis",
        ),
        sa.CheckConstraint(
            "extension_likelihood IN " "('likely', 'possible', 'unlikely', 'unstated')",
            name="ck_job_engagement_terms_extension_likelihood",
        ),
        schema="silver",
    )


def downgrade() -> None:
    op.drop_table("job_engagement_terms", schema="silver")
    op.execute("DROP SCHEMA IF EXISTS silver")
