"""The Tailor: renders the truth base and job into a prompt, calls the
`cv_tailoring` LLM task, and parses the reply (Step 17).

The Tailor's output has no company, job-title or date fields, so it can
never change them — see core.tailoring.assemble.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.cv.schema import CVTruthBase
from core.llm import gateway
from core.llm.prompts import load_prompt
from core.llm.task_config import load_task_config
from core.llm.types import LLMAdapter
from core.tailoring.backends import Backend
from core.tailoring.schema import (
    JobContext,
    JobSkill,
    TailorExperience,
    TailorOutput,
    TailorOutputError,
    parse_tailor_output,
)

TASK = "cv_tailoring"
PROMPT_VERSION_NUMBER = 3
RETRY_PROMPT_VERSION_NUMBER = 1
"""The patch-style retry prompt (`<family>.retry.v1`)."""
MAX_TOKENS = 8192
"""Reply cap. A reply that hits it comes back truncated and is rejected —
a model stuck repeating itself must not be half-parsed. On Claude it is also
the budget adaptive thinking draws from, hence the headroom."""


@dataclass(frozen=True)
class TailorResult:
    """One successful Tailor call.

    Attributes:
        output: The parsed instructions (for a retry, the patch already
            merged over the previous output).
        model: The model that produced them.
        prompt_version: The prompt file version used, e.g. `local.v3` or
            `local.retry.v1`.
        input_tokens: Prompt tokens, as the provider reported them.
        output_tokens: Completion tokens, as the provider reported them.
    """

    output: TailorOutput
    model: str
    prompt_version: str
    input_tokens: int = 0
    output_tokens: int = 0


def render_truth_base(truth_base: CVTruthBase) -> str:
    """Render the truth base for the prompt, with roles numbered and every
    bullet's ID shown so the model can cite it.

    Args:
        truth_base: The user's truth base.

    Returns:
        A plain-text rendering.
    """
    lines: list[str] = [f"Headline: {truth_base.headline}"]
    if truth_base.summary:
        lines.append(f"Summary: {truth_base.summary}")
    lines.append("Experience:")
    for index, role in enumerate(truth_base.experience):
        lines.append(
            f"[{index}] {role.title} at {role.company} "
            f"({role.start or '?'} – {role.end or 'present'})"
        )
        for bullet in role.bullets:
            lines.append(f"  - ({bullet.bullet_id}) {bullet.text}")
    lines.append("Skills: " + ", ".join(skill.name for skill in truth_base.skills))
    return "\n".join(lines)


def render_job_skills(job_skills: list[JobSkill]) -> str:
    """Render a job's skills for the prompt.

    Args:
        job_skills: The job's skills.

    Returns:
        One `- label (level)` line per skill, or `- (none listed)`.
    """
    if not job_skills:
        return "- (none listed)"
    return "\n".join(
        f"- {skill.label} ({skill.requirement_level or 'unspecified'})"
        for skill in job_skills
    )


def render_feedback(feedback: list[str]) -> str:
    """Render retry feedback for the prompt.

    Args:
        feedback: Concrete problems from the previous attempt.

    Returns:
        An empty string when there is none, else a block listing them.
    """
    if not feedback:
        return ""
    listed = "\n".join(f"- {item}" for item in feedback)
    return f"\nFix these problems from your previous attempt:\n{listed}\n"


def merge_outputs(previous: TailorOutput, patch: TailorOutput) -> TailorOutput:
    """Apply a retry's patch over the previous attempt's output.

    Args:
        previous: The previous attempt's (merged) output.
        patch: The retry's reply: only the parts that change.

    Returns:
        The summary is the patch's if given, else the previous one. A role
        in the patch replaces that role's whole bullet list (in place);
        roles absent from the patch keep their previous bullets; a patch
        role the previous output lacked is appended. Skills are the patch's
        when non-empty, else the previous ones.
    """
    patched: dict[int, TailorExperience] = {}
    for entry in patch.experience:
        patched.setdefault(entry.truth_index, entry)
    experience: list[TailorExperience] = []
    placed: set[int] = set()
    for entry in previous.experience:
        replacement = patched.get(entry.truth_index)
        if replacement is None:
            experience.append(entry)
        elif entry.truth_index not in placed:
            experience.append(replacement)
            placed.add(entry.truth_index)
    experience.extend(e for i, e in patched.items() if i not in placed)
    return TailorOutput(
        summary=patch.summary if patch.summary is not None else previous.summary,
        experience=experience,
        skills=list(patch.skills) if patch.skills else list(previous.skills),
    )


def run_tailor(
    truth_base: CVTruthBase,
    job: JobContext,
    feedback: list[str],
    *,
    adapters: dict[str, LLMAdapter],
    config_path: Path | None = None,
    backend: Backend | None = None,
    previous: TailorOutput | None = None,
) -> TailorResult:
    """Ask the Tailor for per-bullet instructions.

    Args:
        truth_base: The user's truth base.
        job: The target job.
        feedback: Problems from the previous attempt, if any.
        adapters: LLM adapters keyed by provider.
        config_path: Task-config override (tests).
        backend: Where to run; None uses the `cv_tailoring` config's own
            provider, model and prompt family.
        previous: The previous attempt's output. When given, the patch-style
            retry prompt is used (no job description; the model returns only
            what must change) and its reply is merged over `previous`.

    Returns:
        The parsed result with the model and prompt version used.

    Raises:
        TailorOutputError: If the reply was truncated or unusable (its
            `spent` carries the call's model and token counts).
    """
    if backend is None:
        family = load_task_config(TASK, config_path).prompt_family
        overrides: dict[str, str] = {}
    else:
        family = backend.prompt_family
        overrides = {"provider": backend.provider, "model": backend.model}
    if previous is None:
        template = load_prompt(TASK, family, PROMPT_VERSION_NUMBER)
        prompt_version = f"{family}.v{PROMPT_VERSION_NUMBER}"
        prompt = template.format(
            cv_text=render_truth_base(truth_base),
            job_title=job.title_for_display or "",
            job_description=job.description,
            job_skills=render_job_skills(job.skills),
            feedback=render_feedback(feedback),
        )
    else:
        template = load_prompt(TASK, f"{family}.retry", RETRY_PROMPT_VERSION_NUMBER)
        prompt_version = f"{family}.retry.v{RETRY_PROMPT_VERSION_NUMBER}"
        prompt = template.format(
            cv_text=render_truth_base(truth_base),
            job_title=job.title_for_display or "",
            job_skills=render_job_skills(job.skills),
            previous_output=previous.model_dump_json(exclude_none=True),
            feedback=render_feedback(feedback),
        )
    response = gateway.complete(
        TASK,
        prompt,
        prompt_version=prompt_version,
        adapters=adapters,
        config_path=config_path,
        max_tokens=MAX_TOKENS,
        **overrides,
    )
    try:
        if response.truncated:
            raise TailorOutputError("the Tailor's reply hit the output cap (truncated)")
        output = parse_tailor_output(response.text)
        if previous is not None:
            output = merge_outputs(previous, output)
    except TailorOutputError as exc:
        # The tokens were spent even though the reply is unusable.
        exc.spent = (response.model, response.input_tokens, response.output_tokens)
        raise
    return TailorResult(
        output=output,
        model=response.model,
        prompt_version=prompt_version,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
    )
