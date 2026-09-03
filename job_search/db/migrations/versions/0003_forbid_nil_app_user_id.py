"""forbid the nil UUID as a real app_user id

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-03

Why: `session_scope` (packages/core/core/db/session.py) sets
`app.current_user_id` to the nil UUID
(00000000-0000-0000-0000-000000000000) whenever no user is given, so that
every RLS policy keyed on that GUC matches zero rows — a fail-closed
sentinel. If a real `app_user` row ever existed with that same nil id, the
sentinel would stop being unmatchable: every UNSCOPED app-role session
would suddenly see that row, turning fail-closed into fail-into-the-nil-
tenant. This constraint makes the nil UUID permanently unmatchable as a
real `app_user.id`, so the sentinel can never collide with actual data.
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_app_user_id_not_nil",
        "app_user",
        "id <> '00000000-0000-0000-0000-000000000000'::uuid",
    )


def downgrade() -> None:
    op.drop_constraint("ck_app_user_id_not_nil", "app_user", type_="check")
