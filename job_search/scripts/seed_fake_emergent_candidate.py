"""Insert (or remove) a clearly labelled FAKE emergent-role candidate.

Lets you try the Emergent Roles page's confirm/dismiss buttons while the real
corpus has no candidate. Run from `job_search/` with the database URL pointing
at localhost:

    python scripts/seed_fake_emergent_candidate.py            # add it
    python scripts/seed_fake_emergent_candidate.py --remove   # take it away
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "core"))

from core.db.session import build_engine  # noqa: E402
from core.emergent.testdata import remove_fake, seed_fake_candidate  # noqa: E402
from core.settings import get_settings  # noqa: E402


def main() -> int:
    """Add or remove the fake candidate.

    Returns:
        0 on success.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--remove", action="store_true", help="Delete the fake candidate"
    )
    args = parser.parse_args()
    engine = build_engine(get_settings().database_url)  # the owner role: it may delete
    if args.remove:
        removed = remove_fake(engine)
        print(f"removed {removed} fake run(s); the real latest run is current again")
        return 0
    seed_fake_candidate(engine)
    print("added a FAKE candidate as the newest run; open the Emergent Roles page")
    print("remove it with: python scripts/seed_fake_emergent_candidate.py --remove")
    return 0


if __name__ == "__main__":
    sys.exit(main())
