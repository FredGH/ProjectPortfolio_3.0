"""create intel schema (PLAN.md Step 19a)

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-14

Backs docs/superpowers/specs/2026-10-14-step19a-company-intel-design.md.
Company intelligence is shared data (no RLS), like gold.dim_job:

- intel.company_intel: append-only, one row per company per version.
- intel.crawl_cache: crawled page markdown, one row per URL per day.
- intel.job_company: which company a job is really for (employer / agency
  end client), automatic or a manual override.
- intel.job_competitive_angle: append-only per-job analysis, pinned to the
  intel version it was written from.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def _now() -> sa.TextClause:
    """Return the `now()` server default.

    Returns:
        A SQL text clause.
    """
    return sa.text("now()")


def upgrade() -> None:
    """Create the intel schema, its four tables and the app-role grants."""
    op.execute("CREATE SCHEMA IF NOT EXISTS intel")
    op.execute("GRANT USAGE ON SCHEMA intel TO job_search_app")

    op.create_table(
        "company_intel",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("company_key", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "researched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_now(),
        ),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column("pages", JSONB(), nullable=False),
        sa.Column("resolved_from_job", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("usage", JSONB(), nullable=False),
        sa.UniqueConstraint("company_key", "version", name="uq_company_intel_version"),
        sa.CheckConstraint(
            "status IN ('ok', 'no_content', 'failed')", name="ck_company_intel_status"
        ),
        schema="intel",
    )
    op.execute("GRANT SELECT, INSERT ON intel.company_intel TO job_search_app")

    op.create_table(
        "crawl_cache",
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("fetched_on", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("links", JSONB(), nullable=False, server_default="[]"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("url", "fetched_on", name="pk_crawl_cache"),
        schema="intel",
    )
    op.execute("GRANT SELECT, INSERT ON intel.crawl_cache TO job_search_app")

    op.create_table(
        "job_company",
        sa.Column("job_group_id", sa.Text(), primary_key=True),
        sa.Column("posted_company", sa.Text(), nullable=True),
        sa.Column("poster_kind", sa.Text(), nullable=False),
        sa.Column("end_client", sa.Text(), nullable=True),
        sa.Column("website_url", sa.Text(), nullable=True),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_now(),
        ),
        sa.CheckConstraint(
            "poster_kind IN ('employer', 'agency', 'unknown')",
            name="ck_job_company_poster_kind",
        ),
        sa.CheckConstraint(
            "method IN ('employer', 'llm', 'manual')", name="ck_job_company_method"
        ),
        schema="intel",
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON intel.job_company TO job_search_app")

    op.create_table(
        "job_competitive_angle",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("job_group_id", sa.Text(), nullable=False),
        sa.Column("company_key", sa.Text(), nullable=False),
        sa.Column("intel_version", sa.Integer(), nullable=False),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("usage", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_now(),
        ),
        schema="intel",
    )
    op.create_index(
        "ix_job_competitive_angle_job",
        "job_competitive_angle",
        ["job_group_id", "created_at"],
        schema="intel",
    )
    op.execute("GRANT SELECT, INSERT ON intel.job_competitive_angle TO job_search_app")


def downgrade() -> None:
    """Drop the four tables and the schema."""
    op.drop_index(
        "ix_job_competitive_angle_job",
        table_name="job_competitive_angle",
        schema="intel",
    )
    op.drop_table("job_competitive_angle", schema="intel")
    op.drop_table("job_company", schema="intel")
    op.drop_table("crawl_cache", schema="intel")
    op.drop_table("company_intel", schema="intel")
    op.execute("DROP SCHEMA IF EXISTS intel")
