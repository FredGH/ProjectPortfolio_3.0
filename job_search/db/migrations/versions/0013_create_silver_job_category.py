"""create silver.job_category

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-09

silver.job_category is SHARED per-job classification data (PLAN.md
Step 11a): a job's category/seniority is the same for every user, so
no user_id, no RLS — same two-zone reasoning as job_survivorship
(0012).

Like job_survivorship (UPSERT, not insert-only): a classification is
not an identity decision the way job_group_id is (DECISIONS.md §2.6)
— re-running the classifier with a better seed set or an improved
rules table should be free to change a category, not locked in
forever.

Written only by the migration/owner role, via the `classify-jobs`
pipeline CLI subcommand (core.classification.write_job_category) —
never by a live per-user request. Read by dbt's gold.dim_job model as
a plain source().

job_search_app inherits SELECT here automatically via migration 0010's
`ALTER DEFAULT PRIVILEGES FOR ROLE job_search_owner IN SCHEMA silver`
rule — no explicit grant needed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_category",
        sa.Column("job_group_id", sa.Text(), primary_key=True),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("category_confidence", sa.Numeric(), nullable=False),
        sa.Column("category_method", sa.Text(), nullable=False),
        sa.Column("qa_category", sa.Text(), nullable=True),
        sa.Column("seniority_band", sa.Text(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "category_method IN ('rules', 'embedding', 'llm')",
            name="ck_job_category_category_method",
        ),
        schema="silver",
    )


def downgrade() -> None:
    op.drop_table("job_category", schema="silver")
