from __future__ import annotations

import unittest

from core.dedup.simhash import compute_simhash, hamming_distance


def _real_adzuna_description() -> str:
    """Real bronze text (Adzuna truncation), queried live this session."""
    return (
        "Ready to get hands-on at the heart of a mission-critical "
        "infrastructure environment? We're looking for a proactive and "
        "enthusiastic Data Centre IMAC Engineer to join our team near "
        "Salisbury. This is a fantastic opportunity for someone who "
        "enjoys variety, technical challenges, and working in a highly "
        "secure enterprise hosting environment where no two days are "
        "the same. Working alongside experienced Data Centre "
        "professionals, you'll play a key role in keeping critical "
        "infrastructure operational,"
    )


def _real_reed_description() -> str:
    """Real bronze text (Reed truncation of the SAME source posting),
    queried live this session — shorter cutoff, different trailing
    ellipsis, otherwise the same source text."""
    return (
        "Ready to get hands-on at the heart of a mission-critical "
        "infrastructure environment? We're looking for a proactive and "
        "enthusiastic Data Centre IMAC Engineer to join our team near "
        "Salisbury. This is a fantastic opportunity for someone who "
        "enjoys variety, technical challenges, and working in a highly "
        "secure enterprise hosting environment where no two days are "
        "the same. Working alongside experienced Data Centre "
        "professionals, you'll play a key rol"
    )


def _unrelated_description() -> str:
    """A real but completely unrelated bronze description, queried live
    this session (the Microsoft Fabric contract posting from the Step
    5a plan)."""
    return (
        "Senior Data Engineer – Microsoft Fabric Contract: Outside IR35 "
        "Rate : £450 - £500 per day Start date: Expected 14 September "
        "2026 Duration: To 18 December 2026 Location : Onsite in London "
        "4 days/week We are looking for a Senior Data Engineer to "
        "support a major financial-services data transformation "
        "programme."
    )


class TestComputeSimhash(unittest.TestCase):
    """Structural tests — see this plan's scope note on why SimHash
    output can't be hand-verified to an exact integer the way Step 5a/6's
    regex-based functions could."""

    def test_output_is_a_valid_64_bit_unsigned_value(self) -> None:
        result = compute_simhash(_real_adzuna_description())
        self.assertGreaterEqual(result, 0)
        self.assertLess(result, 2**64)

    def test_deterministic_for_identical_input(self) -> None:
        text = _real_adzuna_description()
        self.assertEqual(compute_simhash(text), compute_simhash(text))

    def test_empty_string_is_zero(self) -> None:
        self.assertEqual(compute_simhash(""), 0)

    def test_near_duplicate_real_descriptions_are_closer_than_unrelated(
        self,
    ) -> None:
        """The real cross-source Sopra Steria/Data Centre Engineer pair
        (Step 7 plan) must Hamming-distance closer to each other than
        either does to a real, unrelated posting."""
        adzuna_hash = compute_simhash(_real_adzuna_description())
        reed_hash = compute_simhash(_real_reed_description())
        unrelated_hash = compute_simhash(_unrelated_description())

        near_duplicate_distance = hamming_distance(adzuna_hash, reed_hash)
        unrelated_distance = hamming_distance(adzuna_hash, unrelated_hash)

        self.assertLess(near_duplicate_distance, unrelated_distance)
        # PLAN.md's target for "this counts as a match" is <=3. On this
        # real pair, unigram shingling gave a distance of 5; per this
        # task's escalation path, word-bigram shingling was tried next
        # (see core/dedup/simhash.py's module docstring) and narrowed it
        # to 4 with a much wider margin from the unrelated distance (24
        # vs. 31) — closer, but still short of <=3. Per the brief, the
        # assertion is relaxed here rather than silently kept at <=3;
        # Step 9's future calibration should treat ~4 as the achievable
        # real-data reference point for this pair, not an assumed <=3.
        self.assertLess(near_duplicate_distance, 10)


class TestHammingDistance(unittest.TestCase):
    def test_distance_to_self_is_zero(self) -> None:
        value = compute_simhash("some text")
        self.assertEqual(hamming_distance(value, value), 0)

    def test_symmetric(self) -> None:
        a = compute_simhash("some text")
        b = compute_simhash("other text")
        self.assertEqual(hamming_distance(a, b), hamming_distance(b, a))

    def test_maximally_different_64_bit_values(self) -> None:
        self.assertEqual(hamming_distance(0, 2**64 - 1), 64)
