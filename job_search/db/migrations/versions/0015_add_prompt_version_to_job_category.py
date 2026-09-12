"""add prompt_version/model_id to silver.job_category

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-12

Stamps every LLM-produced row in silver.job_category with the prompt
version and model that produced it (PLAN.md Step 12a, JOB-196:
"stamp prompt_version and model_id on every generated artefact row").
silver.job_category is the only table an LLM call actually produces
end-to-end today — every future artefact table (Steps 17, 19, 20) does
the same when it lands.

Both columns are nullable: a 'rules'- or 'embedding'-method row never
called an LLM, so both stay NULL for those rows by design, not by
omission.

No new grants needed — job_search_app already inherits SELECT on every
silver table via migration 0010's `ALTER DEFAULT PRIVILEGES FOR ROLE
job_search_owner IN SCHEMA silver` rule, and nothing outside the owner
role writes to job_category (see 0013's docstring).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_category",
        sa.Column("prompt_version", sa.Text(), nullable=True),
        schema="silver",
    )
    op.add_column(
        "job_category",
        sa.Column("model_id", sa.Text(), nullable=True),
        schema="silver",
    )


def downgrade() -> None:
    op.drop_column("job_category", "model_id", schema="silver")
    op.drop_column("job_category", "prompt_version", schema="silver")
