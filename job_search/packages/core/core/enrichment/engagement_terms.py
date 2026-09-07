"""Phrase-rule extraction of engagement/IR35/rate terms from a posting's
free text (PLAN.md Step 5a).

Phrase rules only — the LLM-residual pass PLAN.md describes for postings
these rules can't resolve is a deliberately deferred follow-up (see this
plan's scope note), not built here. Every field always gets an explicit
value; `unknown`/`undetermined`/`unstated` are values, never a stand-in
for "not yet implemented."
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PERMANENT_RE = re.compile(r"\bpermanent\b", re.IGNORECASE)
_FTC_RE = re.compile(r"\b(fixed[\s-]?term contract|\bftc\b)", re.IGNORECASE)
_INTERIM_RE = re.compile(r"\binterim\b", re.IGNORECASE)
_CONTRACT_RE = re.compile(
    r"\b(contract|contractor|day rate|ir35|umbrella|paye)\b", re.IGNORECASE
)

_OUTSIDE_IR35_RE = re.compile(r"\boutside\s+(?:of\s+)?ir35\b", re.IGNORECASE)
_INSIDE_IR35_RE = re.compile(r"\binside\s+(?:of\s+)?ir35\b", re.IGNORECASE)
_BARE_IR35_RE = re.compile(r"\bir35\b", re.IGNORECASE)

_AGENCY_PAYE_RE = re.compile(r"\bagency\s+paye\b", re.IGNORECASE)
_PAYE_RE = re.compile(r"\bpaye\b", re.IGNORECASE)
_UMBRELLA_RE = re.compile(r"\bumbrella\b", re.IGNORECASE)
_LIMITED_RE = re.compile(
    r"\b(own limited company|limited company|ltd company)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class EngagementClassification:
    """The engagement-type/IR35/vehicle half of a posting's engagement terms.

    Attributes:
        engagement_type: One of permanent, contract, ftc, interim, unknown.
        ir35_status: One of inside, outside, not_applicable, undetermined,
            unknown. `not_applicable` when engagement_type is permanent
            (IR35 only applies to contract engagements). `unknown` when no
            engagement-type signal was found at all. `undetermined` when
            the posting is clearly a contract engagement but IR35 status
            specifically wasn't stated or wasn't resolvable.
        engagement_vehicle: One of umbrella, limited, paye, agency_paye,
            unknown.
    """

    engagement_type: str
    ir35_status: str
    engagement_vehicle: str


def classify_engagement(description: str | None) -> EngagementClassification:
    """Classify a posting's engagement type, IR35 status and vehicle.

    Args:
        description: The posting's free text (job spec/description). `None`
            when a source has no description for this row (e.g. some
            manual entries with failed extraction).

    Returns:
        The `EngagementClassification`, with every field explicitly set —
        never `None` for any field.
    """
    text = description or ""

    # An explicit "inside/outside IR35" statement is strong evidence of a
    # contract-family engagement, so it disqualifies the `permanent`
    # branch: a bare "permanent" match is easily a false positive from
    # unrelated text in a real posting ("we offer permanent health
    # insurance"). The remaining branches keep their original precedence,
    # so an explicit FTC/interim posting that also states IR35 still
    # classifies as ftc/interim rather than being flattened to contract.
    has_explicit_ir35 = bool(
        _OUTSIDE_IR35_RE.search(text) or _INSIDE_IR35_RE.search(text)
    )

    if _PERMANENT_RE.search(text) and not has_explicit_ir35:
        engagement_type = "permanent"
    elif _FTC_RE.search(text):
        engagement_type = "ftc"
    elif _INTERIM_RE.search(text):
        engagement_type = "interim"
    elif _CONTRACT_RE.search(text):
        engagement_type = "contract"
    else:
        engagement_type = "unknown"

    if engagement_type == "permanent":
        ir35_status = "not_applicable"
    elif _OUTSIDE_IR35_RE.search(text):
        ir35_status = "outside"
    elif _INSIDE_IR35_RE.search(text):
        ir35_status = "inside"
    elif _BARE_IR35_RE.search(text) or engagement_type in (
        "contract",
        "ftc",
        "interim",
    ):
        ir35_status = "undetermined"
    else:
        ir35_status = "unknown"

    if _AGENCY_PAYE_RE.search(text):
        engagement_vehicle = "agency_paye"
    elif _UMBRELLA_RE.search(text):
        engagement_vehicle = "umbrella"
    elif _LIMITED_RE.search(text):
        engagement_vehicle = "limited"
    elif _PAYE_RE.search(text):
        engagement_vehicle = "paye"
    else:
        engagement_vehicle = "unknown"

    return EngagementClassification(
        engagement_type=engagement_type,
        ir35_status=ir35_status,
        engagement_vehicle=engagement_vehicle,
    )


WORKING_DAYS_PER_YEAR = 260
"""52 weeks × 5 days, no holiday deduction. Matches Adzuna's own implicit
day-rate-to-annual conversion, confirmed against real bronze data: posting
5860498506 states '£450 - £500 per day' and Adzuna's own structured
salary_min/salary_max for that row are 117000/130000 — exactly
450 × 260 and 500 × 260. Contestable (see PLAN.md Step 5a); documented
here so a future change is deliberate, not silent."""

HOURS_PER_DAY = 7.5
"""Standard UK working day, used only to bridge hourly rates to the same
daily/annual figures as day-rate and annual postings."""

_CURRENCY_SYMBOLS = {"£": "GBP", "$": "USD", "€": "EUR"}

_RATE_PHRASE_RE = re.compile(
    r"([£$€])\s*([\d,]+(?:\.\d+)?k?)\s*(?:-|to)?\s*"
    r"([£$€]?\s*[\d,]+(?:\.\d+)?k?)?\s*"
    r"per\s*(day|hour|annum|year|month)",
    re.IGNORECASE,
)

_K_SHORTHAND_RE = re.compile(r"(\d+(?:\.\d+)?)k", re.IGNORECASE)

# Must start with a digit: `[\d,]+` would match a bare "," as a whole
# token (e.g. in "Competitive, negotiable"), which `_parse_amount` then
# turned into float("") -> ValueError, aborting the whole enrichment run.
_SALARY_RAW_NUMBERS_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

_MONTHS_RE = re.compile(r"\b(\d{1,2})[\s-]*month", re.IGNORECASE)

_EXTENSION_LIKELY_RE = re.compile(
    r"\b(view to extend|likely to (?:be )?extend|potential to extend)\b",
    re.IGNORECASE,
)
_EXTENSION_UNLIKELY_RE = re.compile(r"\bno extension\b", re.IGNORECASE)
_EXTENSION_POSSIBLE_RE = re.compile(
    r"\b(possible extension|may be extended|extension possible)\b", re.IGNORECASE
)

_BASIS_PHRASE_TO_KEY = {
    "day": "daily",
    "hour": "hourly",
    "annum": "annual",
    "year": "annual",
    "month": "monthly",
}


def _parse_amount(raw: str) -> float:
    """Parse one amount fragment, handling a trailing 'k' shorthand.

    Args:
        raw: A numeric fragment, e.g. "450", "1,500", "80k".

    Returns:
        The parsed float, with 'k' expanded (e.g. "80k" -> 80000.0).

    Raises:
        ValueError: If `raw` holds no digits at all. Callers are expected
            to pass fragments matched by a digit-anchored regex, so this
            is a programming error rather than a data condition.
    """
    match = _K_SHORTHAND_RE.fullmatch(raw.strip())
    if match:
        return float(match.group(1)) * 1000
    # Belt-and-braces: strip stray leading/trailing commas so a fragment
    # like ",500" or "500," can never reach float() as an empty string.
    return float(raw.strip().strip(",").replace(",", ""))


def _extract_rate_from_phrase(
    text: str,
) -> tuple[str, str | None, float] | None:
    """Find an explicit '£X - £Y per <basis>' style phrase.

    Args:
        text: The posting's free text.

    Returns:
        A tuple of (basis, currency, midpoint_amount), or `None` if no
        rate phrase is found. `basis` is one of daily/hourly/annual/
        monthly (monthly is collapsed to annual by the caller).
    """
    match = _RATE_PHRASE_RE.search(text)
    if not match:
        return None

    symbol, low_raw, high_raw, period = match.groups()
    currency = _CURRENCY_SYMBOLS.get(symbol)
    low = _parse_amount(low_raw)
    high = _parse_amount(high_raw.lstrip("£$€ ").strip()) if high_raw else low
    basis = _BASIS_PHRASE_TO_KEY[period.lower()]
    return basis, currency, (low + high) / 2


def _extract_rate_from_salary_raw(
    salary_raw: str,
) -> tuple[str | None, float] | None:
    """Fall back to salary_raw's own numbers when no rate phrase is found.

    Treats salary_raw's numbers as already annual-equivalent — true for
    every structured source observed in this session's live bronze
    queries (Adzuna pre-annualises day rates itself; Reed's minimumSalary/
    maximumSalary are plain annual figures).

    Args:
        salary_raw: The staging-layer salary_raw text (may itself be
            free text, e.g. Jooble's "£80k - £95k per year" — that case is
            handled by `_extract_rate_from_phrase` first; this function is
            the fallback for plain numeric ranges like "25000-45000").

    Returns:
        A tuple of (currency_or_None, midpoint_amount), or `None` if no
        number can be parsed at all.
    """
    numbers = _SALARY_RAW_NUMBERS_RE.findall(salary_raw)
    if not numbers:
        return None
    amounts = [_parse_amount(n) for n in numbers]
    currency = "GBP" if "GBP" in salary_raw.upper() else None
    return currency, sum(amounts) / len(amounts)


@dataclass(frozen=True)
class EngagementTerms:
    """The full engagement/IR35/rate picture for one posting (PLAN.md Step 5a).

    Attributes:
        engagement_type: See EngagementClassification.
        ir35_status: See EngagementClassification.
        engagement_vehicle: See EngagementClassification.
        rate_basis: One of annual, daily, hourly, unknown. 'unknown' when
            no rate signal was found at all (e.g. every Greenhouse row).
        rate_currency: ISO-ish 3-letter code (GBP/USD/EUR), or `None` when
            no currency signal was found.
        rate_annualised: The rate annualised, at WORKING_DAYS_PER_YEAR/
            HOURS_PER_DAY where a conversion was needed. `None` when
            rate_basis is 'unknown'. Stated in whatever currency the
            posting used (see rate_currency) — deliberately NOT
            converted to GBP; that conversion is Step 6's parse_salary's
            job, which is why this field is not named `_gbp`.
        rate_daily_equivalent: The rate as a day-rate equivalent, same
            currency caveat as rate_annualised.
        contract_length_months: Stated contract duration in months, or
            `None` when not stated (never 0 — a stated duration is always
            a positive integer here).
        extension_likelihood: One of likely, possible, unlikely, unstated.
    """

    engagement_type: str
    ir35_status: str
    engagement_vehicle: str
    rate_basis: str
    rate_currency: str | None
    rate_annualised: float | None
    rate_daily_equivalent: float | None
    contract_length_months: int | None
    extension_likelihood: str


def extract_engagement_terms(
    description: str | None, salary_raw: str | None
) -> EngagementTerms:
    """Extract the full engagement/IR35/rate picture for one posting.

    Args:
        description: The posting's free text. `None` when unavailable.
        salary_raw: The staging-layer salary_raw column (numeric range,
            free text, or `None` — see `stg_<source>__jobs`'s contract).

    Returns:
        The `EngagementTerms`, with every categorical field explicitly
        set and numeric fields `None` only when genuinely unstated.
    """
    classification = classify_engagement(description)
    text = description or ""

    phrase_rate = _extract_rate_from_phrase(text)
    if phrase_rate is not None:
        basis, currency, amount = phrase_rate
        if basis == "daily":
            daily, annual = amount, amount * WORKING_DAYS_PER_YEAR
        elif basis == "hourly":
            daily = amount * HOURS_PER_DAY
            annual = daily * WORKING_DAYS_PER_YEAR
        else:  # annual or monthly
            annual = amount * 12 if basis == "monthly" else amount
            daily = annual / WORKING_DAYS_PER_YEAR
            basis = "annual"
        rate_basis, rate_currency = basis, currency
        rate_annualised, rate_daily_equivalent = annual, daily
    elif salary_raw:
        salary_rate = _extract_rate_from_phrase(salary_raw)
        if salary_rate is not None:
            basis, currency, amount = salary_rate
            annual = amount * 12 if basis == "monthly" else amount
            if basis == "daily":
                daily, annual = amount, amount * WORKING_DAYS_PER_YEAR
            elif basis == "hourly":
                daily = amount * HOURS_PER_DAY
                annual = daily * WORKING_DAYS_PER_YEAR
            else:
                daily = annual / WORKING_DAYS_PER_YEAR
            rate_basis, rate_currency = (
                "annual" if basis == "monthly" else basis
            ), currency
            rate_annualised, rate_daily_equivalent = annual, daily
        else:
            fallback = _extract_rate_from_salary_raw(salary_raw)
            if fallback is not None:
                currency, annual = fallback
                rate_basis, rate_currency = "annual", currency
                rate_annualised = annual
                rate_daily_equivalent = annual / WORKING_DAYS_PER_YEAR
            else:
                rate_basis, rate_currency = "unknown", None
                rate_annualised, rate_daily_equivalent = None, None
    else:
        rate_basis, rate_currency = "unknown", None
        rate_annualised, rate_daily_equivalent = None, None

    months_match = _MONTHS_RE.search(text)
    contract_length_months = int(months_match.group(1)) if months_match else None

    if _EXTENSION_LIKELY_RE.search(text):
        extension_likelihood = "likely"
    elif _EXTENSION_UNLIKELY_RE.search(text):
        extension_likelihood = "unlikely"
    elif _EXTENSION_POSSIBLE_RE.search(text):
        extension_likelihood = "possible"
    else:
        extension_likelihood = "unstated"

    return EngagementTerms(
        engagement_type=classification.engagement_type,
        ir35_status=classification.ir35_status,
        engagement_vehicle=classification.engagement_vehicle,
        rate_basis=rate_basis,
        rate_currency=rate_currency,
        rate_annualised=rate_annualised,
        rate_daily_equivalent=rate_daily_equivalent,
        contract_length_months=contract_length_months,
        extension_likelihood=extension_likelihood,
    )
