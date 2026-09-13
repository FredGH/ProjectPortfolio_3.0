"""add forward_deployed_engineer to category_review_labels' CHECK

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-13

Step 11b (JOB-444) adds forward_deployed_engineer as an 8th
classification-taxonomy value, extending Step 11a's original 7. The
CHECK constraint 0014 put on classification.category_review_labels.
reviewed_category is the one place that taxonomy is enforced at the
database layer, so it must grow alongside the Python-side taxonomy
(core.classification.llm_classifier._CATEGORIES, rules.py's regex, and
the dbt accepted_values tests on gold.dim_job) or a genuine reviewer
verdict of "forward_deployed_engineer" would be rejected with a raw
Postgres CheckViolation instead of persisting.

Postgres has no ALTER CHECK CONSTRAINT — a check constraint change is
always drop-and-recreate under the same name, never an ADD VALUE the
way an enum type would allow.
"""

from __future__ import annotations

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

_OLD_VALUES = (
    "'software_engineer', 'data_engineer', 'data_scientist', "
    "'ai_ml_engineer', 'analytics_engineer', 'platform_devops', "
    "'other'"
)
_NEW_VALUES = (
    "'software_engineer', 'data_engineer', 'data_scientist', "
    "'ai_ml_engineer', 'analytics_engineer', 'platform_devops', "
    "'forward_deployed_engineer', 'other'"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_category_review_labels_reviewed_category",
        "category_review_labels",
        schema="classification",
    )
    op.create_check_constraint(
        "ck_category_review_labels_reviewed_category",
        "category_review_labels",
        f"reviewed_category IN ({_NEW_VALUES})",
        schema="classification",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_category_review_labels_reviewed_category",
        "category_review_labels",
        schema="classification",
    )
    op.create_check_constraint(
        "ck_category_review_labels_reviewed_category",
        "category_review_labels",
        f"reviewed_category IN ({_OLD_VALUES})",
        schema="classification",
    )
