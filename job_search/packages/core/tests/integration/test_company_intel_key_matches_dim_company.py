"""company_key must equal gold.dim_company.company_key."""

from __future__ import annotations

import unittest

from sqlalchemy import text
from tests.integration.skills_fixtures import live_app_engine

from core.company_intel.keys import company_key


class TestKeyMatchesDimCompany(unittest.TestCase):
    """The Python key joins to the dbt key."""

    def test_the_first_hundred_companies_match(self) -> None:
        engine = live_app_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT company_key, normalised_company FROM gold.dim_company "
                    "ORDER BY company_key LIMIT 100"
                )
            ).all()
        self.assertGreater(len(rows), 0)
        for row in rows:
            self.assertEqual(company_key(row.normalised_company), row.company_key)


if __name__ == "__main__":
    unittest.main()
