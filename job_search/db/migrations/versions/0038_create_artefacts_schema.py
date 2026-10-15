"""create artefacts schema (PLAN.md Step 19)

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-15

Backs docs/superpowers/specs/2026-10-15-step19-cover-letter-pitch-design.md:

- artefacts.artefact_cover_letter / artefacts.artefact_pitch: one row per
  generation, keyed on (user_id, job_group_id); `content` holds the document.
- artefacts.artefact_open_sentence: sentences still untraced after the
  retries, awaiting the user's link/remove decision.

All three tables are RLS-isolated per user (same policy pattern as
tailoring, migration 0032): letters and pitches are built from one user's CV.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    """Enable RLS and add the standard app.current_user_id policy.

    Args:
        table: The unqualified table name, in the `artefacts` schema.
    """
    op.execute(f"ALTER TABLE artefacts.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_isolation ON artefacts.{table} "
        "USING (user_id = current_setting('app.current_user_id', true)::uuid)"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON artefacts.{table} TO job_search_app")


def _artefact_table(name: str) -> None:
    """Create one artefact table (letter or pitch) with its index and RLS.

    Args:
        name: `artefact_cover_letter` or `artefact_pitch`.
    """
    op.create_table(
        name,
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False
        ),
        sa.Column("job_group_id", sa.Text(), nullable=False),
        sa.Column("truth_base_version", sa.Integer(), nullable=False),
        sa.Column("intel_version", sa.Integer(), nullable=True),
        sa.Column("target_title", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="needs_review"),
        sa.Column("content", JSONB(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("writer_model", sa.Text(), nullable=True),
        sa.Column("writer_prompt_version", sa.Text(), nullable=True),
        sa.Column("critic_model", sa.Text(), nullable=True),
        sa.Column("critic_prompt_version", sa.Text(), nullable=True),
        sa.Column("usage", JSONB(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('needs_review', 'approved', 'failed')",
            name=f"ck_{name}_status",
        ),
        schema="artefacts",
    )
    op.create_index(
        f"ix_{name}_user_job_created",
        name,
        ["user_id", "job_group_id", "created_at"],
        schema="artefacts",
    )
    _rls(name)


def upgrade() -> None:
    """Create the artefacts schema and its three tables."""
    op.execute("CREATE SCHEMA IF NOT EXISTS artefacts")
    op.execute("GRANT USAGE ON SCHEMA artefacts TO job_search_app")
    _artefact_table("artefact_cover_letter")
    _artefact_table("artefact_pitch")

    op.create_table(
        "artefact_open_sentence",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False
        ),
        sa.Column("artefact_kind", sa.Text(), nullable=False),
        sa.Column("artefact_id", UUID(as_uuid=True), nullable=False),
        sa.Column("sentence_id", sa.Text(), nullable=False),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "claimed_sources",
            ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("issue", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("evidence_ref", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "artefact_kind IN ('cover_letter', 'pitch')",
            name="ck_open_sentence_artefact_kind",
        ),
        sa.CheckConstraint(
            "kind IN ('orphan', 'unsupported')", name="ck_open_sentence_kind"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'linked', 'removed')",
            name="ck_open_sentence_status",
        ),
        schema="artefacts",
    )
    op.create_index(
        "ix_open_sentence_artefact",
        "artefact_open_sentence",
        ["artefact_id"],
        schema="artefacts",
    )
    _rls("artefact_open_sentence")


def downgrade() -> None:
    """Drop the three tables and the schema."""
    op.drop_index(
        "ix_open_sentence_artefact",
        table_name="artefact_open_sentence",
        schema="artefacts",
    )
    op.drop_table("artefact_open_sentence", schema="artefacts")
    for name in ("artefact_pitch", "artefact_cover_letter"):
        op.drop_index(
            f"ix_{name}_user_job_created", table_name=name, schema="artefacts"
        )
        op.drop_table(name, schema="artefacts")
    op.execute("DROP SCHEMA IF EXISTS artefacts")
