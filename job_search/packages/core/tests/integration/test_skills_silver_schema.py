"""Integration tests for the silver skill tables (migration 0023)."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from tests.integration.skills_fixtures import (
    insert_job_skills,
    insert_mapping,
    live_app_engine,
    live_owner_engine,
    purge_fixtures,
)


class TestSkillMappingConstraints(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()

    def setUp(self) -> None:
        purge_fixtures(self.engine)
        self.suffix = uuid.uuid4().hex[:8]

    def tearDown(self) -> None:
        purge_fixtures(self.engine)

    def _insert(self, **kwargs) -> None:
        with self.engine.begin() as conn:
            insert_mapping(conn, f"zzfixture {self.suffix}", **kwargs)

    def _stored(self) -> tuple[str | None, str, str | None]:
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT skill_id, method, review_status "
                    "FROM silver.skill_mapping WHERE raw_norm = :raw_norm"
                ),
                {"raw_norm": f"zzfixture {self.suffix}"},
            ).one()
        return row.skill_id, row.method, row.review_status

    def test_accepts_an_unmapped_open_row(self) -> None:
        self._insert()
        self.assertEqual(self._stored(), (None, "none", "open"))

    def test_accepts_a_mapped_label_row_with_no_review_status(self) -> None:
        self._insert(skill_id="fixture-x", method="label", review_status=None)
        self.assertEqual(self._stored(), ("fixture-x", "label", None))

    def test_accepts_a_rejected_row_with_no_skill(self) -> None:
        self._insert(review_status="rejected", candidate_skill_id="fixture-x")
        self.assertEqual(self._stored(), (None, "none", "rejected"))

    def test_rejects_method_none_with_a_skill(self) -> None:
        with self.assertRaises(IntegrityError):
            self._insert(skill_id="fixture-x", method="none", review_status=None)

    def test_rejects_a_mapped_method_without_a_skill(self) -> None:
        with self.assertRaises(IntegrityError):
            self._insert(skill_id=None, method="label", review_status=None)

    def test_rejects_open_status_with_a_skill(self) -> None:
        with self.assertRaises(IntegrityError):
            self._insert(skill_id="fixture-x", method="label", review_status="open")

    def test_rejects_resolved_status_without_a_skill(self) -> None:
        with self.assertRaises(IntegrityError):
            self._insert(review_status="resolved")

    def test_rejects_an_unknown_review_status(self) -> None:
        with self.assertRaises(IntegrityError):
            self._insert(review_status="bogus")

    def test_rejects_an_unknown_method(self) -> None:
        with self.assertRaises(IntegrityError):
            self._insert(skill_id="fixture-x", method="magic", review_status=None)


class TestOtherSilverSkillTables(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = live_owner_engine()
        cls.app = live_app_engine()

    def setUp(self) -> None:
        purge_fixtures(self.engine)
        self.job = f"fixture-job-{uuid.uuid4().hex[:8]}"

    def tearDown(self) -> None:
        purge_fixtures(self.engine)

    def test_custom_skill_id_must_use_the_custom_prefix(self) -> None:
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO silver.custom_skill (skill_id, canonical_label) "
                        "VALUES ('not-custom', 'x')"
                    )
                )

    def test_alias_source_must_be_seed_or_review(self) -> None:
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO silver.skill_alias (alias_norm, skill_id, source) "
                        "VALUES ('zzfixture a', 'fixture-x', 'other')"
                    )
                )

    def test_job_skill_raw_rows_need_a_parent_extraction_row(self) -> None:
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO silver.job_skill_raw (job_group_id, "
                        "prompt_version, raw_skill, raw_norm, requirement_level) "
                        "VALUES (:job, 'local.v1', 'Python', 'python', 'must_have')"
                    ),
                    {"job": self.job},
                )

    def test_requirement_level_is_constrained(self) -> None:
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as conn:
                insert_job_skills(
                    conn, self.job, "local.v1", [("Python", "python", "maybe")]
                )

    def test_deleting_an_extraction_cascades_to_its_raw_skills(self) -> None:
        with self.engine.begin() as conn:
            insert_job_skills(
                conn, self.job, "local.v1", [("Python", "python", "must_have")]
            )
            conn.execute(
                text("DELETE FROM silver.job_skill_extraction WHERE job_group_id = :j"),
                {"j": self.job},
            )
            left = conn.execute(
                text(
                    "SELECT count(*) FROM silver.job_skill_raw WHERE job_group_id = :j"
                ),
                {"j": self.job},
            ).scalar_one()
        self.assertEqual(left, 0)

    def test_app_role_can_write_review_tables_but_not_extraction_tables(self) -> None:
        with self.app.begin() as conn:
            insert_mapping(conn, f"zzfixture app {self.job}")
        with self.assertRaises(DBAPIError):
            with self.app.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO silver.job_skill_extraction "
                        "(job_group_id, prompt_version) VALUES (:j, 'local.v1')"
                    ),
                    {"j": self.job},
                )


if __name__ == "__main__":
    unittest.main()
