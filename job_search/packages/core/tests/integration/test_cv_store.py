"""Integration tests for core.cv.store against live Postgres."""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import text

from core.cv.schema import Bullet, CVTruthBase, Experience
from core.cv.store import (
    list_truth_base_history,
    read_truth_base,
    read_truth_base_version,
    write_truth_base,
)
from core.db.session import build_engine, session_scope
from core.settings import get_settings


def _live_migration_engine():
    settings = get_settings()
    engine = build_engine(settings.database_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — any connection failure means "skip"
        raise unittest.SkipTest(
            f"Postgres not reachable ({exc}); "
            "run `docker compose up -d postgres` first."
        ) from None
    return engine


def _sample_truth_base(identity: str) -> CVTruthBase:
    return CVTruthBase(
        identity=identity,
        headline="Senior Test Engineer",
        experience=[
            Experience(
                company="Fixture Corp",
                title="Test Engineer",
                start="2020-01",
                end=None,
                bullets=[Bullet(bullet_id="fixture01", text="Wrote fixtures.")],
            )
        ],
    )


class TestCvStore(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.migration_engine = _live_migration_engine()
        cls.app_engine = build_engine(get_settings().app_database_url)

    def setUp(self) -> None:
        with session_scope(self.migration_engine) as conn:
            self.user_id = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO app_user (id, email, display_name) "
                    "VALUES (:id, :email, 'Test User')"
                ),
                {"id": self.user_id, "email": f"{self.user_id}@example.com"},
            )

    def tearDown(self) -> None:
        with session_scope(self.migration_engine) as conn:
            conn.execute(
                text("DELETE FROM cv_truth_base_history WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM cv_truth_base WHERE user_id = :id"),
                {"id": self.user_id},
            )
            conn.execute(
                text("DELETE FROM app_user WHERE id = :id"), {"id": self.user_id}
            )

    def test_first_write_creates_version_1_and_no_prior_history(self) -> None:
        truth_base = _sample_truth_base("Jane Doe")
        version = write_truth_base(
            self.app_engine, self.user_id, "# Jane Doe CV", truth_base
        )
        self.assertEqual(version, 1)
        stored = read_truth_base(self.app_engine, self.user_id)
        assert stored is not None
        self.assertEqual(stored.version, 1)
        self.assertEqual(stored.truth_base, truth_base)

    def test_second_write_replaces_current_and_preserves_history(self) -> None:
        first = _sample_truth_base("Jane Doe")
        second = _sample_truth_base("Jane A. Doe")
        write_truth_base(self.app_engine, self.user_id, "# v1", first)
        version = write_truth_base(self.app_engine, self.user_id, "# v2", second)

        self.assertEqual(version, 2)
        current = read_truth_base(self.app_engine, self.user_id)
        assert current is not None
        self.assertEqual(current.version, 2)
        self.assertEqual(current.truth_base.identity, "Jane A. Doe")

        with session_scope(self.app_engine, user_id=self.user_id) as conn:
            history_versions = sorted(
                row.version
                for row in conn.execute(
                    text("SELECT version FROM cv_truth_base_history")
                ).all()
            )
        self.assertEqual(history_versions, [1, 2])

    def test_read_returns_none_when_no_truth_base_exists(self) -> None:
        self.assertIsNone(read_truth_base(self.app_engine, self.user_id))

    def test_rls_isolates_users(self) -> None:
        write_truth_base(
            self.app_engine, self.user_id, "# mine", _sample_truth_base("Jane Doe")
        )
        other_user_id = uuid.uuid4()
        self.assertIsNone(read_truth_base(self.app_engine, other_user_id))

    def test_write_persists_and_returns_the_given_label(self) -> None:
        write_truth_base(
            self.app_engine,
            self.user_id,
            "# v1",
            _sample_truth_base("Jane Doe"),
            label="Before I added the AI section",
        )
        current = read_truth_base(self.app_engine, self.user_id)
        assert current is not None
        self.assertEqual(current.label, "Before I added the AI section")

    def test_write_without_a_label_defaults_to_none(self) -> None:
        write_truth_base(
            self.app_engine, self.user_id, "# v1", _sample_truth_base("Jane Doe")
        )
        current = read_truth_base(self.app_engine, self.user_id)
        assert current is not None
        self.assertIsNone(current.label)

    def test_list_history_returns_every_version_newest_first(self) -> None:
        write_truth_base(
            self.app_engine,
            self.user_id,
            "# v1",
            _sample_truth_base("Jane Doe"),
            label="First draft",
        )
        write_truth_base(
            self.app_engine, self.user_id, "# v2", _sample_truth_base("Jane A. Doe")
        )

        history = list_truth_base_history(self.app_engine, self.user_id)

        self.assertEqual([entry.version for entry in history], [2, 1])
        self.assertIsNone(history[0].label)
        self.assertEqual(history[1].label, "First draft")

    def test_read_version_returns_that_specific_historical_snapshot(self) -> None:
        first = _sample_truth_base("Jane Doe")
        second = _sample_truth_base("Jane A. Doe")
        write_truth_base(self.app_engine, self.user_id, "# v1", first, label="v1")
        write_truth_base(self.app_engine, self.user_id, "# v2", second)

        old = read_truth_base_version(self.app_engine, self.user_id, 1)

        assert old is not None
        self.assertEqual(old.version, 1)
        self.assertEqual(old.label, "v1")
        self.assertEqual(old.truth_base.identity, "Jane Doe")

    def test_read_version_returns_none_for_an_unknown_version(self) -> None:
        write_truth_base(
            self.app_engine, self.user_id, "# v1", _sample_truth_base("Jane Doe")
        )
        self.assertIsNone(read_truth_base_version(self.app_engine, self.user_id, 99))


if __name__ == "__main__":
    unittest.main()
