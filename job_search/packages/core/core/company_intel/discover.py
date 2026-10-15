"""Choose which of a company's pages to crawl (PLAN.md Step 19a)."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit

# Second-level suffixes where the registered domain has three labels. A
# short list: a miss only means a subdomain is treated as its own site.
_TWO_PART_SUFFIXES = frozenset(
    {
        "co.uk",
        "org.uk",
        "ac.uk",
        "gov.uk",
        "ltd.uk",
        "plc.uk",
        "com.au",
        "co.nz",
        "co.in",
    }
)
# Path words that signal a page worth reading, best first.
_KEYWORDS = (
    "about",
    "company",
    "careers",
    "jobs",
    "engineering",
    "technology",
    "tech",
    "blog",
    "team",
    "culture",
    "product",
    "platform",
    "news",
)
_SKIP_SUFFIXES = (
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".zip",
    ".mp4",
    ".css",
    ".js",
)


def registered_domain(url: str) -> str:
    """Return a URL's registered domain (the site, without subdomains).

    Args:
        url: Any URL.

    Returns:
        e.g. `acme.com` or `acme.co.uk`; an empty string when the URL has
        no usable host.
    """
    host = (urlsplit(url).hostname or "").lower().strip(".")
    labels = host.split(".")
    if len(labels) < 2 or not all(labels):
        return ""
    if ".".join(labels[-2:]) in _TWO_PART_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _clean(url: str) -> str | None:
    """Drop the query and fragment and reject non-page URLs.

    Args:
        url: A candidate link.

    Returns:
        The cleaned http(s) URL, or None when it is not a crawlable page.
    """
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    if parts.path.lower().endswith(_SKIP_SUFFIXES):
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", "", ""))


def _rank(url: str) -> tuple[int, int, str]:
    """Score a page: lower is better.

    Args:
        url: A cleaned URL.

    Returns:
        `(keyword position, path depth, url)`; a page with no keyword
        sorts after every page with one.
    """
    path = urlsplit(url).path.lower()
    words = [w for w in path.replace("_", "-").replace("/", "-").split("-") if w]
    positions = [_KEYWORDS.index(w) for w in words if w in _KEYWORDS]
    return (
        min(positions) if positions else len(_KEYWORDS),
        path.count("/"),
        url,
    )


def pick_pages(home_url: str, links: Iterable[str], max_pages: int) -> list[str]:
    """Pick the pages to crawl for one company.

    Args:
        home_url: The company's homepage (always included, first).
        links: Links found on the homepage.
        max_pages: The most pages in total, the homepage included.

    Returns:
        The homepage, then up to `max_pages - 1` same-site pages whose path
        contains a useful word, best first and in a stable order.
    """
    site = registered_domain(home_url)
    chosen: dict[str, tuple[int, int, str]] = {}
    for link in links:
        cleaned = _clean(link)
        if cleaned is None or cleaned == _clean(home_url):
            continue
        if not site or registered_domain(cleaned) != site:
            continue
        rank = _rank(cleaned)
        if rank[0] == len(_KEYWORDS):
            continue
        chosen[cleaned] = rank
    ordered = sorted(chosen, key=lambda u: chosen[u])
    return [home_url, *ordered[: max(0, max_pages - 1)]]
