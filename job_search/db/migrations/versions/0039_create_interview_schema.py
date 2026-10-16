"""create interview schema (PLAN.md Step 20)

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-15

Backs docs/superpowers/specs/2026-10-15-step20-interview-question-bank-design.md:

- interview.question_bank: SHARED (no RLS, like intel): questions and model
  answers per qa_category, generated once and amortised across users.
- interview.user_question_progress: per user (RLS): personal answer, SM-2
  state and the CV-gap score.
- interview.personal_answer_open_sentence: per user (RLS): untraced sentences
  in a drafted personal answer, awaiting the user's link/remove decision.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def _now() -> sa.TextClause:
    """Return the `now()` server default.

    Returns:
        A SQL text clause.
    """
    return sa.text("now()")


def _rls(table: str) -> None:
    """Enable RLS and add the standard app.current_user_id policy.

    Args:
        table: The unqualified table name, in the `interview` schema.
    """
    op.execute(f"ALTER TABLE interview.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_isolation ON interview.{table} "
        "USING (user_id = current_setting('app.current_user_id', true)::uuid)"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON interview.{table} TO job_search_app")


def upgrade() -> None:
    """Create the interview schema and its three tables."""
    op.execute("CREATE SCHEMA IF NOT EXISTS interview")
    op.execute("GRANT USAGE ON SCHEMA interview TO job_search_app")

    op.create_table(
        "question_bank",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("qa_category", sa.Text(), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("difficulty", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("question_hash", sa.Text(), nullable=False),
        sa.Column("model_answer", sa.Text(), nullable=False),
        sa.Column(
            "tags", ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("source", sa.Text(), nullable=False, server_default="generated"),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("critic_issue", sa.Text(), nullable=True),
        sa.Column("writer_model", sa.Text(), nullable=True),
        sa.Column("writer_prompt_version", sa.Text(), nullable=True),
        sa.Column("critic_model", sa.Text(), nullable=True),
        sa.Column("critic_prompt_version", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_now(),
        ),
        sa.Column("last_reviewed", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('technical', 'behavioural')", name="ck_question_bank_kind"
        ),
        sa.CheckConstraint(
            "difficulty BETWEEN 1 AND 5", name="ck_question_bank_difficulty"
        ),
        sa.CheckConstraint(
            "status IN ('approved', 'needs_review', 'rejected')",
            name="ck_question_bank_status",
        ),
        sa.UniqueConstraint(
            "qa_category",
            "topic",
            "question_hash",
            name="uq_question_bank_category_topic_hash",
        ),
        schema="interview",
    )
    op.create_index(
        "ix_question_bank_category_topic",
        "question_bank",
        ["qa_category", "topic", "status"],
        schema="interview",
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON interview.question_bank TO job_search_app"
    )

    op.create_table(
        "user_question_progress",
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False
        ),
        sa.Column(
            "question_id",
            UUID(as_uuid=True),
            sa.ForeignKey("interview.question_bank.id"),
            nullable=False,
        ),
        sa.Column("personal_answer", sa.Text(), nullable=True),
        sa.Column("answer_status", sa.Text(), nullable=False, server_default="none"),
        sa.Column("ease_factor", sa.Float(), nullable=False, server_default="2.5"),
        sa.Column("interval_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("repetitions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_review", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_quality", sa.Integer(), nullable=True),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cv_gap_score", sa.Float(), nullable=True),
        sa.Column("truth_base_version_scored", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("user_id", "question_id"),
        sa.CheckConstraint(
            "answer_status IN ('none', 'drafted', 'needs_review', 'written')",
            name="ck_progress_answer_status",
        ),
        schema="interview",
    )
    _rls("user_question_progress")

    op.create_table(
        "personal_answer_open_sentence",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False
        ),
        sa.Column(
            "question_id",
            UUID(as_uuid=True),
            sa.ForeignKey("interview.question_bank.id"),
            nullable=False,
        ),
        sa.Column("sentence_id", sa.Text(), nullable=False),
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
            server_default=_now(),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'linked', 'removed')",
            name="ck_personal_open_sentence_status",
        ),
        schema="interview",
    )
    op.create_index(
        "ix_personal_open_sentence_question",
        "personal_answer_open_sentence",
        ["user_id", "question_id"],
        schema="interview",
    )
    _rls("personal_answer_open_sentence")


def downgrade() -> None:
    """Drop the three tables and the schema."""
    op.drop_table("personal_answer_open_sentence", schema="interview")
    op.drop_table("user_question_progress", schema="interview")
    op.drop_table("question_bank", schema="interview")
    op.execute("DROP SCHEMA IF EXISTS interview")
