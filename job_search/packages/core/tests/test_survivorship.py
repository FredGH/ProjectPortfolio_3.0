from __future__ import annotations

import unittest

from core.dedup.survivorship import (
    ClusterMember,
    build_sources_array,
    resolve_apply_source,
    resolve_description,
)


def _member(
    source_name: str,
    job_url: str = "https://example.com",
    title_for_display: str = "Data Engineer",
    description: str | None = "desc",
    first_seen_at: str = "2026-09-01",
) -> ClusterMember:
    return ClusterMember(
        source_name=source_name,
        job_url=job_url,
        title_for_display=title_for_display,
        description=description,
        first_seen_at=first_seen_at,
    )


class TestResolveDescription(unittest.TestCase):
    def test_longest_non_empty_description_wins(self) -> None:
        members = [
            _member("reed", description="short"),
            _member("manual", description="a much longer description"),
        ]
        self.assertEqual(resolve_description(members), "a much longer description")

    def test_empty_and_none_descriptions_are_ignored(self) -> None:
        members = [
            _member("reed", description=""),
            _member("adzuna", description=None),
            _member("manual", description="the only real one"),
        ]
        self.assertEqual(resolve_description(members), "the only real one")

    def test_all_empty_returns_none(self) -> None:
        members = [_member("reed", description=None), _member("adzuna", description="")]
        self.assertIsNone(resolve_description(members))


class TestResolveApplySource(unittest.TestCase):
    def test_ats_source_wins_even_with_shorter_description(self) -> None:
        members = [
            _member("reed", job_url="https://reed.example", description="x" * 500),
            _member(
                "greenhouse",
                job_url="https://greenhouse.example",
                description="x",
            ),
        ]
        winner = resolve_apply_source(members)
        self.assertEqual(winner.source_name, "greenhouse")
        self.assertEqual(winner.job_url, "https://greenhouse.example")

    def test_title_for_display_comes_from_the_same_winning_source(self) -> None:
        members = [
            _member("reed", title_for_display="Data Engineer (Reed phrasing)"),
            _member("greenhouse", title_for_display="Senior Data Engineer"),
        ]
        winner = resolve_apply_source(members)
        self.assertEqual(winner.title_for_display, "Senior Data Engineer")

    def test_unknown_source_name_never_wins_over_a_known_one(self) -> None:
        members = [
            _member("some-new-scraper"),
            _member("jooble"),
        ]
        self.assertEqual(resolve_apply_source(members).source_name, "jooble")


class TestBuildSourcesArray(unittest.TestCase):
    def test_keeps_every_member_regardless_of_survivorship_winner(self) -> None:
        members = [
            _member("greenhouse", job_url="https://gh.example"),
            _member("reed", job_url="https://reed.example"),
        ]
        sources = build_sources_array(members)
        self.assertEqual(len(sources), 2)
        self.assertEqual({s["source_name"] for s in sources}, {"greenhouse", "reed"})

    def test_ordered_deterministically_by_source_name(self) -> None:
        members = [_member("reed"), _member("adzuna"), _member("greenhouse")]
        sources = build_sources_array(members)
        self.assertEqual(
            [s["source_name"] for s in sources],
            ["adzuna", "greenhouse", "reed"],
        )
