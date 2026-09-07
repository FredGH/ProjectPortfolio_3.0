"""Real examples pulled live from bronze.raw_jobs in this project's own
session (PLAN.md Step 6's testing rule: real rows, never invented).
Company/title/location strings are quoted exactly as stored; only salary
figures are lightly restated as plain numbers for readability.
"""

from __future__ import annotations

# (source_name, raw_company, expected_normalised_company)
COMPANY_EXAMPLES: list[tuple[str, str, str]] = [
    ("adzuna", "Oscar Associates  Limited", "Oscar Associates"),
    ("adzuna", "ZENZO DIGITAL LTD", "Zenzo Digital"),
    ("adzuna", "ODIN RECRUITMENT GROUP LIMITED", "Odin Recruitment Group"),
    ("reed", "Archangel Lightworks Ltd", "Archangel Lightworks"),
    ("reed", "Photo-Sonics International Ltd", "Photo-Sonics International"),
    ("reed", "Network Mapping Limited", "Network Mapping"),
    ("reed", "Eden James Consulting Limited", "Eden James Consulting"),
    (
        "reed",
        "MSC Mediterranean Shipping Company (UK)",
        "MSC Mediterranean Shipping Company",
    ),
    ("reed", "Sanderson ", "Sanderson"),
    ("greenhouse", "Stripe", "Stripe"),
    ("greenhouse", "Anthropic", "Anthropic"),
    ("greenhouse", "Pinterest", "Pinterest"),
]

# (raw_title, expected_strip_title, expected_title_for_display)
TITLE_EXAMPLES: list[tuple[str, str, str]] = [
    ("Senior Data Engineer", "Data Engineer", "Senior Data Engineer"),
    (
        "Full Stack Product Engineer - Remote/Europe",
        "Full Stack Product Engineer",
        "Full Stack Product Engineer",
    ),
    (
        "Senior AWS Cloud Engineer | S4 | Data & AI Domain | Multiple Locations",
        "AWS Cloud Engineer",
        "Senior AWS Cloud Engineer",
    ),
    (
        "Sr. Client Account Manager | Nordics (CPG)",
        "Client Account Manager",
        "Sr. Client Account Manager",
    ),
    (
        "Core Software Engineer (C++) - Remote",
        "Core Software Engineer (C++)",
        "Core Software Engineer (C++)",
    ),
    (
        "Senior Network Planner - Occupancy",
        "Network Planner - Occupancy",
        "Senior Network Planner - Occupancy",
    ),
    (
        "Staff+ Software Engineer, Kubernetes Platform",
        "Software Engineer, Kubernetes Platform",
        "Staff+ Software Engineer, Kubernetes Platform",
    ),
    (
        "Senior Data Engineer, Public Sector",
        "Data Engineer, Public Sector",
        "Senior Data Engineer, Public Sector",
    ),
    ("BI Data Engineer", "BI Data Engineer", "BI Data Engineer"),
    ("Software Team Leader", "Software Team Leader", "Software Team Leader"),
    # Synthetic, not from bronze — PLAN.md Step 6 names these patterns
    # explicitly (m/f/d, req IDs) but neither appears in this session's
    # real bronze sample.
    (
        "Data Engineer (m/f/d)",
        "Data Engineer",
        "Data Engineer",
    ),
    (
        "Data Engineer (Req ID: 48213)",
        "Data Engineer",
        "Data Engineer",
    ),
]

# (raw_location, expected_country_iso, expected_region, expected_is_remote)
LOCATION_EXAMPLES: list[tuple[str, str | None, str | None, bool]] = [
    ("Central London, London", "GB", "UKI", False),
    ("Kensington, West London", "GB", "UKI", False),
    ("Hemel Hempstead, Hertfordshire", "GB", "UKH", False),
    ("Rotherham, South Yorkshire", "GB", "UKE", False),
    ("Sheffield, South Yorkshire", "GB", "UKE", False),
    ("Tunbridge Wells, Kent", "GB", "UKJ", False),
    ("Maidstone, Kent", "GB", "UKJ", False),
    ("Oxfordshire, South East England", "GB", "UKJ", False),
    ("Dorset, South West England", "GB", "UKK", False),
    ("Burton-On-Trent, Staffordshire", "GB", "UKG", False),
    ("Ladywood, Birmingham", "GB", "UKG", False),
    ("Inverkip, Greenock", "GB", "UKM", False),
    ("Downpatrick, County Down", "GB", "UKN", False),
    ("BB17DY", "GB", None, False),
    ("SW1E5LB", "GB", None, False),
    ("Salt Lake City, UT", None, None, False),
    ("Dublin", None, None, False),
    ("Berlin, DE", "DE", None, False),
    ("MX- Mexico City", "MX", None, False),
    ("Seoul, South Korea", "KR", None, False),
    ("US-Remote, Chicago, Seattle, San Francisco", "US", None, True),
    ("n/a", None, None, False),
    ("Remote in the US", "US", None, True),
    # Synthetic regression cases, not from bronze: "DE" is both Germany's
    # ISO code and the US postal code for Delaware, so a bare ", DE" is
    # ambiguous and must resolve to nothing rather than to Germany.
    # "Berlin, DE" above still resolves, via the city name.
    ("Wilmington, DE", None, None, False),
    ("Newark, DE", None, None, False),
]

# (description, salary_raw, expected_annualised_gbp_or_None)
SALARY_EXAMPLES: list[tuple[str | None, str | None, float | None]] = [
    (
        "Senior Data Engineer – Microsoft Fabric Contract: Outside IR35 "
        "Rate : £450 - £500 per day",
        "117000-130000",
        475 * 260,
    ),
    ("Data Engineer, permanent role, London.", "130000-130000", 130000.0),
    (None, "£80k - £95k per year", 87500.0),
    ("Senior Data Engineer, Public Sector", None, None),
]
