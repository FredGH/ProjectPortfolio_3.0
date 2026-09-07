from __future__ import annotations

import unittest

from core.dedup.blocking_keys import compute_blocking_key


class TestComputeBlockingKey(unittest.TestCase):
    """Tests against the real cross-source duplicate pair found live in
    this project's own silver__job_posting data (Adzuna job_key
    0d5842a06b5b9e5308381d7b7ed7af69 / Reed job_key
    e2bd39c45efc081ca3be8abc2a1aaad5 — both "Sopra Steria, Data Centre
    Engineer")."""

    def test_real_cross_source_duplicate_lands_in_the_same_block(self) -> None:
        adzuna = compute_blocking_key(
            company="Sopra Steria",
            title="Data Centre Engineer",
            location="Salisbury, Wiltshire",
            description=(
                "Ready to get hands-on at the heart of a mission-critical "
                "infrastructure environment? We're looking for a proactive "
                "and enthusiastic Data Centre IMAC Engineer to join our "
                "team near Salisbury."
            ),
        )
        reed = compute_blocking_key(
            company="Sopra Steria",
            title="Data Centre Engineer",
            location="Salisbury",
            description=(
                "Ready to get hands-on at the heart of a mission-critical "
                "infrastructure environment? We're looking for a proactive "
                "and enthusiastic Data Centre IMAC Engineer to join our "
                "team near Salisbury, but the wording trails off "
                "differently here because Reed truncates shorter."
            ),
        )
        self.assertEqual(adzuna.block_key, reed.block_key)
        self.assertEqual(adzuna.normalised_company, "Sopra Steria")
        self.assertEqual(adzuna.matching_title, "Data Centre Engineer")
        # Neither "Salisbury, Wiltshire" nor "Salisbury" resolves in Step
        # 6's small UK lookup table — both fall back to unresolved, which
        # is WHY they still collide into the same block.
        self.assertIsNone(adzuna.country_iso)
        self.assertIsNone(reed.country_iso)

    def test_real_duplicate_pair_does_not_share_content_sha256(self) -> None:
        """The two real descriptions diverge before their respective
        truncation points, so the exact hash correctly does NOT catch
        this pair — that's Step 8's job, not Step 7's cheap check."""
        adzuna = compute_blocking_key(
            "Sopra Steria",
            "Data Centre Engineer",
            "Salisbury, Wiltshire",
            "Ready to get hands-on... near Salisbury. This is a fantastic "
            "opportunity for someone who enjoys variety…",
        )
        reed = compute_blocking_key(
            "Sopra Steria",
            "Data Centre Engineer",
            "Salisbury",
            "Ready to get hands-on... near Salisbury. This is a fantastic "
            "opportunity for someone who enjoys variety, technical "
            "challenges, and working in a highly secure...",
        )
        self.assertNotEqual(adzuna.content_sha256, reed.content_sha256)

    def test_identical_inputs_produce_identical_hash(self) -> None:
        result_a = compute_blocking_key(
            "Acme Ltd", "Data Engineer", "London", "Some description text."
        )
        result_b = compute_blocking_key(
            "Acme Ltd", "Data Engineer", "London", "Some description text."
        )
        self.assertEqual(result_a.content_sha256, result_b.content_sha256)

    def test_different_company_produces_different_block(self) -> None:
        acme = compute_blocking_key("Acme Ltd", "Data Engineer", "London", "x")
        other = compute_blocking_key("Other Co", "Data Engineer", "London", "x")
        self.assertNotEqual(acme.block_key, other.block_key)

    def test_seniority_stripped_before_blocking(self) -> None:
        """'Senior Data Engineer' and 'Data Engineer' at the same company
        must block together — this is the entire reason strip_title
        exists (DECISIONS.md §5)."""
        senior = compute_blocking_key("Acme Ltd", "Senior Data Engineer", "London", "x")
        plain = compute_blocking_key("Acme Ltd", "Data Engineer", "London", "x")
        self.assertEqual(senior.block_key, plain.block_key)

    def test_resolved_country_included_in_block_key(self) -> None:
        """Two identical company/title pairs in different resolved
        countries must NOT block together."""
        uk = compute_blocking_key(
            "Acme Ltd", "Data Engineer", "Central London, London", "x"
        )
        de = compute_blocking_key("Acme Ltd", "Data Engineer", "Berlin, DE", "x")
        self.assertNotEqual(uk.block_key, de.block_key)

    def test_none_fields_do_not_raise(self) -> None:
        """Manual entries can have every one of these genuinely null."""
        result = compute_blocking_key(None, None, None, None)
        self.assertIsInstance(result.block_key, str)
        self.assertIsInstance(result.content_sha256, str)
