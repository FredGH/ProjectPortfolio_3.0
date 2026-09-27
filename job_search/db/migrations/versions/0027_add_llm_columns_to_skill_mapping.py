"""Add the LLM pre-review columns and the 'llm' method to skill_mapping.

An LLM pre-review (`core.skills.llm_map`) either applies a high-confidence
ESCO match — stored as `method = 'llm'`, `review_status` NULL, so it shows up
in the "Auto-matches — verify" list — or records its verdict on a still-open
row. `llm_checked_at` marks a string as already asked so it is not sent again.

`job_search_app` already has UPDATE on `silver.skill_mapping` (0023), so no
new grants are needed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_skill_mapping_method", "skill_mapping", schema="silver", type_="check"
    )
    op.create_check_constraint(
        "ck_skill_mapping_method",
        "skill_mapping",
        "method IN ('alias', 'label', 'embedding', 'llm', 'none')",
        schema="silver",
    )
    op.add_column("skill_mapping", sa.Column("llm_verdict", sa.Text()), schema="silver")
    op.add_column(
        "skill_mapping", sa.Column("llm_custom_label", sa.Text()), schema="silver"
    )
    op.add_column("skill_mapping", sa.Column("llm_note", sa.Text()), schema="silver")
    op.add_column(
        "skill_mapping",
        sa.Column("llm_checked_at", sa.DateTime(timezone=True)),
        schema="silver",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE silver.skill_mapping SET method = 'embedding' WHERE method = 'llm'"
    )
    for column in ("llm_checked_at", "llm_note", "llm_custom_label", "llm_verdict"):
        op.drop_column("skill_mapping", column, schema="silver")
    op.drop_constraint(
        "ck_skill_mapping_method", "skill_mapping", schema="silver", type_="check"
    )
    op.create_check_constraint(
        "ck_skill_mapping_method",
        "skill_mapping",
        "method IN ('alias', 'label', 'embedding', 'none')",
        schema="silver",
    )
