"""create the scoring schema (PLAN.md Step 15)

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-27

Five tables for the scoring funnel:

- user_preference: per-user hard-filter settings (RLS). A missing field
  (NULL, or an empty array) means "no filter on this dimension" — never
  coerced to excluding or including everything (DECISIONS.md §2.13's
  never-default-unknown principle, applied here to preferences too).
- job_chunk_embedding: SHARED job-description chunks and their vectors,
  computed once. No RLS — same pattern as dedup.job_blocking_keys (0008):
  owner-role writes via a pipeline CLI subcommand, job_search_app gets
  SELECT only, dbt reads it as a plain source.
- cv_chunk_embedding: per-user CV chunks and their vectors (RLS) — CV
  embeddings are per-user and never shared (PLAN.md Step 15).
- job_score: per-user component scores (RLS), grain (user_id,
  job_group_id) — the silver table dbt's fct_job_score mart reads from.
- weight: per-user calibration weights (RLS), written by Step 16, read
  here with a documented default when no row exists yet.

scoring.job_label (Step 16's hand-labels) is NOT created here — it
belongs to Step 16's own migration.

The pgvector column type has no SQLAlchemy core equivalent without the
pgvector driver package, so `job_chunk_embedding.embedding` and
`cv_chunk_embedding.embedding` are added via raw DDL (matching 0022's
`esco.skill_embedding` style) instead of `op.create_table`.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    """Enable RLS and add the standard app.current_user_id policy.

    Args:
        table: The unqualified table name, in the `scoring` schema.
    """
    op.execute(f"ALTER TABLE scoring.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_isolation ON scoring.{table} "
        "USING (user_id = current_setting('app.current_user_id', true)::uuid)"
    )


def upgrade() -> None:
    """Create the `scoring` schema and its five tables."""
    op.execute("CREATE SCHEMA IF NOT EXISTS scoring")

    op.create_table(
        "user_preference",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app_user.id"),
            primary_key=True,
        ),
        sa.Column(
            "preferred_locations",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "remote_ok", sa.Text(), nullable=False, server_default="no_preference"
        ),
        sa.Column(
            "contract_types", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column(
            "excluded_ir35_statuses",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("min_seniority_band", sa.Text(), nullable=True),
        sa.Column("max_seniority_band", sa.Text(), nullable=True),
        sa.Column("min_salary_annual", sa.Numeric(), nullable=True),
        sa.Column("min_rate_daily", sa.Numeric(), nullable=True),
        sa.Column("max_posting_age_days", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "remote_ok IN ('required', 'preferred', 'no_preference', 'excluded')",
            name="ck_user_preference_remote_ok",
        ),
        sa.CheckConstraint(
            "min_seniority_band IS NULL OR min_seniority_band IN "
            "('junior', 'mid', 'senior', 'lead', 'principal')",
            name="ck_user_preference_min_seniority",
        ),
        sa.CheckConstraint(
            "max_seniority_band IS NULL OR max_seniority_band IN "
            "('junior', 'mid', 'senior', 'lead', 'principal')",
            name="ck_user_preference_max_seniority",
        ),
        schema="scoring",
    )
    _rls("user_preference")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON scoring.user_preference TO job_search_app"
    )

    op.create_table(
        "job_chunk_embedding",
        sa.Column("job_group_id", sa.Text(), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("job_group_id", "section", "chunk_index"),
        sa.CheckConstraint(
            "section IN ('company_blurb', 'responsibilities', 'requirements', "
            "'nice_to_have', 'benefits', 'other')",
            name="ck_job_chunk_embedding_section",
        ),
        schema="scoring",
    )
    op.execute(
        "ALTER TABLE scoring.job_chunk_embedding "
        "ADD COLUMN embedding vector(768) NOT NULL"
    )
    op.execute(
        "CREATE INDEX job_chunk_embedding_hnsw ON scoring.job_chunk_embedding "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute("GRANT SELECT ON scoring.job_chunk_embedding TO job_search_app")

    op.create_table(
        "cv_chunk_embedding",
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False
        ),
        sa.Column("cv_version", sa.Integer(), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("user_id", "cv_version", "section", "chunk_index"),
        sa.CheckConstraint(
            "section IN ('summary', 'experience', 'skills', 'education', "
            "'certifications', 'projects')",
            name="ck_cv_chunk_embedding_section",
        ),
        schema="scoring",
    )
    op.execute(
        "ALTER TABLE scoring.cv_chunk_embedding "
        "ADD COLUMN embedding vector(768) NOT NULL"
    )
    op.execute(
        "CREATE INDEX cv_chunk_embedding_hnsw ON scoring.cv_chunk_embedding "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    _rls("cv_chunk_embedding")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON scoring.cv_chunk_embedding "
        "TO job_search_app"
    )

    op.create_table(
        "job_score",
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False
        ),
        sa.Column("job_group_id", sa.Text(), nullable=False),
        sa.Column("hard_filter_passed", sa.Boolean(), nullable=False),
        sa.Column("vector_similarity_score", sa.Numeric(), nullable=True),
        sa.Column("reranker_score", sa.Numeric(), nullable=True),
        sa.Column("skill_coverage_score", sa.Numeric(), nullable=True),
        sa.Column("llm_fit_score", sa.Numeric(), nullable=True),
        sa.Column("llm_rationale", sa.Text(), nullable=True),
        sa.Column("llm_missing_skills", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("llm_stretch_flag", sa.Boolean(), nullable=True),
        sa.Column("final_score", sa.Numeric(), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column(
            "scored_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("user_id", "job_group_id"),
        schema="scoring",
    )
    _rls("job_score")
    op.execute("GRANT SELECT, INSERT, UPDATE ON scoring.job_score TO job_search_app")

    op.create_table(
        "weight",
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False
        ),
        sa.Column("component", sa.Text(), nullable=False),
        sa.Column("weight", sa.Numeric(), nullable=False),
        sa.Column(
            "fitted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("user_id", "component"),
        sa.CheckConstraint(
            "component IN ('vector_similarity', 'reranker', 'skill_coverage', "
            "'llm_fit')",
            name="ck_weight_component",
        ),
        schema="scoring",
    )
    _rls("weight")
    op.execute("GRANT SELECT, INSERT, UPDATE ON scoring.weight TO job_search_app")


def downgrade() -> None:
    """Drop the five `scoring` tables and the schema itself."""
    op.drop_table("weight", schema="scoring")
    op.drop_table("job_score", schema="scoring")
    op.drop_table("cv_chunk_embedding", schema="scoring")
    op.drop_table("job_chunk_embedding", schema="scoring")
    op.drop_table("user_preference", schema="scoring")
    op.execute("DROP SCHEMA IF EXISTS scoring")
