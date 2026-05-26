"""
Prints the first N lines of every file in the data/ directory.
Handles plain files and files inside zip archives.

Usage:
    python scripts/peek_data.py              # first 10 lines (default)
    python scripts/peek_data.py --lines 20  # first 20 lines
    python scripts/peek_data.py --dir path/to/other/dir
"""
import argparse
import io
import zipfile
from pathlib import Path


def peek(source_name: str, lines_iter, n: int) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {source_name}")
    print(f"{'=' * 60}")
    for i, line in enumerate(lines_iter):
        if i >= n:
            break
        print(line.rstrip("\n"))


def process_file(path: Path, n: int) -> None:
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if name.endswith("/"):
                    continue
                with zf.open(name) as fh:
                    lines = io.TextIOWrapper(fh, encoding="utf-8", errors="replace")
                    peek(f"{path.name}::{name}", lines, n)
    else:
        with path.open(encoding="utf-8", errors="replace") as fh:
            peek(path.name, fh, n)


def main() -> None:
    parser = argparse.ArgumentParser(description="Peek at the first N lines of data files.")
    parser.add_argument("--lines", type=int, default=10, help="Number of lines to show (default: 10)")
    parser.add_argument("--dir", default="data", help="Directory to scan (default: data/)")
    args = parser.parse_args()

    data_dir = Path(args.dir)
    if not data_dir.exists():
        print(f"Directory not found: {data_dir}")
        raise SystemExit(1)

    files = sorted(p for p in data_dir.iterdir() if p.is_file())
    if not files:
        print(f"No files found in {data_dir}")
        raise SystemExit(0)

    for path in files:
        process_file(path, args.lines)


if __name__ == "__main__":
    main()
