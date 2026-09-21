"""grant delete on silver.skill_alias to job_search_app

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-21

`core.skills.review.reopen` withdraws a human decision, which deletes the
`review` alias it created. 0023 only granted job_search_app
SELECT/INSERT/UPDATE on the skill review tables, since a decision could
not be undone until now. Only `skill_alias` needs DELETE: `reopen`
resets a `skill_mapping` row with an UPDATE.
"""

from __future__ import annotations

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT DELETE ON silver.skill_alias TO job_search_app")


def downgrade() -> None:
    op.execute("REVOKE DELETE ON silver.skill_alias FROM job_search_app")
