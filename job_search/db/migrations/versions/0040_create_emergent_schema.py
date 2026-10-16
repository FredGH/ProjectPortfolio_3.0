"""create emergent schema (PLAN.md Step 21b)

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-16

Backs docs/superpowers/specs/2026-10-16-step21b-emergent-roles-design.md.
Shared (no RLS, derived and rebuildable): title_canonical, title_embedding,
cluster_run, title_cluster, role_candidate. Per user (RLS): emergent_role_review,
because a confirm/dismiss verdict with its rationale is the user's judgment.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def _now() -> sa.TextClause:
    """Return the `now()` server default.

    Returns:
        A SQL text clause.
    """
    return sa.text("now()")


def _uuid_pk() -> sa.Column:
    """Build a generated UUID primary-key column.

    Returns:
        The `id` column.
    """
    return sa.Column(
        "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def _grant(table: str) -> None:
    """Grant the app role read/write on a shared table.

    Args:
        table: The unqualified table name, in the `emergent` schema.
    """
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON emergent.{table} TO job_search_app")


def upgrade() -> None:
    """Create the emergent schema and its tables."""
    op.execute("CREATE SCHEMA IF NOT EXISTS emergent")
    op.execute("GRANT USAGE ON SCHEMA emergent TO job_search_app")

    op.create_table(
        "title_canonical",
        sa.Column("canonical_title", sa.Text(), primary_key=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("distinct_employer_count", sa.Integer(), nullable=False),
        sa.Column("distinct_source_count", sa.Integer(), nullable=False),
        sa.Column("postings", sa.Integer(), nullable=False),
        sa.Column("example_titles", ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        schema="emergent",
    )
    _grant("title_canonical")

    op.create_table(
        "title_embedding",
        sa.Column("canonical_title", sa.Text(), primary_key=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("vector", ARRAY(sa.Float()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        schema="emergent",
    )
    _grant("title_embedding")

    op.create_table(
        "cluster_run",
        _uuid_pk(),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("params", JSONB(), nullable=False),
        sa.Column("n_titles", sa.Integer(), nullable=False),
        sa.Column("n_clusters", sa.Integer(), nullable=False),
        sa.Column("n_noise", sa.Integer(), nullable=False),
        sa.Column("history_days", sa.Float(), nullable=False),
        sa.Column("mature", sa.Boolean(), nullable=False),
        sa.Column("maturity_note", sa.Text(), nullable=True),
        schema="emergent",
    )
    _grant("cluster_run")

    op.create_table(
        "title_cluster",
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("emergent.cluster_run.id"),
            nullable=False,
        ),
        sa.Column("cluster_id", sa.Integer(), nullable=False),
        sa.Column("canonical_titles", ARRAY(sa.Text()), nullable=False),
        sa.Column("n_titles", sa.Integer(), nullable=False),
        sa.Column("n_employers", sa.Integer(), nullable=False),
        sa.Column("centroid", ARRAY(sa.Float()), nullable=False),
        sa.Column("nearest_category", sa.Text(), nullable=True),
        sa.Column("nearest_cosine", sa.Float(), nullable=True),
        sa.Column("is_candidate", sa.Boolean(), nullable=False),
        sa.Column("candidate_note", sa.Text(), nullable=True),
        sa.Column("hybrid_a", sa.Text(), nullable=True),
        sa.Column("hybrid_weight_a", sa.Float(), nullable=True),
        sa.Column("hybrid_b", sa.Text(), nullable=True),
        sa.Column("hybrid_weight_b", sa.Float(), nullable=True),
        sa.Column("hybrid_gain", sa.Float(), nullable=True),
        sa.Column("novelty", sa.Float(), nullable=True),
        sa.Column("novelty_note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("run_id", "cluster_id"),
        schema="emergent",
    )
    _grant("title_cluster")

    op.create_table(
        "role_candidate",
        _uuid_pk(),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("emergent.cluster_run.id"),
            nullable=False,
        ),
        sa.Column("cluster_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("characterisation", sa.Text(), nullable=False),
        sa.Column("skill_signature", ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("corroboration", sa.Text(), nullable=False, server_default="uncorroborated"),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("usage", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.UniqueConstraint("run_id", "cluster_id", name="uq_role_candidate_run_cluster"),
        sa.CheckConstraint(
            "corroboration IN ('uncorroborated', 'corroborated')",
            name="ck_role_candidate_corroboration",
        ),
        schema="emergent",
    )
    _grant("role_candidate")

    op.create_table(
        "emergent_role_review",
        _uuid_pk(),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column(
            "candidate_id",
            UUID(as_uuid=True),
            sa.ForeignKey("emergent.role_candidate.id"),
            nullable=False,
        ),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.CheckConstraint(
            "verdict IN ('confirmed', 'dismissed')", name="ck_emergent_review_verdict"
        ),
        sa.CheckConstraint("length(trim(rationale)) > 0", name="ck_emergent_review_rationale"),
        schema="emergent",
    )
    op.create_index(
        "ix_emergent_review_user_candidate",
        "emergent_role_review",
        ["user_id", "candidate_id", "decided_at"],
        schema="emergent",
    )
    op.execute("ALTER TABLE emergent.emergent_role_review ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY emergent_role_review_isolation ON emergent.emergent_role_review "
        "USING (user_id = current_setting('app.current_user_id', true)::uuid)"
    )
    op.execute("GRANT SELECT, INSERT ON emergent.emergent_role_review TO job_search_app")


def downgrade() -> None:
    """Drop the tables and the schema."""
    for table in (
        "emergent_role_review",
        "role_candidate",
        "title_cluster",
        "cluster_run",
        "title_embedding",
        "title_canonical",
    ):
        op.drop_table(table, schema="emergent")
    op.execute("DROP SCHEMA IF EXISTS emergent")
