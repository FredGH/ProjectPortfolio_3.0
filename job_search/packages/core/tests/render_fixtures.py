"""Shared invented fixture for the Step 18 rendering tests."""

from __future__ import annotations

from typing import Any

from core.cv.schema import Certification, Education, Skill
from core.tailoring.schema import (
    TailoredBullet,
    TailoredDocument,
    TailoredExperience,
    TailoredSummary,
)


def make_tailored_document(**overrides: Any) -> TailoredDocument:
    """Build a small invented `TailoredDocument`.

    Args:
        **overrides: Fields to replace on the default document.

    Returns:
        A document with a summary, two roles, skills, education and one
        certification. It names ELT and GCP in the summary and ELT and ETL
        in a bullet, to exercise first-use acronym expansion.
    """
    base = TailoredDocument(
        target_title="Lead Data Engineer",
        headline="Lead Data Engineer",
        identity="Zz Fixture",
        email="zz@example.com",
        phone="+00 000 000 000",
        linkedin_url="linkedin.com/in/zzfixture",
        nationality="Fixtureland",
        summary=TailoredSummary(
            text="Engineer building ELT pipelines on GCP.", origin="original"
        ),
        experience=[
            TailoredExperience(
                truth_index=0,
                company="Acme Bank",
                title="Senior Data Engineer",
                start="2019-01",
                end=None,
                bullets=[
                    TailoredBullet(
                        text="Built dbt models for risk reporting", origin="original"
                    ),
                    TailoredBullet(
                        text="Ran the ELT platform and wrote an ETL guide",
                        origin="reworded",
                        evidence_refs=["ref"],
                    ),
                ],
            ),
            TailoredExperience(
                truth_index=1,
                company="Beta Retail",
                title="Data Analyst",
                start="2015-06",
                end="2018-12",
                bullets=[TailoredBullet(text="Wrote SQL reports", origin="original")],
            ),
        ],
        skills=[Skill(name="dbt"), Skill(name="Airflow"), Skill(name="SQL")],
        education=[
            Education(
                institution="Zz University",
                qualification="BSc",
                start="2011",
                end="2014",
            )
        ],
        qualifications=[Certification(name="Fixture Cert", year=2020)],
    )
    return base.model_copy(update=overrides)
