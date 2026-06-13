"""CI regression gate for PolicyLens eval.

Reads a Ragas report JSON (produced by eval/ragas/run_ragas.py) and a
thresholds YAML (eval/thresholds.yaml), then exits non-zero if any gate
fails.

This logic is a standalone script — NOT inline bash — so it is unit-testable
(see tests/test_gate.py) and keeps the CI workflow readable.

Usage (in CI):
    python eval/gate.py \\
        --report eval/ragas/report_<timestamp>.json \\
        [--thresholds eval/thresholds.yaml] \\
        [--faithfulness-threshold 0.80]   # env override: FAITHFULNESS_THRESHOLD

Exit codes:
    0 — all gates passed
    1 — one or more gates failed (CI build fails)
    2 — report file not found / unreadable (CI build fails)
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import Any


def load_report(path: str) -> dict[str, Any]:
    """Load and return the Ragas report JSON."""
    p = Path(path)
    if not p.exists():
        print(f"ERROR: report file not found: {path}", file=sys.stderr)
        sys.exit(2)
    with open(p) as f:
        return json.load(f)  # type: ignore[no-any-return]


def load_thresholds(path: str) -> dict[str, float]:
    """Load thresholds from YAML (or return defaults if file/lib absent)."""
    defaults: dict[str, float] = {
        "faithfulness": 0.80,
        "abstention_accuracy": 0.90,
    }
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return defaults

    p = Path(path)
    if not p.exists():
        return defaults

    with open(p) as f:
        data = yaml.safe_load(f) or {}

    result: dict[str, float] = {}
    for key, default in defaults.items():
        val = data.get(key, default)
        try:
            result[key] = float(val)
        except (TypeError, ValueError):
            result[key] = default

    return result


def check_gate(
    report: dict[str, Any],
    thresholds: dict[str, float],
    faithfulness_override: float | None = None,
) -> tuple[bool, list[str]]:
    """Evaluate the gate conditions.

    Returns (passed: bool, failures: list[str]).
    A NaN metric means "not computed" (dry run / no API key) — gate is skipped.
    """
    failures: list[str] = []

    ragas = report.get("ragas") or {}
    house = report.get("house_metrics") or {}

    # --- Faithfulness gate ---
    faith_thresh = (
        faithfulness_override
        if faithfulness_override is not None
        else thresholds.get("faithfulness", 0.80)
    )
    faith_raw = ragas.get("faithfulness")
    if faith_raw is None:
        faith = float("nan")
    else:
        try:
            faith = float(faith_raw)
        except (TypeError, ValueError):
            faith = float("nan")

    if not math.isnan(faith):
        if faith < faith_thresh:
            failures.append(
                f"faithfulness {faith:.4f} < threshold {faith_thresh:.4f}"
            )
        else:
            print(f"  faithfulness {faith:.4f} >= {faith_thresh:.4f} PASS")
    else:
        print("  faithfulness: NaN — skipped (dry run or no API key)")

    # --- Abstention accuracy gate ---
    abst_thresh = thresholds.get("abstention_accuracy", 0.90)
    abst_raw = house.get("abstention_accuracy")
    if abst_raw is None:
        abst = float("nan")
    else:
        try:
            abst = float(abst_raw)
        except (TypeError, ValueError):
            abst = float("nan")

    if not math.isnan(abst):
        if abst < abst_thresh:
            failures.append(
                f"abstention_accuracy {abst:.4f} < threshold {abst_thresh:.4f}"
            )
        else:
            print(f"  abstention_accuracy {abst:.4f} >= {abst_thresh:.4f} PASS")
    else:
        print("  abstention_accuracy: NaN — skipped (dry run or no API key)")

    # If the report itself already recorded a gate failure, honour it.
    if not report.get("gate_passed", True):
        report_failures = report.get("gate_failures") or []
        for f in report_failures:
            if f not in failures:
                failures.append(f)

    passed = len(failures) == 0
    return passed, failures


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="CI regression gate: check Ragas report against thresholds"
    )
    parser.add_argument(
        "--report",
        required=True,
        help="Path to the Ragas report JSON (output of eval/ragas/run_ragas.py)",
    )
    parser.add_argument(
        "--thresholds",
        default="eval/thresholds.yaml",
        help="Path to thresholds YAML (default: eval/thresholds.yaml)",
    )
    parser.add_argument(
        "--faithfulness-threshold",
        type=float,
        default=None,
        help="Override faithfulness threshold (env: FAITHFULNESS_THRESHOLD)",
    )
    args = parser.parse_args(argv)

    # env override for faithfulness threshold (CONTRACTS §11)
    faith_override: float | None = args.faithfulness_threshold
    env_faith = os.environ.get("FAITHFULNESS_THRESHOLD")
    if env_faith and faith_override is None:
        try:
            faith_override = float(env_faith)
        except ValueError:
            print(
                f"WARNING: FAITHFULNESS_THRESHOLD={env_faith!r} is not a float; ignored",
                file=sys.stderr,
            )

    print(f"Loading report: {args.report}")
    report = load_report(args.report)

    print(f"Loading thresholds: {args.thresholds}")
    thresholds = load_thresholds(args.thresholds)

    n_items = report.get("n_items", "?")
    backend = report.get("backend", "?")
    ts = report.get("timestamp", "?")
    print(f"Report: {n_items} items, backend={backend}, timestamp={ts}")
    print("Checking gates:")

    passed, failures = check_gate(report, thresholds, faith_override)

    if failures:
        print("\nGATE FAILED:")
        for f in failures:
            print(f"  FAIL: {f}")
        return 1

    print("\nAll gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
