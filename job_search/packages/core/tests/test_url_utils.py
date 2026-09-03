from __future__ import annotations

import hashlib
import unittest

import httpx

from core.ingestion.url_utils import canonicalise_url, extract_source_job_id


class TestCanonicaliseUrl(unittest.TestCase):
    """Tests for canonicalise_url's normalisation and redirect resolution."""

    def test_lowercases_host_and_strips_tracking_params(self) -> None:
        """utm_*, ref, src, trk, aff params and trailing slash are stripped."""
        result = canonicalise_url(
            "HTTPS://WWW.LinkedIn.com/jobs/view/12345/"
            "?utm_source=li&ref=abc&trk=xyz&aff=1"
        )
        self.assertEqual(result, "https://www.linkedin.com/jobs/view/12345")

    def test_strips_session_like_params_but_keeps_real_ones(self) -> None:
        """Session-ish params are stripped; a real query param survives."""
        result = canonicalise_url(
            "https://example.com/job?id=42&PHPSESSID=abc123&utm_campaign=x"
        )
        self.assertEqual(result, "https://example.com/job?id=42")

    def test_no_client_skips_redirect_resolution(self) -> None:
        """Without an http_client, the URL is normalised as-is, no network."""
        result = canonicalise_url("https://example.com/job/")
        self.assertEqual(result, "https://example.com/job")

    def test_resolves_one_redirect_hop_when_client_given(self) -> None:
        """A single 301/302 hop is followed before normalisation."""

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == "https://old.example.com/job":
                return httpx.Response(
                    301, headers={"Location": "https://new.example.com/job/?ref=x"}
                )
            raise AssertionError(f"unexpected request to {request.url}")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        result = canonicalise_url("https://old.example.com/job", http_client=client)
        self.assertEqual(result, "https://new.example.com/job")

    def test_redirect_failure_falls_back_to_original_url(self) -> None:
        """A network error during redirect resolution is swallowed (best-effort)."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        result = canonicalise_url("https://example.com/job/", http_client=client)
        self.assertEqual(result, "https://example.com/job")


class TestExtractSourceJobId(unittest.TestCase):
    """Tests for extract_source_job_id's LinkedIn pattern and hash fallback."""

    def test_extracts_linkedin_numeric_id(self) -> None:
        """LinkedIn's /jobs/view/{id} pattern yields the numeric id directly."""
        result = extract_source_job_id("https://www.linkedin.com/jobs/view/3812345")
        self.assertEqual(result, "3812345")

    def test_falls_back_to_sha256_for_unknown_patterns(self) -> None:
        """A non-LinkedIn URL falls back to sha256(canonical_url)."""
        url = "https://example.com/job/42"
        result = extract_source_job_id(url)
        self.assertEqual(result, hashlib.sha256(url.encode()).hexdigest())

    def test_rejects_lookalike_linkedin_domains(self) -> None:
        """Lookalike domains that contain 'linkedin.com' fall back to hash."""
        # fakelinkedin.com contains "linkedin.com" as substring but isn't LinkedIn
        url = "https://fakelinkedin.com/jobs/view/12345"
        result = extract_source_job_id(url)
        self.assertEqual(result, hashlib.sha256(url.encode()).hexdigest())

        # notlinkedin.com.evil.net contains "linkedin.com" substring
        url = "https://notlinkedin.com.evil.net/jobs/view/99999"
        result = extract_source_job_id(url)
        self.assertEqual(result, hashlib.sha256(url.encode()).hexdigest())


if __name__ == "__main__":
    unittest.main()
