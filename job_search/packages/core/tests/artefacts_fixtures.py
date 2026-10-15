"""Shared builders and scripted replies for the artefact tests."""

from __future__ import annotations

import json
from datetime import date

from tests.company_intel_fixtures import make_job
from tests.tailoring_fixtures import bullet_id, make_truth_base

from core.artefacts.context import EvidencePack, build_pack

TODAY = date(2026, 10, 15)


def make_pack(**overrides: object) -> EvidencePack:
    """Build an evidence pack from the fixture CV, job and two site facts.

    Args:
        **overrides: `site_facts` to replace the default facts (use `[]` for
            none).

    Returns:
        An `EvidencePack` for "Zz Fixture" applying for "Data Engineer".
    """
    site_facts = overrides.get(
        "site_facts",
        [
            ("business_model", 0, "Zzfixture Intel sells widgets to retailers."),
            ("tech_stack", 0, "Its data platform runs on Snowflake and dbt."),
        ],
    )
    return build_pack(
        make_truth_base(),
        make_job(title_for_display="Data Engineer", company="Zzfixture Intel Ltd"),
        site_facts=site_facts,  # type: ignore[arg-type]
    )


def cv_ref(experience_index: int, bullet_index: int) -> str:
    """Return the `cv:` source id of a fixture bullet.

    Args:
        experience_index: The role index.
        bullet_index: The bullet's position.

    Returns:
        e.g. `cv:abcd1234ef567890`.
    """
    return f"cv:{bullet_id(make_truth_base(), experience_index, bullet_index)}"


def sentence_json(
    text: str, kind: str = "claim", sources: list[str] | None = None
) -> dict:
    """Build one writer-reply sentence.

    Args:
        text: The sentence.
        kind: `claim` or `voice`.
        sources: The cited ids.

    Returns:
        The dict the writer is asked to return.
    """
    return {"text": text, "kind": kind, "sources": sources or []}


def letter_reply_json(**overrides: object) -> str:
    """Build a well-formed writer reply for a letter.

    Args:
        **overrides: Replace a paragraph's sentence list, keyed by role.

    Returns:
        A JSON string whose claims cite real fixture ids.
    """
    paragraphs = {
        "opening": [sentence_json("Your platform work caught my attention.", "voice")],
        "why_company": [
            sentence_json(
                "Zzfixture Intel sells widgets to retailers.",
                sources=["site:business_model:0", "job:posting"],
            )
        ],
        "evidence": [
            sentence_json(
                "I built dbt models for risk reporting.", sources=[cv_ref(0, 0)]
            ),
            sentence_json(
                "I migrated nightly batch jobs to Airflow.", sources=[cv_ref(0, 1)]
            ),
        ],
        "close": [sentence_json("I would welcome a conversation.", "voice")],
    }
    paragraphs.update(overrides)  # type: ignore[arg-type]
    return json.dumps(
        {"paragraphs": [{"role": r, "sentences": s} for r, s in paragraphs.items()]}
    )


def pitch_reply_json(**overrides: object) -> str:
    """Build a well-formed writer reply for a pitch.

    Args:
        **overrides: Replace a variant's sentence list, keyed by "30"/"60"/"90".

    Returns:
        A JSON string with three variants citing real fixture ids.
    """
    base = [
        sentence_json("I build dbt models for risk reporting.", sources=[cv_ref(0, 0)]),
        sentence_json("I moved batch jobs to Airflow.", sources=[cv_ref(0, 1)]),
    ]
    variants = {"30": list(base), "60": list(base), "90": list(base)}
    variants.update(overrides)  # type: ignore[arg-type]
    return json.dumps({"variants": variants})


def critic_reply_json(verdicts: dict[str, str | None]) -> str:
    """Build a critic reply.

    Args:
        verdicts: Sentence id to None (supported) or an issue text (not).

    Returns:
        A JSON string in the critic's reply shape.
    """
    return json.dumps(
        {
            "verdicts": [
                {"id": i, "supported": issue is None, "issue": issue or ""}
                for i, issue in verdicts.items()
            ]
        }
    )
