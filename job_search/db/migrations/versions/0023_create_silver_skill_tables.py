"""create the silver skill-normalisation tables

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-19

PLAN.md Step 14 (JOB-215). All five tables are SHARED-zone (docs/
tenancy.md: taxonomy and per-job extraction are the same facts for every
user), so no user_id and no RLS.

- custom_skill: skills ESCO lacks (`custom:<slug>`), created by the seed
  file or the review page.
- skill_alias: curated `alias_norm -> skill_id`; outranks ESCO labels.
- skill_mapping: one row per distinct normalised skill string, shared by
  CV and JD skills. `skill_id` is polymorphic (ESCO id or `custom:`), so
  it deliberately has no foreign key; dbt's `silver__skill` relationships
  test covers it. Invariants are CHECK constraints.
- job_skill_extraction / job_skill_raw: the LLM's per-job output. The
  extraction row exists even for a job that yielded zero skills, so it is
  not re-sent to the LLM every run.

Written by the owner role via pipeline CLI subcommands, except the three
review tables (custom_skill, skill_alias, skill_mapping), which the
review API writes — so job_search_app gets INSERT/UPDATE on those only.
SELECT on everything comes from 0010's default privileges on `silver`.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def upgrade() -> None:
    op.create_table(
        "custom_skill",
        sa.Column("skill_id", sa.Text(), primary_key=True),
        sa.Column("canonical_label", sa.Text(), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "skill_id LIKE 'custom:%'", name="ck_custom_skill_id_prefix"
        ),
        schema="silver",
    )
    op.create_table(
        "skill_alias",
        sa.Column("alias_norm", sa.Text(), primary_key=True),
        sa.Column("skill_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "source IN ('seed', 'review')", name="ck_skill_alias_source"
        ),
        schema="silver",
    )
    op.create_table(
        "skill_mapping",
        sa.Column("raw_norm", sa.Text(), primary_key=True),
        sa.Column("raw_example", sa.Text(), nullable=False),
        sa.Column("skill_id", sa.Text(), nullable=True),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("score", sa.Numeric(), nullable=True),
        sa.Column("candidate_skill_id", sa.Text(), nullable=True),
        sa.Column("candidate_score", sa.Numeric(), nullable=True),
        sa.Column("review_status", sa.Text(), nullable=True),
        sa.Column(
            "seen_in_cv", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "mapped_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "method IN ('alias', 'label', 'embedding', 'none')",
            name="ck_skill_mapping_method",
        ),
        sa.CheckConstraint(
            "(skill_id IS NULL) = (method = 'none')",
            name="ck_skill_mapping_none_iff_unmapped",
        ),
        sa.CheckConstraint(
            "review_status IS NULL OR review_status IN "
            "('open', 'rejected', 'resolved', 'dismissed')",
            name="ck_skill_mapping_review_status",
        ),
        sa.CheckConstraint(
            "review_status IS NULL OR review_status = 'resolved' "
            "OR skill_id IS NULL",
            name="ck_skill_mapping_unresolved_has_no_skill",
        ),
        sa.CheckConstraint(
            "review_status IS DISTINCT FROM 'resolved' OR skill_id IS NOT NULL",
            name="ck_skill_mapping_resolved_has_skill",
        ),
        schema="silver",
    )
    op.create_index(
        "ix_skill_mapping_review_status",
        "skill_mapping",
        ["review_status"],
        schema="silver",
    )
    op.create_table(
        "job_skill_extraction",
        sa.Column("job_group_id", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column(
            "extracted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("job_group_id", "prompt_version"),
        schema="silver",
    )
    op.create_table(
        "job_skill_raw",
        sa.Column("job_group_id", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("raw_skill", sa.Text(), nullable=False),
        sa.Column("raw_norm", sa.Text(), nullable=False),
        sa.Column("requirement_level", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("job_group_id", "prompt_version", "raw_norm"),
        sa.ForeignKeyConstraint(
            ["job_group_id", "prompt_version"],
            [
                "silver.job_skill_extraction.job_group_id",
                "silver.job_skill_extraction.prompt_version",
            ],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "requirement_level IN ('must_have', 'nice_to_have')",
            name="ck_job_skill_raw_requirement_level",
        ),
        schema="silver",
    )
    op.create_index(
        "ix_job_skill_raw_raw_norm", "job_skill_raw", ["raw_norm"], schema="silver"
    )

    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON silver.custom_skill, silver.skill_alias, "
        "silver.skill_mapping TO job_search_app"
    )


def downgrade() -> None:
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE ON silver.custom_skill, silver.skill_alias, "
        "silver.skill_mapping FROM job_search_app"
    )
    op.drop_index(
        "ix_job_skill_raw_raw_norm", table_name="job_skill_raw", schema="silver"
    )
    op.drop_table("job_skill_raw", schema="silver")
    op.drop_table("job_skill_extraction", schema="silver")
    op.drop_index(
        "ix_skill_mapping_review_status", table_name="skill_mapping", schema="silver"
    )
    op.drop_table("skill_mapping", schema="silver")
    op.drop_table("skill_alias", schema="silver")
    op.drop_table("custom_skill", schema="silver")
