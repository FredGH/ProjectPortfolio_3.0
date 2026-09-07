"""Location normalisation to ISO country + a coarse UK region (PLAN.md
Step 6). See the Step 6 plan's scope note: this resolves UK
county/region names and a handful of observed non-UK signals, not
general geocoding. Anything unresolved is explicit None, never a guess —
the raw location string is preserved unchanged upstream regardless.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_REMOTE_RE = re.compile(r"\bremote\b", re.IGNORECASE)

_UK_POSTCODE_RE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}$", re.IGNORECASE)

# UK county/region name -> ITL1 (post-Brexit NUTS1-equivalent) code.
# Populated from names actually observed in real bronze data; extend as
# new counties appear rather than trying to enumerate every UK county
# up front.
_UK_REGION_TO_ITL1 = {
    "london": "UKI",
    "west london": "UKI",
    "hertfordshire": "UKH",
    "south yorkshire": "UKE",
    "north yorkshire": "UKE",
    "kent": "UKJ",
    "south east england": "UKJ",
    "south west england": "UKK",
    "staffordshire": "UKG",
    "birmingham": "UKG",
    "warwickshire": "UKG",
    "west sussex": "UKJ",
    "gloucestershire": "UKK",
    "greenock": "UKM",
    "county down": "UKN",
}

# A handful of explicit non-UK country signals actually observed in real
# Greenhouse location strings — not a general gazetteer.
_NON_UK_COUNTRY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\bus[\s-]*remote\b|\bunited states\b|\bremote in the us\b", re.IGNORECASE
        ),
        "US",
    ),
    # Deliberately NOT matching a bare ", DE": that is also the US postal
    # code for Delaware, so "Wilmington, DE"/"Newark, DE" would resolve to
    # Germany. Per this module's own philosophy (unresolved beats wrong),
    # the two-letter code is dropped and only unambiguous signals are
    # kept: the country name, and "Berlin" as a city that no US state
    # abbreviation collides with.
    (re.compile(r"\bgermany\b", re.IGNORECASE), "DE"),
    (re.compile(r"\bberlin\b", re.IGNORECASE), "DE"),
    (re.compile(r"\bMX\b|\bmexico\b", re.IGNORECASE), "MX"),
    (re.compile(r"\bsouth korea\b", re.IGNORECASE), "KR"),
]


@dataclass(frozen=True)
class NormalisedLocation:
    """A location resolved to ISO country and (for the UK) a coarse region.

    Attributes:
        country_iso: 2-letter ISO country code, or `None` if not
            resolved (see this plan's scope note — not every real
            location string is resolvable without full geocoding).
        region: ITL1 region code, only ever populated for `country_iso ==
            "GB"`, and only when the county/region name is in this
            module's lookup. `None` otherwise, including for every
            non-UK country.
        is_remote: Whether "remote" appears anywhere in the raw string,
            independent of whether a country was also resolved.
    """

    country_iso: str | None
    region: str | None
    is_remote: bool


def normalise_location(raw: str | None) -> NormalisedLocation:
    """Normalise a location string to ISO country + (for the UK) region.

    Args:
        raw: The location string as stored in int_jobs__unioned. `None`
            when the source has no location for this row —
            int_jobs__unioned.location is nullable (manual entries with
            failed extraction).

    Returns:
        The `NormalisedLocation`. `country_iso`/`region` are `None` when
        genuinely unresolved (see this module's docstring) — never
        guessed. `None` input yields the fully-unresolved
        `NormalisedLocation(None, None, is_remote=False)` rather than
        raising: no location string is no signal, not an error.
    """
    if raw is None:
        return NormalisedLocation(country_iso=None, region=None, is_remote=False)

    is_remote = bool(_REMOTE_RE.search(raw))

    if _UK_POSTCODE_RE.match(raw.strip()):
        return NormalisedLocation(country_iso="GB", region=None, is_remote=is_remote)

    last_segment = raw.split(",")[-1].strip().lower()
    region_code = _UK_REGION_TO_ITL1.get(last_segment)
    if region_code:
        return NormalisedLocation(
            country_iso="GB", region=region_code, is_remote=is_remote
        )

    for pattern, country_iso in _NON_UK_COUNTRY_PATTERNS:
        if pattern.search(raw):
            return NormalisedLocation(
                country_iso=country_iso, region=None, is_remote=is_remote
            )

    return NormalisedLocation(country_iso=None, region=None, is_remote=is_remote)
