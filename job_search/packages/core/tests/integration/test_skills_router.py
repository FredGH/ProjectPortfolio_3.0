"""Integration tests for the skill-review API (no mocking the database)."""

from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "apps" / "api"))

from app.dependencies import get_app_db_engine  # noqa: E402
from app.routers import skills  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from tests.integration.skills_fixtures import (  # noqa: E402
    FIXTURE_ESCO_DIR,
    insert_job_skills,
    insert_mapping,
    live_app_engine,
    live_owner_engine,
    purge_fixtures,
)

from core.skills.esco_load import load_esco  # noqa: E402
from core.skills.mapper import remap_all_auto  # noqa: E402

# Mount only the skills router: the full app (app.main) also imports the cv
# router, which needs docling / python-multipart that this test does not.
app = FastAPI()
app.include_router(skills.router)

_UNMAPPED = "zzfixture unmapped one"
_MATCHED = "zzfixture matched"


class TestSkillReviewApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = live_owner_engine()
        cls.app_engine = live_app_engine()

    def setUp(self) -> None:
        purge_fixtures(self.owner)
        load_esco(self.owner, FIXTURE_ESCO_DIR)
        self.job = f"fixture-job-{uuid.uuid4().hex[:8]}"
        with self.owner.begin() as conn:
            insert_mapping(
                conn,
                _UNMAPPED,
                candidate_skill_id="fixture-cloud",
                candidate_score=0.6,
                seen_in_cv=True,
            )
            insert_mapping(
                conn,
                _MATCHED,
                skill_id="fixture-python",
                method="embedding",
                score=0.86,
                review_status=None,
            )
            insert_job_skills(
                conn, self.job, "local.v1", [(_UNMAPPED, _UNMAPPED, "must_have")]
            )
        app.dependency_overrides[get_app_db_engine] = lambda: self.app_engine
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.pop(get_app_db_engine, None)
        purge_fixtures(self.owner)

    def _mapping(self, raw_norm: str):
        with self.owner.connect() as conn:
            return conn.execute(
                text("SELECT * FROM silver.skill_mapping WHERE raw_norm = :n"),
                {"n": raw_norm},
            ).one()

    def _review_norms(self) -> set[str]:
        body = self.client.get(
            "/skills/review", params={"limit": 500, "q": "zzfixture"}
        ).json()
        return {item["raw_norm"] for item in body}

    def _insert_esco_skill(self, conn, skill_id: str, label: str) -> None:
        """Insert one ESCO skill and its preferred label."""
        conn.execute(
            text(
                "INSERT INTO esco.skill (skill_id, concept_uri, preferred_label) "
                "VALUES (:i, :u, :l)"
            ),
            {"i": skill_id, "u": f"http://example.invalid/esco/{skill_id}", "l": label},
        )
        conn.execute(
            text(
                "INSERT INTO esco.skill_label "
                "(skill_id, label, label_norm, is_preferred) "
                "VALUES (:i, :l, :n, true)"
            ),
            {"i": skill_id, "l": label, "n": label},
        )

    def test_review_list_shows_the_unmapped_string_with_context_and_suggestion(
        self,
    ) -> None:
        body = self.client.get(
            "/skills/review", params={"limit": 500, "q": "zzfixture"}
        ).json()
        item = next(i for i in body if i["raw_norm"] == _UNMAPPED)
        self.assertEqual(item["review_status"], "open")
        self.assertEqual(item["jd_job_count"], 1)
        self.assertEqual(item["sample_job_group_ids"], [self.job])
        self.assertTrue(item["seen_in_cv"])
        self.assertEqual(item["candidate_skill_id"], "fixture-cloud")
        self.assertEqual(item["candidate_label"], "zzfixture cloud technologies")
        self.assertAlmostEqual(item["candidate_score"], 0.6)
        self.assertNotIn(_MATCHED, {i["raw_norm"] for i in body})

    def test_auto_matches_list_shows_the_embedding_matched_string(self) -> None:
        body = self.client.get(
            "/skills/review/auto-matches", params={"limit": 500, "q": "zzfixture"}
        ).json()
        item = next(i for i in body if i["raw_norm"] == _MATCHED)
        self.assertEqual(item["skill_id"], "fixture-python")
        self.assertEqual(item["skill_label"], "zzfixture python")
        self.assertEqual(item["method"], "embedding")
        self.assertAlmostEqual(item["score"], 0.86)
        self.assertFalse(item["suspicious"])
        self.assertFalse(item["seen_in_cv"])

    def _insert_label_match(
        self, raw_norm: str, skill_id: str, *, seen_in_cv: bool = False
    ) -> None:
        """Insert an auto-made exact-label mapping (`method = 'label'`)."""
        with self.owner.begin() as conn:
            insert_mapping(
                conn,
                raw_norm,
                skill_id=skill_id,
                method="label",
                review_status=None,
                seen_in_cv=seen_in_cv,
            )

    def _auto_matches(self) -> list[dict]:
        body = self.client.get(
            "/skills/review/auto-matches", params={"limit": 500, "q": "zzfixture"}
        ).json()
        return [m for m in body if m["raw_norm"].startswith("zzfixture")]

    def test_auto_matches_lists_label_matches_flagging_the_suspicious_ones(
        self,
    ) -> None:
        with self.owner.begin() as conn:
            self._insert_esco_skill(
                conn, "fixture-java", "zzfixture java (computer programming)"
            )
            # A curated seed alias is not an auto-match to verify.
            insert_mapping(
                conn,
                "zzfixture seeded",
                skill_id="fixture-python",
                method="alias",
                review_status=None,
            )
        self._insert_label_match("zzfixture kotlin", "fixture-cloud", seen_in_cv=True)
        self._insert_label_match("zzfixture python", "fixture-python")
        self._insert_label_match("zzfixture java", "fixture-java")
        # Mapped through the head of a compound string (W1a).
        self._insert_label_match("zzfixture cloud technologies (s3", "fixture-cloud")

        matches = {m["raw_norm"]: m for m in self._auto_matches()}

        self.assertNotIn("zzfixture seeded", matches)
        kotlin = matches["zzfixture kotlin"]
        self.assertEqual(
            (kotlin["method"], kotlin["suspicious"], kotlin["seen_in_cv"]),
            ("label", True, True),
        )
        self.assertIsNone(kotlin["score"])
        self.assertEqual(kotlin["skill_label"], "zzfixture cloud technologies")
        for name in (
            "zzfixture python",
            "zzfixture java",
            "zzfixture cloud technologies (s3",
        ):
            self.assertFalse(matches[name]["suspicious"], name)

    def test_auto_matches_puts_suspicious_labels_first_then_embeddings_then_exact(
        self,
    ) -> None:
        with self.owner.begin() as conn:
            self._insert_esco_skill(
                conn, "fixture-java", "zzfixture java (computer programming)"
            )
        self._insert_label_match("zzfixture python", "fixture-python")
        self._insert_label_match("zzfixture java", "fixture-java")
        self._insert_label_match("zzfixture kotlin", "fixture-cloud")
        self.assertEqual(
            [m["raw_norm"] for m in self._auto_matches()],
            [
                "zzfixture kotlin",  # suspicious label match
                _MATCHED,  # embedding match
                "zzfixture java",  # exact-name label matches, most-used then A-Z
                "zzfixture python",
            ],
        )

    def test_auto_matches_orders_label_matches_by_how_many_jobs_use_them(self) -> None:
        self._insert_label_match("zzfixture aaa quiet", "fixture-cloud")
        self._insert_label_match("zzfixture zzz busy", "fixture-cloud")
        with self.owner.begin() as conn:
            for n in range(2):
                insert_job_skills(
                    conn,
                    f"fixture-job-busy{n}",
                    "local.v1",
                    [("zzfixture zzz busy", "zzfixture zzz busy", "must_have")],
                )
        listed = [
            m["raw_norm"]
            for m in self._auto_matches()
            if m["method"] == "label" and m["suspicious"]
        ]
        self.assertEqual(listed, ["zzfixture zzz busy", "zzfixture aaa quiet"])

    def test_search_finds_skills_by_any_label(self) -> None:
        body = self.client.get("/skills/search", params={"q": "zzfixture cloud"}).json()
        found = {o["skill_id"]: o["source"] for o in body}
        self.assertEqual(found.get("fixture-cloud"), "esco")

    def _search_ids(self, query: str) -> list[str]:
        body = self.client.get("/skills/search", params={"q": query}).json()
        return [o["skill_id"] for o in body]

    def test_search_treats_wildcards_literally(self) -> None:
        # Positive control: a plain substring of the fixture label does match,
        # so the empty results below are meaningful. Each wildcard query below
        # would match the fixture labels if its metacharacter were left
        # unescaped, and matches nothing when it is escaped: "%" is any run of
        # characters, "_" is any one character, and "\ " (backslash-space) is
        # a literal space to LIKE.
        self.assertIn("fixture-cloud", self._search_ids("zzfixture cloud"))
        for query in ("zzfixture%", "zzfixture_cloud", "zzfixture\\ cloud"):
            with self.subTest(query=query):
                self.assertEqual(self._search_ids(query), [])

    def test_search_puts_an_exact_label_match_first(self) -> None:
        # 12 decoys contain the query as a substring and sort alphabetically
        # before the exact match, so a purely alphabetical ordering pushes
        # the skill the reviewer wants out of the UI's 10-result window.
        with self.owner.begin() as conn:
            for index in range(1, 13):
                self._insert_esco_skill(
                    conn,
                    f"fixture-rank-{index:02d}",
                    f"a zzfixture ranktarget {index:02d}",
                )
            self._insert_esco_skill(conn, "fixture-rank-exact", "zzfixture ranktarget")
        body = self.client.get(
            "/skills/search", params={"q": "zzfixture ranktarget", "limit": 10}
        ).json()
        self.assertEqual(len(body), 10)
        self.assertEqual(body[0]["skill_id"], "fixture-rank-exact")

    def test_search_puts_a_prefix_match_before_a_mid_label_match(self) -> None:
        with self.owner.begin() as conn:
            self._insert_esco_skill(
                conn, "fixture-rank-mid", "a zzfixture ranktarget tail"
            )
            self._insert_esco_skill(
                conn, "fixture-rank-prefix", "zzfixture ranktarget tail"
            )
        body = self.client.get(
            "/skills/search", params={"q": "zzfixture ranktarget"}
        ).json()
        self.assertEqual(
            [o["skill_id"] for o in body],
            ["fixture-rank-prefix", "fixture-rank-mid"],
        )

    def test_resolving_to_an_esco_skill_maps_it_and_creates_a_review_alias(
        self,
    ) -> None:
        response = self.client.post(
            "/skills/review/resolve",
            json={"raw_norm": _UNMAPPED, "skill_id": "fixture-cloud"},
        )
        self.assertEqual(response.status_code, 200)
        row = self._mapping(_UNMAPPED)
        self.assertEqual(
            (row.skill_id, row.method, row.review_status),
            ("fixture-cloud", "alias", "resolved"),
        )
        with self.owner.connect() as conn:
            alias = conn.execute(
                text(
                    "SELECT skill_id, source FROM silver.skill_alias "
                    "WHERE alias_norm = :n"
                ),
                {"n": _UNMAPPED},
            ).one()
        self.assertEqual((alias.skill_id, alias.source), ("fixture-cloud", "review"))
        self.assertNotIn(_UNMAPPED, self._review_norms())

    def test_confirming_an_embedding_match_never_leaves_it_as_an_embedding_row(
        self,
    ) -> None:
        # remap_unresolved deletes method='embedding' rows, so a resolved row
        # that kept that method would silently undo the human decision.
        response = self.client.post(
            "/skills/review/resolve",
            json={"raw_norm": _MATCHED, "skill_id": "fixture-python"},
        )
        self.assertEqual(response.status_code, 200)
        row = self._mapping(_MATCHED)
        self.assertEqual(
            (row.skill_id, row.method, row.review_status),
            ("fixture-python", "alias", "resolved"),
        )
        self.assertIsNone(row.score)
        self.assertIsNone(row.candidate_skill_id)
        self.assertIsNone(row.candidate_score)
        matches = self.client.get(
            "/skills/review/auto-matches", params={"limit": 500, "q": "zzfixture"}
        ).json()
        self.assertNotIn(_MATCHED, {m["raw_norm"] for m in matches})

    def test_resolving_as_a_custom_skill_creates_it(self) -> None:
        response = self.client.post(
            "/skills/review/resolve",
            json={"raw_norm": _UNMAPPED, "custom_label": "ZZFixture My Tool"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._mapping(_UNMAPPED).skill_id, "custom:zzfixture-my-tool")
        with self.owner.connect() as conn:
            label = conn.execute(
                text(
                    "SELECT canonical_label FROM silver.custom_skill "
                    "WHERE skill_id = 'custom:zzfixture-my-tool'"
                )
            ).scalar_one()
        self.assertEqual(label, "ZZFixture My Tool")

    def test_resolving_to_an_unknown_skill_is_rejected(self) -> None:
        response = self.client.post(
            "/skills/review/resolve",
            json={"raw_norm": _UNMAPPED, "skill_id": "nope"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self._mapping(_UNMAPPED).review_status, "open")

    def test_resolve_needs_exactly_one_target(self) -> None:
        for payload in (
            {"raw_norm": _UNMAPPED},
            {"raw_norm": _UNMAPPED, "skill_id": "fixture-cloud", "custom_label": "X"},
        ):
            self.assertEqual(
                self.client.post("/skills/review/resolve", json=payload).status_code,
                422,
            )

    def test_an_unknown_string_is_a_404(self) -> None:
        response = self.client.post(
            "/skills/review/dismiss", json={"raw_norm": "zzfixture does not exist"}
        )
        self.assertEqual(response.status_code, 404)

    def test_dismissing_removes_it_from_the_list_and_only_applies_to_unmapped(
        self,
    ) -> None:
        ok = self.client.post("/skills/review/dismiss", json={"raw_norm": _UNMAPPED})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self._mapping(_UNMAPPED).review_status, "dismissed")
        self.assertNotIn(_UNMAPPED, self._review_norms())
        bad = self.client.post("/skills/review/dismiss", json={"raw_norm": _MATCHED})
        self.assertEqual(bad.status_code, 422)

    def test_rejecting_an_embedding_match_returns_it_to_the_unmapped_list(
        self,
    ) -> None:
        response = self.client.post(
            "/skills/review/reject", json={"raw_norm": _MATCHED}
        )
        self.assertEqual(response.status_code, 200)
        row = self._mapping(_MATCHED)
        self.assertEqual(
            (row.skill_id, row.method, row.review_status, row.candidate_skill_id),
            (None, "none", "rejected", "fixture-python"),
        )
        self.assertIsNone(row.score)
        self.assertAlmostEqual(float(row.candidate_score), 0.86)
        listed = self.client.get(
            "/skills/review", params={"limit": 500, "q": "zzfixture"}
        ).json()
        rejected = next(i for i in listed if i["raw_norm"] == _MATCHED)
        self.assertEqual(rejected["review_status"], "rejected")
        again = self.client.post("/skills/review/reject", json={"raw_norm": _MATCHED})
        self.assertEqual(again.status_code, 422)

    def _resolve(self, **payload) -> int:
        """POST a resolve and return its status code."""
        return self.client.post("/skills/review/resolve", json=payload).status_code

    def test_resolving_an_already_resolved_string_is_refused(self) -> None:
        # Re-pointing a settled mapping would silently move the alias every
        # past and future string shares, and source='review' then shields
        # the new target from the seed-alias sync.
        self.assertEqual(
            self._resolve(raw_norm=_UNMAPPED, skill_id="fixture-cloud"), 200
        )
        self.assertEqual(
            self._resolve(raw_norm=_UNMAPPED, skill_id="fixture-python"), 422
        )
        self.assertEqual(self._mapping(_UNMAPPED).skill_id, "fixture-cloud")
        with self.owner.connect() as conn:
            alias = conn.execute(
                text("SELECT skill_id FROM silver.skill_alias WHERE alias_norm = :n"),
                {"n": _UNMAPPED},
            ).scalar_one()
        self.assertEqual(alias, "fixture-cloud")

    def test_resolving_a_dismissed_string_is_refused(self) -> None:
        self.assertEqual(
            self.client.post(
                "/skills/review/dismiss", json={"raw_norm": _UNMAPPED}
            ).status_code,
            200,
        )
        self.assertEqual(
            self._resolve(raw_norm=_UNMAPPED, skill_id="fixture-cloud"), 422
        )
        self.assertEqual(self._mapping(_UNMAPPED).review_status, "dismissed")

    def test_confirming_a_label_match_saves_a_review_alias_and_leaves_the_list(
        self,
    ) -> None:
        # Label matches are on the verify tab now, so Confirm must work on
        # them and, like an embedding match, must not stay an auto row that
        # `map-skills --remap-all-auto` would delete again.
        labelled = "zzfixture label matched"
        self._insert_label_match(labelled, "fixture-python")
        self.assertEqual(
            self._resolve(raw_norm=labelled, skill_id="fixture-python"), 200
        )
        row = self._mapping(labelled)
        self.assertEqual(
            (row.skill_id, row.method, row.review_status),
            ("fixture-python", "alias", "resolved"),
        )
        with self.owner.connect() as conn:
            alias = conn.execute(
                text(
                    "SELECT skill_id, source FROM silver.skill_alias "
                    "WHERE alias_norm = :n"
                ),
                {"n": labelled},
            ).one()
        self.assertEqual((alias.skill_id, alias.source), ("fixture-python", "review"))
        self.assertNotIn(labelled, {m["raw_norm"] for m in self._auto_matches()})
        self.assertEqual(remap_all_auto(self.owner, raw_norms=[labelled]), 0)

    def test_resolving_a_curated_seed_alias_row_is_still_refused(self) -> None:
        # method='alias' with no review status is a curated seed mapping, not
        # something waiting for a decision: re-pointing it would only ever be
        # unintended.
        seeded = "zzfixture seed settled"
        with self.owner.begin() as conn:
            insert_mapping(
                conn,
                seeded,
                skill_id="fixture-python",
                method="alias",
                review_status=None,
            )
        self.assertEqual(self._resolve(raw_norm=seeded, skill_id="fixture-cloud"), 422)
        self.assertEqual(self._mapping(seeded).skill_id, "fixture-python")

    def test_rejecting_a_label_match_returns_it_to_the_unmapped_list(self) -> None:
        labelled = "zzfixture kotlin"
        self._insert_label_match(labelled, "fixture-cloud")
        response = self.client.post(
            "/skills/review/reject", json={"raw_norm": labelled}
        )
        self.assertEqual(response.status_code, 200)
        row = self._mapping(labelled)
        self.assertEqual(
            (row.skill_id, row.method, row.review_status, row.candidate_skill_id),
            (None, "none", "rejected", "fixture-cloud"),
        )
        self.assertIsNone(row.score)
        self.assertIsNone(row.candidate_score)  # a label match has no score
        listed = self.client.get(
            "/skills/review", params={"limit": 500, "q": "zzfixture"}
        ).json()
        rejected = next(i for i in listed if i["raw_norm"] == labelled)
        self.assertEqual(rejected["review_status"], "rejected")
        self.assertIsNone(rejected["candidate_score"])
        self.assertNotIn(labelled, {m["raw_norm"] for m in self._auto_matches()})
        self.assertEqual(
            self.client.post(
                "/skills/review/reject", json={"raw_norm": labelled}
            ).status_code,
            422,
        )

    def test_a_rejected_label_match_is_protected_from_remap_all_auto(self) -> None:
        labelled = "zzfixture kotlin"
        self._insert_label_match(labelled, "fixture-cloud")
        self.client.post("/skills/review/reject", json={"raw_norm": labelled})
        self.assertEqual(remap_all_auto(self.owner, raw_norms=[labelled]), 0)
        self.assertEqual(self._mapping(labelled).review_status, "rejected")

    def test_rejecting_a_curated_seed_alias_row_is_refused(self) -> None:
        seeded = "zzfixture seed settled"
        with self.owner.begin() as conn:
            insert_mapping(
                conn,
                seeded,
                skill_id="fixture-python",
                method="alias",
                review_status=None,
            )
        self.assertEqual(
            self.client.post(
                "/skills/review/reject", json={"raw_norm": seeded}
            ).status_code,
            422,
        )
        self.assertEqual(self._mapping(seeded).skill_id, "fixture-python")

    def test_a_nul_character_is_rejected_rather_than_crashing(self) -> None:
        self.assertEqual(self._resolve(raw_norm="zzfixture\x00x", skill_id="a"), 422)
        self.assertEqual(
            self._resolve(raw_norm=_UNMAPPED, custom_label="ZZFixture\x00Tool"), 422
        )
        self.assertEqual(
            self.client.get(
                "/skills/search", params={"q": "zzfixture\x00cloud"}
            ).status_code,
            422,
        )

    def test_an_over_long_input_is_rejected_rather_than_crashing(self) -> None:
        too_long = "zzfixture " + "x" * 300
        self.assertEqual(self._resolve(raw_norm=_UNMAPPED, custom_label=too_long), 422)
        self.assertEqual(
            self._resolve(raw_norm=too_long, skill_id="fixture-cloud"), 422
        )
        self.assertEqual(
            self.client.get("/skills/search", params={"q": too_long}).status_code, 422
        )
        self.assertEqual(self._mapping(_UNMAPPED).review_status, "open")

    def _decisions(self, **params) -> list[dict]:
        """GET the decisions list."""
        response = self.client.get(
            "/skills/review/decisions", params={"limit": 500, **params}
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _reopen(self, raw_norm: str) -> int:
        """POST a reopen and return its status code."""
        return self.client.post(
            "/skills/review/reopen", json={"raw_norm": raw_norm}
        ).status_code

    def _alias_target(self, alias_norm: str) -> str | None:
        """Return the skill an alias points at, or None if there is no alias."""
        with self.owner.connect() as conn:
            return conn.execute(
                text("SELECT skill_id FROM silver.skill_alias WHERE alias_norm = :n"),
                {"n": alias_norm},
            ).scalar_one_or_none()

    def _dismiss(self, raw_norm: str) -> None:
        """Dismiss an unmapped string through the API."""
        response = self.client.post(
            "/skills/review/dismiss", json={"raw_norm": raw_norm}
        )
        self.assertEqual(response.status_code, 200)

    def test_decisions_lists_resolved_and_dismissed_strings_with_their_target(
        self,
    ) -> None:
        dismissed = "zzfixture dismissed one"
        with self.owner.begin() as conn:
            insert_mapping(conn, dismissed)
        self.assertEqual(
            self._resolve(raw_norm=_UNMAPPED, skill_id="fixture-cloud"), 200
        )
        self._dismiss(dismissed)
        by_norm = {d["raw_norm"]: d for d in self._decisions()}
        resolved = by_norm[_UNMAPPED]
        self.assertEqual(resolved["review_status"], "resolved")
        self.assertEqual(resolved["skill_id"], "fixture-cloud")
        self.assertEqual(resolved["skill_label"], "zzfixture cloud technologies")
        self.assertEqual(resolved["jd_job_count"], 1)
        self.assertTrue(resolved["seen_in_cv"])
        self.assertEqual(by_norm[dismissed]["review_status"], "dismissed")
        self.assertIsNone(by_norm[dismissed]["skill_id"])
        # An auto-match still awaiting confirmation is not a decision.
        self.assertNotIn(_MATCHED, by_norm)

    def test_decisions_can_be_searched_by_string_or_target_label(self) -> None:
        self.assertEqual(
            self._resolve(raw_norm=_UNMAPPED, skill_id="fixture-cloud"), 200
        )
        by_string = {d["raw_norm"] for d in self._decisions(q="unmapped one")}
        by_target = {d["raw_norm"] for d in self._decisions(q="cloud technologies")}
        self.assertIn(_UNMAPPED, by_string)
        self.assertIn(_UNMAPPED, by_target)
        self.assertNotIn(_UNMAPPED, {d["raw_norm"] for d in self._decisions(q="nope")})

    def test_decisions_search_treats_wildcards_literally(self) -> None:
        self.assertEqual(
            self._resolve(raw_norm=_UNMAPPED, skill_id="fixture-cloud"), 200
        )
        self.assertEqual(self._decisions(q="%"), [])

    def _unmapped(self, **params) -> set[str]:
        body = self.client.get("/skills/review", params={"limit": 500, **params}).json()
        return {i["raw_norm"] for i in body}

    def _matches(self, **params) -> set[str]:
        body = self.client.get(
            "/skills/review/auto-matches", params={"limit": 500, **params}
        ).json()
        return {m["raw_norm"] for m in body}

    def test_the_unmapped_list_can_be_searched_by_string_or_candidate_label(
        self,
    ) -> None:
        self.assertIn(_UNMAPPED, self._unmapped(q="unmapped one"))
        self.assertIn(_UNMAPPED, self._unmapped(q="cloud technologies"))
        self.assertNotIn(_UNMAPPED, self._unmapped(q="nope"))

    def test_the_unmapped_list_search_treats_wildcards_literally(self) -> None:
        self.assertEqual(self._unmapped(q="%"), set())

    def test_auto_matches_can_be_searched_by_string_or_skill_label(self) -> None:
        self.assertIn(_MATCHED, self._matches(q="zzfixture matched"))
        self.assertIn(_MATCHED, self._matches(q="zzfixture python"))
        self.assertNotIn(_MATCHED, self._matches(q="nope"))

    def test_auto_matches_search_treats_wildcards_literally(self) -> None:
        self.assertEqual(self._matches(q="%"), set())

    def test_a_nul_character_in_a_list_search_is_rejected(self) -> None:
        for path in ("/skills/review", "/skills/review/auto-matches"):
            self.assertEqual(
                self.client.get(path, params={"q": "zzfixture\x00x"}).status_code, 422
            )

    def test_reopening_a_resolved_string_returns_it_as_rejected_and_drops_the_alias(
        self,
    ) -> None:
        self.assertEqual(
            self._resolve(raw_norm=_UNMAPPED, skill_id="fixture-cloud"), 200
        )
        self.assertEqual(self._reopen(_UNMAPPED), 200)
        row = self._mapping(_UNMAPPED)
        self.assertEqual(
            (row.skill_id, row.method, row.review_status, row.candidate_skill_id),
            (None, "none", "rejected", "fixture-cloud"),
        )
        self.assertIsNone(row.score)
        self.assertIsNone(self._alias_target(_UNMAPPED))
        listed = self.client.get(
            "/skills/review", params={"limit": 500, "q": "zzfixture"}
        ).json()
        item = next(i for i in listed if i["raw_norm"] == _UNMAPPED)
        self.assertEqual(item["review_status"], "rejected")
        self.assertEqual(item["candidate_label"], "zzfixture cloud technologies")
        self.assertNotIn(_UNMAPPED, {d["raw_norm"] for d in self._decisions()})

    def test_reopening_only_removes_that_strings_alias(self) -> None:
        other = "zzfixture other alias"
        with self.owner.begin() as conn:
            insert_mapping(conn, other)
        self.assertEqual(self._resolve(raw_norm=other, skill_id="fixture-python"), 200)
        self.assertEqual(
            self._resolve(raw_norm=_UNMAPPED, skill_id="fixture-cloud"), 200
        )
        self.assertEqual(self._reopen(_UNMAPPED), 200)
        self.assertEqual(self._alias_target(other), "fixture-python")
        self.assertEqual(self._mapping(other).review_status, "resolved")

    def test_reopening_a_custom_resolution_keeps_the_custom_skill(self) -> None:
        self.assertEqual(
            self._resolve(raw_norm=_UNMAPPED, custom_label="ZZFixture Tool"), 200
        )
        self.assertEqual(self._reopen(_UNMAPPED), 200)
        with self.owner.connect() as conn:
            kept = conn.execute(
                text(
                    "SELECT count(*) FROM silver.custom_skill "
                    "WHERE skill_id = 'custom:zzfixture-tool'"
                )
            ).scalar_one()
        self.assertEqual(kept, 1)
        self.assertEqual(
            self._mapping(_UNMAPPED).candidate_skill_id, "custom:zzfixture-tool"
        )

    def test_reopening_a_dismissed_string_returns_it_as_open(self) -> None:
        self._dismiss(_UNMAPPED)
        self.assertEqual(self._reopen(_UNMAPPED), 200)
        row = self._mapping(_UNMAPPED)
        self.assertEqual(
            (row.skill_id, row.method, row.review_status, row.candidate_skill_id),
            (None, "none", "open", "fixture-cloud"),
        )
        self.assertIn(_UNMAPPED, self._review_norms())

    def test_a_reopened_string_can_be_resolved_to_a_different_skill(self) -> None:
        self.assertEqual(
            self._resolve(raw_norm=_UNMAPPED, skill_id="fixture-cloud"), 200
        )
        self.assertEqual(self._reopen(_UNMAPPED), 200)
        self.assertEqual(
            self._resolve(raw_norm=_UNMAPPED, skill_id="fixture-python"), 200
        )
        self.assertEqual(self._mapping(_UNMAPPED).skill_id, "fixture-python")
        self.assertEqual(self._alias_target(_UNMAPPED), "fixture-python")

    def test_reopening_a_string_that_carries_no_decision_is_refused(self) -> None:
        # Unmapped, auto-matched, and a curated seed alias are all settled
        # by something other than a human decision.
        self.assertEqual(self._reopen(_UNMAPPED), 422)
        self.assertEqual(self._reopen(_MATCHED), 422)
        seeded = "zzfixture seed settled"
        with self.owner.begin() as conn:
            insert_mapping(
                conn,
                seeded,
                skill_id="fixture-python",
                method="alias",
                review_status=None,
            )
        self.assertEqual(self._reopen(seeded), 422)
        self.assertEqual(self._mapping(seeded).skill_id, "fixture-python")

    def test_reopening_twice_is_refused(self) -> None:
        self.assertEqual(
            self._resolve(raw_norm=_UNMAPPED, skill_id="fixture-cloud"), 200
        )
        self.assertEqual(self._reopen(_UNMAPPED), 200)
        self.assertEqual(self._reopen(_UNMAPPED), 422)

    def test_reopening_an_unknown_string_is_a_404(self) -> None:
        self.assertEqual(self._reopen("zzfixture no such string"), 404)

    def test_reopen_and_decisions_reject_bad_input_rather_than_crashing(self) -> None:
        self.assertEqual(self._reopen("zzfixture\x00x"), 422)
        self.assertEqual(self._reopen("zzfixture " + "x" * 300), 422)
        self.assertEqual(
            self.client.get(
                "/skills/review/decisions", params={"q": "zzfixture\x00x"}
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.get(
                "/skills/review/decisions", params={"q": "x" * 300}
            ).status_code,
            422,
        )


if __name__ == "__main__":
    unittest.main()
