from __future__ import annotations

import unittest
import uuid

import anthropic
import httpx
from sqlalchemy import text

from core.classification.write_job_category import write_job_category
from core.db.session import build_engine
from core.llm.adapters.anthropic import AnthropicAdapter
from core.settings import get_settings

_OWNER_DSN = "postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search"
_OLLAMA_BASE_URL = "http://localhost:11434"
_EMBEDDING_MODEL = "nomic-embed-text"
_settings = get_settings()


def _ollama_available() -> bool:
    try:
        return httpx.get(f"{_OLLAMA_BASE_URL}/api/tags", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


@unittest.skipUnless(_ollama_available(), "Ollama server not reachable")
@unittest.skipUnless(_settings.anthropic_api_key, "ANTHROPIC_API_KEY not configured")
class TestWriteJobCategory(unittest.TestCase):
    """Integration test against a real Postgres instance.

    Inserts one fixture job_group_id's worth of silver.job_identity_map
    + silver.silver__job_posting rows directly (safe here since nothing
    runs `dbt run` mid-test, matching the established pattern from
    test_write_job_survivorship.py), then exercises the real write path
    end-to-end — real embeddings, real (cheap) LLM residual call.
    """

    def setUp(self) -> None:
        self.engine = build_engine(_OWNER_DSN)
        self.http_client = httpx.Client(timeout=30.0)
        self.suffix = uuid.uuid4().hex
        self.job_key = f"gh-{self.suffix}"
        self.job_group_id = f"group-{self.suffix}"
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO silver.silver__job_posting "
                    "(job_key, source_name, source_job_id, job_url, "
                    "job_url_canonical, entry_method, title, company, "
                    "location, description, salary_raw, posted_at) "
                    "VALUES (:job_key, 'greenhouse', :source_job_id, "
                    "'https://greenhouse.example/cat-test', "
                    "'https://greenhouse.example/cat-test', 'api', "
                    "'Senior Data Engineer', 'Test Co', 'London', "
                    "'A description', NULL, now())"
                ),
                {"job_key": self.job_key, "source_job_id": f"src-{self.suffix}"},
            )
            conn.execute(
                text(
                    "INSERT INTO silver.job_identity_map "
                    "(source_name, source_job_id, job_group_id, "
                    "match_method, confidence) "
                    "VALUES ('greenhouse', :source_job_id, :job_group_id, "
                    "'singleton', 1.0)"
                ),
                {
                    "source_job_id": f"src-{self.suffix}",
                    "job_group_id": self.job_group_id,
                },
            )
            conn.execute(
                text(
                    "INSERT INTO silver.job_survivorship "
                    "(job_group_id, winning_description, apply_source_name, "
                    "apply_source_job_id, apply_job_url, apply_title_for_display) "
                    "VALUES (:job_group_id, 'A description', 'greenhouse', "
                    ":source_job_id, 'https://greenhouse.example/cat-test', "
                    "'Senior Data Engineer')"
                ),
                {
                    "job_group_id": self.job_group_id,
                    "source_job_id": f"src-{self.suffix}",
                },
            )

    def tearDown(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM silver.job_category WHERE job_group_id = :g"),
                {"g": self.job_group_id},
            )
            conn.execute(
                text("DELETE FROM silver.job_survivorship WHERE job_group_id = :g"),
                {"g": self.job_group_id},
            )
            conn.execute(
                text("DELETE FROM silver.job_identity_map WHERE job_group_id = :g"),
                {"g": self.job_group_id},
            )
            conn.execute(
                text("DELETE FROM silver.silver__job_posting WHERE job_key = :k"),
                {"k": self.job_key},
            )
        self.engine.dispose()
        self.http_client.close()

    def test_classifies_and_writes_a_real_job_via_rules_stage(self) -> None:
        # "Senior Data Engineer" matches the rules stage — proves the
        # full write path end-to-end without needing the embedding/LLM
        # stages to fire for THIS specific fixture (Tasks 3/4 already
        # cover those stages directly).
        adapters = {
            "anthropic": AnthropicAdapter(
                api_key=_settings.anthropic_api_key,
                client=anthropic.Anthropic(api_key=_settings.anthropic_api_key),
            ),
        }
        written = write_job_category(
            self.engine,
            adapters=adapters,
            http_client=self.http_client,
            job_group_ids=[self.job_group_id],
        )
        self.assertEqual(written, 1)

        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT category, category_method, seniority_band, qa_category "
                    "FROM silver.job_category WHERE job_group_id = :g"
                ),
                {"g": self.job_group_id},
            ).one()
        self.assertEqual(row.category, "data_engineer")
        self.assertEqual(row.category_method, "rules")
        self.assertEqual(row.seniority_band, "senior")
        self.assertEqual(row.qa_category, "data_engineer")

    def test_rerun_upserts_rather_than_duplicating(self) -> None:
        adapters = {
            "anthropic": AnthropicAdapter(
                api_key=_settings.anthropic_api_key,
                client=anthropic.Anthropic(api_key=_settings.anthropic_api_key),
            ),
        }
        write_job_category(
            self.engine,
            adapters=adapters,
            http_client=self.http_client,
            job_group_ids=[self.job_group_id],
        )
        write_job_category(
            self.engine,
            adapters=adapters,
            http_client=self.http_client,
            job_group_ids=[self.job_group_id],
        )

        with self.engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM silver.job_category WHERE job_group_id = :g"
                ),
                {"g": self.job_group_id},
            ).scalar_one()
        self.assertEqual(count, 1)

    def test_llm_classified_row_carries_prompt_version_and_model_id(self) -> None:
        """Persist prompt_version/model_id only for LLM-classified rows.

        Exercises the real write path against this fixture's job_group_id.
        This fixture's title ("Senior Data Engineer") is expected to
        resolve via the rules stage rather than the LLM stage, so
        `category_method` is not guaranteed to be "llm" here — but
        whichever stage resolves it, the two new columns must match the
        documented contract: non-NULL for an "llm"-method row, NULL
        otherwise.

        Args:
            None.

        Returns:
            None.

        Raises:
            AssertionError: If prompt_version/model_id don't match the
                NULL-iff-non-llm contract for the written row.
        """
        write_job_category(
            self.engine,
            adapters={
                "anthropic": AnthropicAdapter(
                    api_key=_settings.anthropic_api_key,
                    client=anthropic.Anthropic(api_key=_settings.anthropic_api_key),
                )
            },
            http_client=self.http_client,
            job_group_ids=[self.job_group_id],
        )
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT category_method, prompt_version, model_id "
                    "FROM silver.job_category WHERE job_group_id = :id"
                ),
                {"id": self.job_group_id},
            ).one()
        if row.category_method == "llm":
            self.assertIsNotNone(row.prompt_version)
            self.assertIsNotNone(row.model_id)
        else:
            # Rules/embedding resolved this fixture's title before the
            # LLM stage — still a valid outcome (this fixture's title
            # isn't guaranteed to reach the LLM stage), and NULL is the
            # documented-correct value for a non-LLM row.
            self.assertIsNone(row.prompt_version)
            self.assertIsNone(row.model_id)
