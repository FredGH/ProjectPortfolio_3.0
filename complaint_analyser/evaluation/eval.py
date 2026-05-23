import argparse
import json
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--f1-threshold", type=float, default=0.80)
    parser.add_argument("--faithfulness-threshold", type=float, default=0.75)
    args = parser.parse_args()

    with open(args.dataset) as fh:
        dataset = json.load(fh)

    report: dict = {"total": len(dataset), "f1": None, "faithfulness": None}
    Path("evaluation/report.json").write_text(json.dumps(report, indent=2))

    if not dataset:
        sys.exit(0)

    # Phase 13 implements RAGAS evaluation against thresholds
    raise NotImplementedError("Evaluation not implemented for non-empty datasets")


if __name__ == "__main__":
    main()
