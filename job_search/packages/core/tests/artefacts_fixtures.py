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


def critic_items_in(prompt: str) -> list[dict]:
    """Extract the critic's JSON item list from its prompt.

    Args:
        prompt: A critic prompt.

    Returns:
        The decoded items.
    """
    start = prompt.index("[\n")
    end = prompt.rindex("\n]") + 2
    return json.loads(prompt[start:end])


class ScriptedAdapter:
    """An LLM adapter that plays the writer and the critic.

    A prompt containing "strict fact-checker" is a critic call; any other is a
    writer call.

    Attributes:
        prompts: Every prompt received, in order.
        writer_calls: How many writer prompts were received.
        critic_calls: How many critic prompts were received.
    """

    def __init__(
        self,
        writer_replies: list[str],
        *,
        reject: dict[str, str] | None = None,
        critic_text: str | None = None,
    ) -> None:
        """Script the adapter.

        Args:
            writer_replies: Writer replies in order (the last repeats).
            reject: Substring of a sentence to the issue the critic reports.
            critic_text: A fixed critic reply, overriding the verdicts.
        """
        self._writer = list(writer_replies)
        self._reject = reject or {}
        self._critic_text = critic_text
        self.prompts: list[str] = []
        self.writer_calls = 0
        self.critic_calls = 0

    def complete(self, *, model: str, prompt: str, **_kwargs: object):  # type: ignore[no-untyped-def]
        """Answer as the writer or the critic.

        Args:
            model: The requested model (echoed back).
            prompt: The prompt (recorded).
            **_kwargs: Ignored sampling options.

        Returns:
            An `LLMResponse` with fixed token counts.
        """
        from core.llm.types import LLMResponse

        self.prompts.append(prompt)
        if "strict fact-checker" in prompt:
            self.critic_calls += 1
            if self._critic_text is not None:
                text = self._critic_text
            else:
                verdicts: dict[str, str | None] = {}
                for item in critic_items_in(prompt):
                    issue = next(
                        (v for k, v in self._reject.items() if k in item["text"]),
                        None,
                    )
                    verdicts[item["id"]] = issue
                text = critic_reply_json(verdicts)
        else:
            text = self._writer[min(self.writer_calls, len(self._writer) - 1)]
            self.writer_calls += 1
        return LLMResponse(
            text=text,
            provider="anthropic",
            model=model,
            input_tokens=100,
            output_tokens=50,
        )


def padded_letter_json(**overrides: object) -> str:
    """Build a letter reply long enough to meet the word budget.

    Args:
        **overrides: Per-role sentence overrides (a `close` override replaces
            the padding).

    Returns:
        A writer-reply JSON string.
    """
    pad = [sentence_json("Plain words that fill the page " * 3, "voice")] * 12
    return letter_reply_json(**{"close": pad, **overrides})


def padded_pitch_json() -> str:
    """Build a pitch reply that meets each variant's word budget.

    Returns:
        A writer-reply JSON string.
    """
    pad = [
        sentence_json(
            "Plain words that fill the time. Plain words that fill the time.", "voice"
        )
    ]
    unit = pad[0]
    return pitch_reply_json(**{"30": [unit] * 5, "60": [unit] * 12, "90": [unit] * 17})
