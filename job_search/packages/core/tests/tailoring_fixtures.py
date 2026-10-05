"""Shared fixtures for the Step 17 tailoring tests."""

from __future__ import annotations

from pathlib import Path

from core.cv.bullet_id import compute_bullet_id
from core.cv.schema import Bullet, CVTruthBase, Education, Experience, Skill


def _bullets(experience_index: int, texts: list[str]) -> list[Bullet]:
    """Build bullets with real, stable IDs.

    Args:
        experience_index: The role's index in the truth base.
        texts: The bullet texts.

    Returns:
        The bullets, each with its computed `bullet_id`.
    """
    return [
        Bullet(bullet_id=compute_bullet_id(experience_index, text), text=text)
        for text in texts
    ]


def make_truth_base() -> CVTruthBase:
    """Build a small two-role truth base used across tailoring tests.

    Returns:
        A `CVTruthBase` with a current role (index 0, two bullets), an
        older role (index 1, one bullet) and three canonical-id skills.
    """
    return CVTruthBase(
        identity="Zz Fixture",
        headline="Senior Data Engineer",
        email="zz@example.com",
        summary="Data engineer with eight years building analytics pipelines.",
        experience=[
            Experience(
                company="Acme Bank",
                title="Senior Data Engineer",
                start="2019-01",
                end=None,
                bullets=_bullets(
                    0,
                    [
                        "Built dbt models for risk reporting",
                        "Migrated nightly batch jobs to Airflow",
                    ],
                ),
                tech=["dbt", "Airflow"],
            ),
            Experience(
                company="Beta Retail",
                title="Data Analyst",
                start="2015-06",
                end="2018-12",
                bullets=_bullets(1, ["Wrote SQL reports for the finance team"]),
                tech=["SQL"],
            ),
        ],
        skills=[
            Skill(name="dbt", canonical_id="zzfixture-skill-dbt"),
            Skill(name="Airflow", canonical_id="zzfixture-skill-airflow"),
            Skill(name="SQL", canonical_id="zzfixture-skill-sql"),
        ],
        education=[Education(institution="Zz University", qualification="BSc")],
    )


def bullet_id(truth_base: CVTruthBase, experience_index: int, bullet_index: int) -> str:
    """Look up one bullet's ID.

    Args:
        truth_base: The truth base.
        experience_index: The role index.
        bullet_index: The bullet's position within that role.

    Returns:
        The bullet's `bullet_id`.
    """
    return truth_base.experience[experience_index].bullets[bullet_index].bullet_id


def write_pinned_task_config(
    directory: str,
    *,
    tailor_model: str = "llama3.1:8b",
    critic_model: str = "claude-sonnet-5",
) -> Path:
    """Write a task config pinning the Tailor/critic routing tests rely on.

    The Tailor goes to ollama (prompt family `local`) and the critic to
    anthropic (family `claude`), whatever the live `llm_tasks.yml` says, so
    tests with a fake `ollama` Tailor and a fake `anthropic` critic keep
    working when the live routing changes.

    Args:
        directory: Where to write the file.
        tailor_model: The model name pinned for `cv_tailoring`.
        critic_model: The model name pinned for `fabrication_critic`.

    Returns:
        The config file's path.
    """
    path = Path(directory) / "llm_tasks.yml"
    path.write_text(
        "tasks:\n"
        "  cv_tailoring:\n"
        "    provider: ollama\n"
        f"    model: {tailor_model}\n"
        "    prompt_family: local\n"
        "  fabrication_critic:\n"
        "    provider: anthropic\n"
        f"    model: {critic_model}\n"
        "    prompt_family: claude\n"
    )
    return path
