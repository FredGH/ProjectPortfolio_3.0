"""Replace live endpoint URL placeholders in tca/README.md after deployment."""

from __future__ import annotations

import os
import re

readme = "tca/README.md"
cf_url = os.environ["CF_URL"]
alb_dns = os.environ["ALB_DNS"]
dashboard_url = os.environ["DASHBOARD_URL"]

replacements = {
    ("CLOUDFRONT_URL_START", "CLOUDFRONT_URL_END"): (
        f"<!-- CLOUDFRONT_URL_START -->\n```\n{cf_url}\n```\n<!-- CLOUDFRONT_URL_END -->"
    ),
    ("AIRFLOW_URL_START", "AIRFLOW_URL_END"): (
        f"<!-- AIRFLOW_URL_START -->\n```\nhttp://{alb_dns}/airflow\n```\n<!-- AIRFLOW_URL_END -->"
    ),
    ("API_DOCS_URL_START", "API_DOCS_URL_END"): (
        f"<!-- API_DOCS_URL_START -->\n```\nhttp://{alb_dns}/api/docs\n```\n<!-- API_DOCS_URL_END -->"
    ),
    ("DASHBOARD_URL_START", "DASHBOARD_URL_END"): (
        f"<!-- DASHBOARD_URL_START -->\n```\n{dashboard_url}\n```\n<!-- DASHBOARD_URL_END -->"
    ),
}

text = open(readme).read()
for (start, end), replacement in replacements.items():
    text = re.sub(
        rf"<!-- {start} -->.*?<!-- {end} -->",
        replacement,
        text,
        flags=re.DOTALL,
    )
open(readme, "w").write(text)
print("README endpoints updated.")
