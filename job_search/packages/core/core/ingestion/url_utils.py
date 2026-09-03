"""URL canonicalisation and source_job_id extraction (PLAN.md Step 2).

Two sources pointing at the same canonical URL is an instant dedup match,
so getting this normalisation right costs nothing and buys a lot.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

_STRIP_EXACT = {"ref", "src", "trk", "aff"}
_STRIP_PREFIXES = ("utm_",)
_LINKEDIN_JOB_ID = re.compile(r"/jobs/view/(\d+)")


def _should_strip_param(name: str) -> bool:
    """Decide whether a query parameter is tracking/session noise.

    Args:
        name: The query parameter's name, as parsed from the URL.

    Returns:
        True if the parameter should be dropped during canonicalisation.
    """
    lowered = name.lower()
    if lowered in _STRIP_EXACT:
        return True
    if lowered.startswith(_STRIP_PREFIXES):
        return True
    return "session" in lowered or "sess" in lowered


def _resolve_one_redirect(url: str, http_client: httpx.Client) -> str:
    """Follow a single redirect hop, best-effort.

    Args:
        url: The URL to check for a redirect.
        http_client: The client to issue the (non-following) request with.

    Returns:
        The `Location` target if the server returned a 3xx with one;
        otherwise the original `url` unchanged. Any request failure is
        swallowed and the original `url` is returned — redirect resolution
        is a nice-to-have, never a hard requirement for ingestion.
    """
    try:
        response = http_client.request("HEAD", url, follow_redirects=False)
    except httpx.HTTPError:
        return url
    if response.is_redirect:
        location = response.headers.get("location")
        if location:
            return str(httpx.URL(url).join(location))
    return url


def _normalise(url: str) -> str:
    """Lowercase host/scheme, strip tracking params, strip a trailing slash.

    Args:
        url: The URL to normalise.

    Returns:
        The normalised URL.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    query_pairs = [
        (name, value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _should_strip_param(name)
    ]
    query = urlencode(sorted(query_pairs))
    return urlunsplit((scheme, netloc, path, query, ""))


def canonicalise_url(url: str, *, http_client: httpx.Client | None = None) -> str:
    """Canonicalise a job posting URL.

    Args:
        url: The raw URL as pasted or received.
        http_client: When given, used to resolve a single redirect hop
            before normalising. When omitted, no network call is made —
            the URL is normalised as-is.

    Returns:
        The canonicalised URL: lowercase scheme/host, tracking/session
        query params stripped, no trailing slash, at most one redirect hop
        resolved.
    """
    resolved = url if http_client is None else _resolve_one_redirect(url, http_client)
    return _normalise(resolved)


def extract_source_job_id(canonical_url: str) -> str:
    """Extract a stable job id from a canonical URL.

    Args:
        canonical_url: The output of `canonicalise_url`.

    Returns:
        The LinkedIn numeric job id when the URL matches LinkedIn's
        `/jobs/view/{id}` pattern; otherwise `sha256(canonical_url)`.
    """
    if "linkedin.com" in canonical_url:
        match = _LINKEDIN_JOB_ID.search(canonical_url)
        if match:
            return match.group(1)
    return hashlib.sha256(canonical_url.encode()).hexdigest()
