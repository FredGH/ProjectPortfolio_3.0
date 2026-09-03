"""Pipeline batch entrypoint.

Real subcommands (`ingest`, `dedup`, ...) land in Step 3+. This module's
job for Step 1 is only to prove the pipeline image runs as a one-shot batch
process rather than a long-lived server.
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    """Run the pipeline CLI.

    Args:
        argv: Command-line arguments, excluding the program name. Defaults
            to `sys.argv[1:]` when `None`.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(prog="pipeline")
    parser.parse_args(argv)
    print("pipeline scaffold ready — no subcommands yet (see Step 3)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
