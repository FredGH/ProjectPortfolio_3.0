"""Router tests for the Step 16 calibration endpoints. Uses the real
app with Step 22a's DEV_USER_ID-free override bypassed via
dependency_overrides, the same pattern test_api_whoami.py's second test
uses — a real ASGI request, not a mocked router.
"""

from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text
from tests.integration.skills_fixtures import live_owner_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

from app.main import app  # noqa: E402

from core.db.session import build_engine, get_current_user_id  # noqa: E402
from core.scoring.calibration import write_label  # noqa: E402
from core.settings import get_settings  # noqa: E402


class TestCalibrationRouter(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner_engine = live_owner_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'zzfixture router user')"
                ),
                {"id": self.user_id, "email": f"zzfixture-{self.user_id}@example.com"},
            )
        app.dependency_overrides[get_current_user_id] = lambda: self.user_id
        self.client = TestClient(app)

    def tearDown(self) -> None:
        del app.dependency_overrides[get_current_user_id]
        with self.owner_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM scoring.calibration_run WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM scoring.weight WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM scoring.job_label WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM scoring.job_score WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text(
                    "DELETE FROM gold.dim_job WHERE job_group_id LIKE "
                    "'zzfixture-router-%'"
                )
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def _insert_labeled_job(self, index: int, label: str) -> None:
        job_group_id = f"zzfixture-router-{index}"
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job (job_group_id, title_for_display, "
                    "company, location, engagement_type, description) "
                    "VALUES (:j, 'zzfixture role', 'zzfixture co', 'London', "
                    "'contract', 'zzfixture description')"
                ),
                {"j": job_group_id},
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score (user_id, job_group_id, "
                    "hard_filter_passed, vector_similarity_score, reranker_score, "
                    "skill_coverage_score, llm_fit_score, final_score, "
                    "embedding_model) "
                    "VALUES (:u, :j, true, :s, :s, :s, :llm, :s, 'nomic-embed-text')"
                ),
                {
                    "u": self.user_id,
                    "j": job_group_id,
                    "s": round(0.1 + 0.02 * index, 4),
                    "llm": round((0.1 + 0.02 * index) * 100, 2),
                },
            )
        write_label(self.app_engine, self.user_id, job_group_id, label)

    def test_labeling_candidate_returns_204_when_none_eligible(self) -> None:
        response = self.client.get("/scoring/labeling-candidate")
        self.assertEqual(response.status_code, 204)

    def test_labeling_candidate_returns_an_eligible_job(self) -> None:
        self._insert_labeled_job(0, "strong")  # already labeled — excluded
        job_group_id = "zzfixture-router-unlabeled"
        with self.owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO gold.dim_job (job_group_id, title_for_display, "
                    "company, location, engagement_type, description) "
                    "VALUES (:j, 'zzfixture role', 'zzfixture co', 'London', "
                    "'contract', 'zzfixture description')"
                ),
                {"j": job_group_id},
            )
            conn.execute(
                text(
                    "INSERT INTO scoring.job_score (user_id, job_group_id, "
                    "hard_filter_passed, vector_similarity_score, reranker_score, "
                    "skill_coverage_score, llm_fit_score, final_score) "
                    "VALUES (:u, :j, true, 0.5, 0.5, 0.5, 60, 0.5)"
                ),
                {"u": self.user_id, "j": job_group_id},
            )
        response = self.client.get("/scoring/labeling-candidate")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["job_group_id"], job_group_id)

    def test_put_label_then_get_labels_round_trips(self) -> None:
        response = self.client.put(
            "/scoring/labels/zzfixture-router-put", json={"label": "maybe"}
        )
        self.assertEqual(response.status_code, 200)
        labels = self.client.get("/scoring/labels").json()
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels[0]["label"], "maybe")

    def test_delete_label_removes_it(self) -> None:
        self.client.put("/scoring/labels/zzfixture-router-del", json={"label": "no"})
        response = self.client.delete("/scoring/labels/zzfixture-router-del")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.client.get("/scoring/labels").json(), [])

    def test_calibrate_returns_400_below_30_labels(self) -> None:
        self._insert_labeled_job(0, "strong")
        response = self.client.post("/scoring/calibrate")
        self.assertEqual(response.status_code, 400)

    def test_calibrate_then_save_then_history(self) -> None:
        for i in range(30):
            label = "strong" if i >= 20 else ("maybe" if i >= 10 else "no")
            self._insert_labeled_job(i, label)
        preview_response = self.client.post("/scoring/calibrate")
        self.assertEqual(preview_response.status_code, 200)
        preview = preview_response.json()
        self.assertEqual(preview["fit_count"], 20)
        self.assertEqual(preview["holdout_count"], 10)

        save_response = self.client.post(
            "/scoring/calibration-runs",
            json={"preview": preview, "calibrated_by": "zzfixture tester"},
        )
        self.assertEqual(save_response.status_code, 200)

        history = self.client.get("/scoring/calibration-runs").json()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["calibrated_by"], "zzfixture tester")

    def _valid_preview(self, **overrides: object) -> dict:
        preview = {
            "fit_count": 20,
            "holdout_count": 10,
            "weights": {
                "vector_similarity": 0.25,
                "reranker": 0.25,
                "skill_coverage": 0.25,
                "llm_fit": 0.25,
            },
            "holdout_agreement": 0.5,
            "embedding_model": "nomic-embed-text",
        }
        preview.update(overrides)
        return preview

    def test_save_calibration_rejects_weights_that_do_not_sum_to_one(self) -> None:
        preview = self._valid_preview(
            weights={
                "vector_similarity": 0.9,
                "reranker": 0.9,
                "skill_coverage": 0.9,
                "llm_fit": 0.9,
            }
        )
        response = self.client.post(
            "/scoring/calibration-runs", json={"preview": preview}
        )
        self.assertEqual(response.status_code, 422)

    def test_save_calibration_rejects_a_missing_component_key(self) -> None:
        preview = self._valid_preview(
            weights={
                "vector_similarity": 0.4,
                "reranker": 0.3,
                "skill_coverage": 0.3,
            }
        )
        response = self.client.post(
            "/scoring/calibration-runs", json={"preview": preview}
        )
        self.assertEqual(response.status_code, 422)

    def test_save_calibration_rejects_an_unknown_component_key(self) -> None:
        preview = self._valid_preview(
            weights={
                "vector_similarity": 0.25,
                "reranker": 0.25,
                "skill_coverage": 0.25,
                "not_a_real_component": 0.25,
            }
        )
        response = self.client.post(
            "/scoring/calibration-runs", json={"preview": preview}
        )
        self.assertEqual(response.status_code, 422)

    def test_save_calibration_rejects_a_negative_weight(self) -> None:
        preview = self._valid_preview(
            weights={
                "vector_similarity": 1.25,
                "reranker": 0.25,
                "skill_coverage": 0.25,
                "llm_fit": -0.75,
            }
        )
        response = self.client.post(
            "/scoring/calibration-runs", json={"preview": preview}
        )
        self.assertEqual(response.status_code, 422)

    def test_get_embedding_model_returns_the_configured_model(self) -> None:
        from core.settings import get_settings

        response = self.client.get("/scoring/embedding-model")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(), {"embedding_model": get_settings().embedding_model}
        )


if __name__ == "__main__":
    unittest.main()
