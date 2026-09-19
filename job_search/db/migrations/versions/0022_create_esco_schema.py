"""create the esco schema

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-19

The ESCO skills/occupations taxonomy (PLAN.md Step 14, JOB-215), loaded
from the bulk CSV release by `pipeline load-esco`. SHARED-zone reference
data: the same vocabulary for every user, so no user_id and no RLS
(docs/tenancy.md — "taxonomy" is shared).

Loader-owned: job_search_app gets SELECT only (the review API reads it to
search skills and label candidates); nothing request-serving writes here.

`esco.skill_embedding` is a derived, rebuildable cache — one vector per
skill, of its preferred label, at `nomic-embed-text`'s 768 dimensions,
with `embedding_model` recorded per row. It deliberately has NO ANN
index: an exact scan over ~14k rows is milliseconds, and DECISIONS.md
fixes the pgvector index type (and the embedding dimension for CV/JD
chunks) at Step 15. If Step 15 changes the model or dimension, this table
is rebuilt with `embed-esco`; no source data is lost.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS esco")
    op.execute("GRANT USAGE ON SCHEMA esco TO job_search_app")

    op.create_table(
        "skill",
        sa.Column("skill_id", sa.Text(), primary_key=True),
        sa.Column("concept_uri", sa.Text(), nullable=False, unique=True),
        sa.Column("preferred_label", sa.Text(), nullable=False),
        sa.Column("skill_type", sa.Text(), nullable=True),
        sa.Column("reuse_level", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        schema="esco",
    )
    op.create_table(
        "skill_label",
        sa.Column(
            "skill_id",
            sa.Text(),
            sa.ForeignKey("esco.skill.skill_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("label_norm", sa.Text(), nullable=False),
        sa.Column("is_preferred", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("label_norm", "skill_id"),
        schema="esco",
    )
    op.create_index(
        "ix_esco_skill_label_skill_id", "skill_label", ["skill_id"], schema="esco"
    )
    op.create_table(
        "occupation",
        sa.Column("occupation_id", sa.Text(), primary_key=True),
        sa.Column("concept_uri", sa.Text(), nullable=False, unique=True),
        sa.Column("preferred_label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        schema="esco",
    )
    op.create_table(
        "occupation_skill",
        sa.Column(
            "occupation_id",
            sa.Text(),
            sa.ForeignKey("esco.occupation.occupation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "skill_id",
            sa.Text(),
            sa.ForeignKey("esco.skill.skill_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("occupation_id", "skill_id", "relation_type"),
        schema="esco",
    )
    # The pgvector column type has no SQLAlchemy core equivalent without the
    # pgvector driver package, so this one table is raw DDL.
    op.execute(
        "CREATE TABLE esco.skill_embedding ("
        "skill_id TEXT PRIMARY KEY REFERENCES esco.skill (skill_id) "
        "ON DELETE CASCADE, "
        "embedding_model TEXT NOT NULL, "
        "embedding vector(768) NOT NULL)"
    )

    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA esco TO job_search_app")
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE job_search_owner IN SCHEMA esco "
        "GRANT SELECT ON TABLES TO job_search_app"
    )


def downgrade() -> None:
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE job_search_owner IN SCHEMA esco "
        "REVOKE SELECT ON TABLES FROM job_search_app"
    )
    op.execute("DROP TABLE esco.skill_embedding")
    op.drop_table("occupation_skill", schema="esco")
    op.drop_table("occupation", schema="esco")
    op.drop_index(
        "ix_esco_skill_label_skill_id", table_name="skill_label", schema="esco"
    )
    op.drop_table("skill_label", schema="esco")
    op.drop_table("skill", schema="esco")
    op.execute("REVOKE USAGE ON SCHEMA esco FROM job_search_app")
    op.execute("DROP SCHEMA esco")
