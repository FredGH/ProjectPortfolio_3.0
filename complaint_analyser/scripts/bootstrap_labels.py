"""
Bootstrap priority labels for complaints_history seed data.

Reads raw complaint narratives from the data/ zip, calls Ollama to generate
priority + dimension scores, and writes a JSONL file for Phase 6 ingest.

Usage:
    python scripts/bootstrap_labels.py --limit 5 --dry-run   # preview prompt
    python scripts/bootstrap_labels.py --limit 20            # label first 20
    python scripts/bootstrap_labels.py                       # label all rows
"""
import argparse
import csv
import io
import json
import re
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import yaml


def _load_config(config_path: Path) -> dict:
    with config_path.open() as f:
        return yaml.safe_load(f)


def _build_prompt(narrative: str, config: dict) -> str:
    dims = config["scoring_dimensions"]
    levels = sorted(config["priority_levels"], key=lambda x: -x["min_composite"])

    dim_lines = "\n".join(
        f"  - {d['name']} (weight {d['weight']}): {d['description']}"
        for d in dims
    )

    level_lines = "\n".join(
        "  - {label}: composite >= {mc}{esc}".format(
            label=lv["label"],
            mc=lv["min_composite"],
            esc=(
                f", OR any single dimension > {lv['escalate_if_any_dimension_exceeds']}"
                if lv.get("escalate_if_any_dimension_exceeds")
                else ""
            ),
        )
        for lv in levels
    )

    dim_json_keys = ", ".join(f'"{d["name"]}": 0.0' for d in dims)

    return (
        "You are a financial complaint triage analyst. Score this banking complaint.\n\n"
        f"Scoring dimensions (each 0.0–5.0):\n{dim_lines}\n\n"
        f"Composite score = sum of (score × weight). Priority thresholds:\n{level_lines}\n\n"
        "Respond ONLY with a JSON object — no markdown, no explanation:\n"
        "{\n"
        '  "priority": "<P1|P2|P3|P4>",\n'
        f'  "dimension_scores": {{{dim_json_keys}}},\n'
        '  "composite_score": 0.0,\n'
        '  "confidence": 0.0,\n'
        '  "reasoning": "<1-2 sentences>",\n'
        '  "recommended_action": "<brief action>"\n'
        "}\n\n"
        f"Complaint (max 800 chars):\n{narrative[:800]}"
    )


def _call_ollama(prompt: str, model: str, host: str, timeout: int) -> dict | None:
    payload = json.dumps(
        {"model": model, "prompt": prompt, "stream": False, "format": "json"}
    ).encode()
    req = urllib.request.Request(
        f"{host}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
            return json.loads(body["response"])
    except (urllib.error.URLError, json.JSONDecodeError, KeyError):
        return None


def _read_rows(data_dir: Path) -> list[dict]:
    zips = sorted(data_dir.glob("*.zip"))
    if not zips:
        raise FileNotFoundError(f"No zip file found in {data_dir}")
    with zipfile.ZipFile(zips[0]) as zf:
        csv_names = [n for n in zf.namelist() if n.endswith(".csv") and not n.startswith("__")]
        if not csv_names:
            raise FileNotFoundError("No CSV inside zip")
        with zf.open(csv_names[0]) as fh:
            reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace"))
            return list(reader)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap complaint priority labels using Ollama.")
    parser.add_argument("--limit", type=int, default=None, help="Max complaints to process (default: all)")
    parser.add_argument("--offset", type=int, default=0, help="Skip the first N rows")
    parser.add_argument("--output", default="data/complaints_labelled.jsonl", help="Output JSONL path")
    parser.add_argument("--model", default="llama3.1:8b", help="Ollama model name")
    parser.add_argument("--ollama-host", default="http://localhost:11434", help="Ollama base URL")
    parser.add_argument("--timeout", type=int, default=120, help="Per-request timeout in seconds")
    parser.add_argument("--dry-run", action="store_true", help="Print first prompt and exit without calling Ollama")
    parser.add_argument("--domain", default="domains/banking_complaints/config.yaml", help="Domain config path")
    parser.add_argument("--data-dir", default="data", help="Data directory")
    args = parser.parse_args()

    config = _load_config(Path(args.domain))
    rows = _read_rows(Path(args.data_dir))

    if args.offset:
        rows = rows[args.offset :]
    if args.limit:
        rows = rows[: args.limit]

    print(f"Processing {len(rows)} complaints")

    if args.dry_run:
        print("\n--- Prompt for row 0 ---\n")
        print(_build_prompt(rows[0].get("narrative", ""), config))
        return

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    ok, failed = 0, 0
    t0 = time.time()

    with output_path.open("w") as out:
        for i, row in enumerate(rows):
            narrative = row.get("narrative", "").strip()
            if not narrative:
                failed += 1
                continue

            result = _call_ollama(
                _build_prompt(narrative, config),
                args.model,
                args.ollama_host,
                args.timeout,
            )

            if result is None:
                print(f"  [{i + 1:>5}/{len(rows)}] FAILED")
                failed += 1
                continue

            # Normalise priority: strip angle brackets, map "level" key alias
            if "level" in result and "priority" not in result:
                result["priority"] = result.pop("level")
            if "priority" in result:
                result["priority"] = re.sub(r"[<>\[\]]", "", str(result["priority"])).strip()

            record = {
                "input_id": f"seed_{args.offset + i:05d}",
                "product": row.get("product", ""),
                "narrative": narrative,
                "domain": config["domain_name"],
                "source": "bootstrap",
                **result,
            }
            out.write(json.dumps(record) + "\n")
            ok += 1

            if (i + 1) % 25 == 0 or (i + 1) == len(rows):
                elapsed = time.time() - t0
                rate = ok / elapsed if elapsed > 0 else 0
                print(f"  [{i + 1:>5}/{len(rows)}] ok={ok} failed={failed} ({rate:.1f}/s)")

    print(f"\nDone: ok={ok}, failed={failed}, output={output_path}")
    if ok > 0:
        print(
            "\nNext steps:\n"
            f"  1. Review {output_path} — spot-check a few rows for label quality\n"
            "  2. Curate 20 high-confidence rows into evaluation/golden_dataset.json\n"
            "  3. Run Phase 6 ingest to load complaints_labelled.jsonl into Qdrant"
        )


if __name__ == "__main__":
    main()
