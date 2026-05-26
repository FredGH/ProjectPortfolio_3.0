"""Evaluation harness for the agentic triage system.

Metrics
-------
- F1 per priority level (P1–P4) + macro F1          gate ≥ 0.80
- RAGAS faithfulness (Ollama or heuristic fallback)  gate ≥ 0.75
- Pre-filter deflection rate (is_auto_p4 fraction)   bounds 20%–70%  (Guard Rail 9)
- Cache hit rate                                      informational

Exit codes
----------
0  all gates passed
1  one or more gates failed
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

LABELS = ["P1", "P2", "P3", "P4"]
DEFLECTION_LO = 0.20
DEFLECTION_HI = 0.70


# ── F1 helpers ────────────────────────────────────────────────────────────────

def _f1_per_class(
    expected: list[str],
    predicted: list[str],
    labels: list[str],
) -> dict[str, dict[str, float]]:
    results: dict[str, dict[str, float]] = {}
    for label in labels:
        tp = sum(e == label and p == label for e, p in zip(expected, predicted))
        fp = sum(e != label and p == label for e, p in zip(expected, predicted))
        fn = sum(e == label and p != label for e, p in zip(expected, predicted))
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )
        results[label] = {"precision": precision, "recall": recall, "f1": f1}
    return results


def _macro_f1(per_class: dict[str, dict[str, float]]) -> float:
    scores = [v["f1"] for v in per_class.values()]
    return sum(scores) / len(scores) if scores else 0.0


# ── Faithfulness ──────────────────────────────────────────────────────────────

def _heuristic_faithfulness(items: list[dict[str, Any]]) -> float:
    """Sentence-level support proxy: a reasoning sentence is 'supported' if at
    least one non-trivial content word from it appears in the retrieved context.
    Faithfulness per item = supported_sentences / total_sentences.
    This mimics the RAGAS sentence-level decomposition without an LLM.
    """
    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "and", "or", "but", "in",
        "on", "at", "to", "for", "of", "with", "by", "from", "as", "this",
        "that", "these", "those", "it", "its", "their", "they", "he", "she",
        "we", "you", "i", "not", "no", "any", "all", "if", "so", "than",
        "which", "who", "what", "when", "where", "how", "also", "may", "must",
        "will", "would", "could", "should", "very", "more", "most", "both",
        "each", "such", "your", "our", "here", "there", "been", "just",
    }

    def _content_words(text: str) -> set[str]:
        return {
            w.lower().strip(".,;:()[]\"'—–")
            for w in text.split()
            if len(w) > 3 and w.lower().strip(".,;:()[]\"'—–") not in stopwords
        }

    scores: list[float] = []
    for item in items:
        reasoning = item.get("reasoning", "").strip()
        context_blob = " ".join(item.get("retrieved_context", [])).lower()
        if not reasoning or not context_blob:
            scores.append(1.0)
            continue

        sentences = [s.strip() for s in reasoning.replace("—", " ").split(".") if s.strip()]
        if not sentences:
            scores.append(1.0)
            continue

        supported = sum(
            1
            for sent in sentences
            if any(w in context_blob for w in _content_words(sent))
        )
        scores.append(supported / len(sentences))

    return sum(scores) / len(scores) if scores else 0.0


def _ragas_faithfulness(items: list[dict[str, Any]]) -> float | None:
    """Attempt RAGAS faithfulness evaluation; return None if unavailable."""
    try:
        from datasets import Dataset  # type: ignore[import]
        from ragas import evaluate  # type: ignore[import]
        from ragas.metrics import faithfulness  # type: ignore[import]
    except ImportError:
        return None

    rows = [
        {
            "question": item["text"],
            "answer": item.get("reasoning", ""),
            "contexts": item.get("retrieved_context", []),
        }
        for item in items
        if item.get("reasoning") and item.get("retrieved_context")
    ]
    if not rows:
        return None

    try:
        ds = Dataset.from_list(rows)
        result = evaluate(ds, metrics=[faithfulness])
        return float(result["faithfulness"])
    except Exception:  # noqa: BLE001
        return None


# ── Deflection / cache ────────────────────────────────────────────────────────

def _deflection_rate(items: list[dict[str, Any]]) -> float:
    if not items:
        return 0.0
    return sum(1 for x in items if x.get("is_auto_p4", False)) / len(items)


def _cache_hit_rate(items: list[dict[str, Any]]) -> float:
    if not items:
        return 0.0
    return sum(1 for x in items if x.get("is_cache_hit", False)) / len(items)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Agentic triage evaluation harness")
    parser.add_argument("--dataset", required=True, help="Path to golden_dataset.json")
    parser.add_argument(
        "--f1-threshold", type=float, default=0.80, help="Macro F1 gate (default 0.80)"
    )
    parser.add_argument(
        "--faithfulness-threshold",
        type=float,
        default=0.75,
        help="Faithfulness gate (default 0.75)",
    )
    parser.add_argument(
        "--report-path",
        default="evaluation/report.json",
        help="Where to write report.json",
    )
    args = parser.parse_args()

    with open(args.dataset) as fh:
        dataset: list[dict[str, Any]] = json.load(fh)

    report: dict[str, Any] = {
        "total": len(dataset),
        "f1_threshold": args.f1_threshold,
        "faithfulness_threshold": args.faithfulness_threshold,
        "f1_per_class": None,
        "macro_f1": None,
        "faithfulness": None,
        "faithfulness_method": None,
        "deflection_rate": None,
        "deflection_in_bounds": None,
        "cache_hit_rate": None,
        "gates_passed": True,
        "failures": [],
    }

    Path(args.report_path).write_text(json.dumps(report, indent=2))

    if not dataset:
        print("No items in dataset — nothing to evaluate.")
        sys.exit(0)

    failures: list[str] = []

    # ── F1 ────────────────────────────────────────────────────────────────────
    expected = [x["expected_priority"] for x in dataset]
    predicted = [x["predicted_priority"] for x in dataset]

    per_class = _f1_per_class(expected, predicted, LABELS)
    macro = _macro_f1(per_class)
    report["f1_per_class"] = per_class
    report["macro_f1"] = round(macro, 4)

    print("F1 per class:")
    for lbl, scores in per_class.items():
        print(
            f"  {lbl}  precision={scores['precision']:.3f}"
            f"  recall={scores['recall']:.3f}"
            f"  f1={scores['f1']:.3f}"
        )
    print(f"Macro F1: {macro:.4f}  (gate ≥ {args.f1_threshold})")

    if macro < args.f1_threshold:
        msg = f"Macro F1 {macro:.4f} < threshold {args.f1_threshold}"
        failures.append(msg)
        print(f"FAIL: {msg}")
    else:
        print("PASS: Macro F1 gate")

    # ── Faithfulness ──────────────────────────────────────────────────────────
    faith_score = _ragas_faithfulness(dataset)
    method = "ragas"
    if faith_score is None:
        faith_score = _heuristic_faithfulness(dataset)
        method = "heuristic"

    report["faithfulness"] = round(faith_score, 4)
    report["faithfulness_method"] = method
    print(
        f"Faithfulness ({method}): {faith_score:.4f}"
        f"  (gate ≥ {args.faithfulness_threshold})"
    )

    if faith_score < args.faithfulness_threshold:
        msg = f"Faithfulness {faith_score:.4f} < threshold {args.faithfulness_threshold}"
        failures.append(msg)
        print(f"FAIL: {msg}")
    else:
        print("PASS: Faithfulness gate")

    # ── Deflection bounds (Guard Rail 9) ──────────────────────────────────────
    defl = _deflection_rate(dataset)
    in_bounds = DEFLECTION_LO <= defl <= DEFLECTION_HI
    report["deflection_rate"] = round(defl, 4)
    report["deflection_in_bounds"] = in_bounds
    print(
        f"Auto-P4 deflection rate: {defl:.1%}"
        f"  (bounds {DEFLECTION_LO:.0%}–{DEFLECTION_HI:.0%})"
    )

    if not in_bounds:
        if defl < DEFLECTION_LO:
            msg = (
                f"Deflection rate {defl:.1%} < {DEFLECTION_LO:.0%} —"
                " keyword/retrieval coverage may be degrading"
            )
        else:
            msg = (
                f"Deflection rate {defl:.1%} > {DEFLECTION_HI:.0%} —"
                " pre-filter may be over-triggering"
            )
        failures.append(msg)
        print(f"FAIL: {msg}")
    else:
        print("PASS: Deflection bounds (Guard Rail 9)")

    # ── Cache hit rate (informational) ────────────────────────────────────────
    cache_rate = _cache_hit_rate(dataset)
    report["cache_hit_rate"] = round(cache_rate, 4)
    print(f"Cache hit rate: {cache_rate:.1%}  (informational)")

    # ── Final report ──────────────────────────────────────────────────────────
    report["gates_passed"] = len(failures) == 0
    report["failures"] = failures

    Path(args.report_path).write_text(json.dumps(report, indent=2))
    print(f"\nReport written to {args.report_path}")

    if failures:
        print(f"\n{len(failures)} gate(s) failed:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)

    print("\nAll gates passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
