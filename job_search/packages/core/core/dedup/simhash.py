"""64-bit SimHash fingerprinting for near-duplicate description
detection (PLAN.md Step 8).

Word-bigram SimHash (Charikar's algorithm, shingled on consecutive word
pairs rather than single words): each shingle votes on every bit of its
own hash, weighted +1/-1, and the final fingerprint bit is 1 wherever
the votes sum positive. Similar texts (sharing most word pairs) produce
fingerprints with a small Hamming distance; unrelated texts produce
fingerprints close to random (~32 bits different, on average, for two
64-bit fingerprints).

Word bigrams (rather than single-word unigrams) were chosen after
comparing both on this project's real near-duplicate calibration pair
(a job description truncated differently by two source sites): unigram
shingling gave a Hamming distance of 5 between the pair, bigram
shingling gave 4 — closer, and with a much wider margin from an
unrelated posting's distance (24 for unigrams vs. 31 for bigrams). See
`tests/test_simhash.py`'s
`test_near_duplicate_real_descriptions_are_closer_than_unrelated` for
the achieved numbers and why the PLAN.md target of <=3 is not met by
either approach on this specific pair.
"""

from __future__ import annotations

import hashlib
import re

_TOKEN_RE = re.compile(r"\w+")


def _shingles(text: str) -> list[str]:
    """Tokenise `text` into word bigrams ("word1 word2" shingles).

    Falls back to unigrams when there are fewer than two tokens, so a
    single-word input still produces a non-empty shingle list.
    """
    tokens = _TOKEN_RE.findall(text.lower())
    if len(tokens) < 2:
        return tokens
    return [f"{tokens[i]} {tokens[i + 1]}" for i in range(len(tokens) - 1)]


def compute_simhash(text: str, hash_bits: int = 64) -> int:
    """Compute a SimHash fingerprint over a text's word-bigram shingles.

    Args:
        text: The text to fingerprint (e.g. a posting's description).
        hash_bits: The fingerprint width. Defaults to 64, matching
            PLAN.md Step 8's "SimHash 64-bit."

    Returns:
        An unsigned integer in [0, 2**hash_bits) — 0 for empty or
        all-non-word-character input.
    """
    tokens = _TOKEN_RE.findall(text.lower())
    if not tokens:
        return 0

    shingles = _shingles(text)

    votes = [0] * hash_bits
    for shingle in shingles:
        shingle_hash = int(hashlib.md5(shingle.encode("utf-8")).hexdigest(), 16)
        for bit_position in range(hash_bits):
            bit = (shingle_hash >> bit_position) & 1
            votes[bit_position] += 1 if bit else -1

    fingerprint = 0
    for bit_position in range(hash_bits):
        if votes[bit_position] > 0:
            fingerprint |= 1 << bit_position
    return fingerprint


def hamming_distance(a: int, b: int) -> int:
    """Count the differing bits between two fingerprints.

    Args:
        a: The first fingerprint.
        b: The second fingerprint.

    Returns:
        The number of bit positions where `a` and `b` differ.
    """
    return bin(a ^ b).count("1")
