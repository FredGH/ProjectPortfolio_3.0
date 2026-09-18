"""grant delete on cv_truth_base_history to job_search_app

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-18

core.cv.store.write_truth_base now prunes a label's own history down
to its last few versions (per-label retention, JOB-202) — 0017 only
granted job_search_app SELECT/INSERT on cv_truth_base_history, since
it was write-once/append-only until now.
"""

from __future__ import annotations

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT DELETE ON cv_truth_base_history TO job_search_app")


def downgrade() -> None:
    op.execute("REVOKE DELETE ON cv_truth_base_history FROM job_search_app")
