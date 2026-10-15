"""Shared builders and a scripted adapter for the interview tests."""

from __future__ import annotations

import json

from core.llm.types import LLMResponse


def batch_json(questions: list[tuple[str, str]], difficulty: int = 3) -> str:
    """Build a writer reply.

    Args:
        questions: `(question, model_answer)` pairs.
        difficulty: The difficulty of every question.

    Returns:
        A JSON string in the writer's reply shape.
    """
    return json.dumps(
        {
            "questions": [
                {
                    "question": q,
                    "model_answer": a,
                    "difficulty": difficulty,
                    "tags": ["sql"],
                }
                for q, a in questions
            ]
        }
    )


class BankAdapter:
    """An LLM adapter that plays the question writer and the critic.

    A prompt containing "strict technical reviewer" is a critic call; any
    other is a writer call.

    Attributes:
        prompts: Every prompt received, in order.
        writer_calls: Writer prompts received.
        critic_calls: Critic prompts received.
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
            reject: Substring of an answer to the issue the critic reports.
            critic_text: A fixed critic reply overriding the verdicts.
        """
        self._writer = list(writer_replies)
        self._reject = reject or {}
        self._critic_text = critic_text
        self.prompts: list[str] = []
        self.writer_calls = 0
        self.critic_calls = 0

    def complete(self, *, model: str, prompt: str, **_kwargs: object) -> LLMResponse:
        """Answer as the writer or the critic.

        Args:
            model: The requested model (echoed back).
            prompt: The prompt (recorded).
            **_kwargs: Ignored sampling options.

        Returns:
            An `LLMResponse` with fixed token counts.
        """
        self.prompts.append(prompt)
        if "strict technical reviewer" in prompt:
            self.critic_calls += 1
            if self._critic_text is not None:
                text = self._critic_text
            else:
                start, end = prompt.index("[\n"), prompt.rindex("\n]") + 2
                verdicts = []
                for item in json.loads(prompt[start:end]):
                    issue = next(
                        (
                            v
                            for k, v in self._reject.items()
                            if k in item["model_answer"]
                        ),
                        "",
                    )
                    verdicts.append(
                        {"id": item["id"], "supported": not issue, "issue": issue}
                    )
                text = json.dumps({"verdicts": verdicts})
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
