"""Company keys that match `gold.dim_company` (PLAN.md Step 19a)."""

from __future__ import annotations

import hashlib

from core.normalisation.company import normalise_company


def company_key(name: str | None) -> str | None:
    """Build the key intel is stored under.

    Matches `gold.dim_company.company_key`, which is
    `dbt_utils.generate_surrogate_key` over the normalised name: the md5 hex
    digest of that string.

    Args:
        name: A company name as written on a posting or typed by the user.

    Returns:
        The md5 hex key, or None when the name is missing or blank after
        normalisation.
    """
    normalised = normalise_company(name)
    if not normalised:
        return None
    return hashlib.md5(  # noqa: S324 - a stable key, not a security hash
        normalised.encode("utf-8")
    ).hexdigest()
