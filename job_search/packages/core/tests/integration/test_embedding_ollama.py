from __future__ import annotations

import unittest

import httpx

from core.embedding.ollama import embed_text

_OLLAMA_BASE_URL = "http://localhost:11434"
_MODEL = "nomic-embed-text"


def _ollama_available() -> bool:
    try:
        response = httpx.get(f"{_OLLAMA_BASE_URL}/api/tags", timeout=2.0)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


@unittest.skipUnless(_ollama_available(), "Ollama server not reachable")
class TestEmbedText(unittest.TestCase):
    def setUp(self) -> None:
        self.client = httpx.Client(timeout=30.0)

    def tearDown(self) -> None:
        self.client.close()

    def test_returns_a_nonempty_float_vector(self) -> None:
        vector = embed_text(
            "Senior Data Engineer",
            base_url=_OLLAMA_BASE_URL,
            model=_MODEL,
            client=self.client,
        )
        self.assertGreater(len(vector), 0)
        self.assertTrue(all(isinstance(x, float) for x in vector))

    def test_same_text_produces_the_same_vector(self) -> None:
        # Ollama embeddings are deterministic for a fixed model/input —
        # this is what makes centroid-building reproducible.
        first = embed_text(
            "Data Engineer", base_url=_OLLAMA_BASE_URL, model=_MODEL, client=self.client
        )
        second = embed_text(
            "Data Engineer", base_url=_OLLAMA_BASE_URL, model=_MODEL, client=self.client
        )
        self.assertEqual(first, second)

    def test_similar_titles_are_closer_than_dissimilar_ones(self) -> None:
        # Structural sanity check on the embedding model itself, not on
        # our code — two data-engineering titles should cosine-sim
        # closer to each other than to an unrelated title.
        import math

        def cosine(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b))
            norm_a = math.sqrt(sum(x * x for x in a))
            norm_b = math.sqrt(sum(y * y for y in b))
            return dot / (norm_a * norm_b)

        de1 = embed_text(
            "Data Engineer", base_url=_OLLAMA_BASE_URL, model=_MODEL, client=self.client
        )
        de2 = embed_text(
            "ETL Developer", base_url=_OLLAMA_BASE_URL, model=_MODEL, client=self.client
        )
        unrelated = embed_text(
            "Product Manager",
            base_url=_OLLAMA_BASE_URL,
            model=_MODEL,
            client=self.client,
        )
        self.assertGreater(cosine(de1, de2), cosine(de1, unrelated))
