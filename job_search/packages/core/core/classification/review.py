"""Shared constants for the categorisation-review tooling — used by
both `apps/api/app/routers/classification.py` and the Streamlit
`apps/ui/app/pages/4_Categorisation_Review.py`, which can't import
from each other directly (separate deployables sharing only `core`).
"""

from __future__ import annotations

# Sentinel `country_iso` query-param value meaning "rows where
# gold.dim_job.country_iso IS NULL" (core.normalisation.location's
# normalise_location never guesses a country, so most non-UK,
# multi-city, or free-text locations resolve to NULL) — distinct from
# omitting the param, which means "no country filter at all".
UNRESOLVED_COUNTRY = "__unknown__"
