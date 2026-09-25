#!/usr/bin/env python3
"""Compare a k6 performance-test summary against an explicit, human-reviewed baseline.

This is deliberately deterministic (no LLM, no network calls): it only reads the JSON
files it is given and does arithmetic against numbers a human already reviewed and
committed to ``tests/performance/baseline.json``. It never invents a threshold.

Two subcommands:

  compare          Read a k6 summary.json + baseline.json, render a markdown report,
                   and decide pass/fail. In "advisory" mode (the default, and the only
                   mode used automatically by .github/workflows/performance.yml on pull
                   requests) it always exits 0 regardless of the comparison outcome --
                   it only *reports*. In "strict" mode it exits non-zero if any metric
                   breaches its threshold, but only once a real baseline exists; while
                   the baseline is still in its bootstrap ``"status": "unset"`` state,
                   strict mode also exits 0 (there is nothing to gate against yet).

  capture-baseline A maintainer-run helper (never invoked automatically) that turns one
                   reviewed summary.json into a new baseline.json, preserving whatever
                   tolerance multipliers are already configured.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCENARIOS = ("reads", "lifecycle")

DEFAULT_TOLERANCE = {
    "p95_latency_ms_multiplier": 1.5,
    "p99_latency_ms_multiplier": 1.75,
    "error_rate_max": 0.0,
    "throughput_min_multiplier": 0.7,
}


class GateError(ValueError):
    """Raised for malformed input files; never for a threshold breach."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError as error:
        raise GateError(f"File not found: {path}") from error
    except json.JSONDecodeError as error:
        raise GateError(f"{path} is not valid JSON: {error}") from error


def extract_metrics(summary: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Pull the per-scenario duration/error/throughput numbers this script owns out of
    a raw k6 summary.json (the object k6's handleSummary() receives)."""
    metrics = summary.get("metrics")
    if not isinstance(metrics, dict):
        raise GateError("summary.json has no 'metrics' object; is this a k6 summary export?")

    duration_ms = summary.get("state", {}).get("testRunDurationMs")
    run_seconds = (duration_ms / 1000) if isinstance(duration_ms, (int, float)) and duration_ms > 0 else None

    extracted: dict[str, dict[str, float]] = {}
    for scenario in SCENARIOS:
        duration_metric = metrics.get(f"{scenario}_duration", {}).get("values", {})
        error_metric = metrics.get(f"{scenario}_errors", {}).get("values", {})
        if not duration_metric:
            # This scenario didn't run (e.g. a partial/manual invocation); skip it
            # rather than fabricate zeros.
            continue

        count = duration_metric.get("count")
        throughput = (count / run_seconds) if count is not None and run_seconds else None

        extracted[scenario] = {
            "p50_ms": duration_metric.get("med"),
            "p95_ms": duration_metric.get("p(95)"),
            "p99_ms": duration_metric.get("p(99)"),
            "error_rate": error_metric.get("rate", 0.0),
            "requests_per_s": throughput,
            "count": count,
        }
    return extracted


def compute_threshold(metric_name: str, baseline_value: float | None, tolerance: dict[str, Any]) -> float | None:
    if baseline_value is None:
        return None
    if metric_name == "p95_ms":
        return baseline_value * float(tolerance.get("p95_latency_ms_multiplier", DEFAULT_TOLERANCE["p95_latency_ms_multiplier"]))
    if metric_name == "p99_ms":
        return baseline_value * float(tolerance.get("p99_latency_ms_multiplier", DEFAULT_TOLERANCE["p99_latency_ms_multiplier"]))
    if metric_name == "requests_per_s":
        return baseline_value * float(tolerance.get("throughput_min_multiplier", DEFAULT_TOLERANCE["throughput_min_multiplier"]))
    return None


def evaluate(observed: dict[str, dict[str, float]], baseline: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one row per (scenario, metric) with baseline/threshold/observed/status.

    ``status`` is one of "no-baseline" (baseline.json is still bootstrap/unset, or this
    scenario has no baseline entry yet), "pass", or "fail". Rows never claim "fail"
    unless a real baseline value exists for that exact metric.
    """
    tolerance = baseline.get("tolerance", DEFAULT_TOLERANCE)
    baseline_scenarios = baseline.get("scenarios", {}) if baseline.get("status") == "set" else {}
    error_rate_max = float(tolerance.get("error_rate_max", DEFAULT_TOLERANCE["error_rate_max"]))

    rows: list[dict[str, Any]] = []
    for scenario, metrics in observed.items():
        baseline_metrics = baseline_scenarios.get(scenario, {})

        for metric_name in ("p95_ms", "p99_ms", "requests_per_s"):
            observed_value = metrics.get(metric_name)
            baseline_value = baseline_metrics.get(metric_name)
            threshold = compute_threshold(metric_name, baseline_value, tolerance)
            rows.append(_row(scenario, metric_name, baseline_value, threshold, observed_value,
                              higher_is_worse=(metric_name != "requests_per_s")))

        error_threshold = error_rate_max if baseline_metrics else None
        rows.append(_row(scenario, "error_rate", baseline_metrics.get("error_rate"), error_threshold,
                          metrics.get("error_rate"), higher_is_worse=True))
    return rows


def _row(scenario, metric_name, baseline_value, threshold, observed_value, *, higher_is_worse) -> dict[str, Any]:
    if threshold is None or observed_value is None:
        status = "no-baseline"
    elif higher_is_worse:
        status = "pass" if observed_value <= threshold else "fail"
    else:
        status = "pass" if observed_value >= threshold else "fail"
    return {
        "scenario": scenario,
        "metric": metric_name,
        "baseline": baseline_value,
        "threshold": threshold,
        "observed": observed_value,
        "status": status,
    }


def _format(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.3f}"


def render_report(rows: list[dict[str, Any]], baseline: dict[str, Any], mode: str) -> str:
    lines = ["## Performance gate report", ""]
    if baseline.get("status") != "set":
        lines += [
            "> ⚠️ **No reviewed baseline yet** (`tests/performance/baseline.json` is still "
            "bootstrap/`unset`). This run is report-only; nothing below can fail the job. "
            "See `docs/agentic-sdlc.md` → Performance and load-testing gate for how a "
            "maintainer promotes a reviewed run to the first real baseline.",
            "",
        ]
    lines.append(f"Mode: `{mode}`")
    lines.append("")
    lines.append("| Scenario | Metric | Baseline | Threshold | Observed | Status |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    icons = {"pass": "✅ pass", "fail": "❌ fail", "no-baseline": "ℹ️ no baseline"}
    for row in rows:
        lines.append(
            f"| {row['scenario']} | {row['metric']} | {_format(row['baseline'])} | "
            f"{_format(row['threshold'])} | {_format(row['observed'])} | {icons[row['status']]} |"
        )
    return "\n".join(lines) + "\n"


def decide_exit_code(rows: list[dict[str, Any]], mode: str) -> int:
    if mode != "strict":
        return 0
    return 1 if any(row["status"] == "fail" for row in rows) else 0


def run_compare(args: argparse.Namespace) -> int:
    summary = load_json(Path(args.summary))
    baseline = load_json(Path(args.baseline))
    observed = extract_metrics(summary)
    if not observed:
        raise GateError("No reads/lifecycle metrics found in the summary; did the k6 run complete?")

    rows = evaluate(observed, baseline)
    report = render_report(rows, baseline, args.mode)

    Path(args.report_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report_out).write_text(report)
    print(report)

    return decide_exit_code(rows, args.mode)


def git_sha() -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                                 timeout=10, check=False)
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def run_capture_baseline(args: argparse.Namespace) -> int:
    summary = load_json(Path(args.from_path))
    observed = extract_metrics(summary)
    if not observed:
        raise GateError("No reads/lifecycle metrics found in the summary; nothing to capture.")

    existing = load_json(Path(args.out)) if Path(args.out).exists() else {}
    tolerance = existing.get("tolerance", DEFAULT_TOLERANCE)

    baseline = {
        "schema_version": 1,
        "status": "set",
        "notes": (
            "Captured by scripts/perf_gate.py capture-baseline from a maintainer-reviewed "
            "run. Not a production SLO — an engineering-headroom reference point for the "
            "local/CI advisory gate. Revise deliberately as the API or its infrastructure "
            "changes; do not treat as an SLA."
        ),
        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_sha": args.git_sha or git_sha(),
        "captured_by": args.captured_by,
        "runner": args.runner,
        "tolerance": tolerance,
        "scenarios": observed,
    }
    Path(args.out).write_text(json.dumps(baseline, indent=2) + "\n")
    print(f"Wrote reviewed baseline to {args.out}. Review the diff before committing.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    compare = subparsers.add_parser("compare", help="Compare a k6 summary against baseline.json")
    compare.add_argument("--summary", default="perf-results/summary.json")
    compare.add_argument("--baseline", default="tests/performance/baseline.json")
    compare.add_argument("--report-out", default="perf-results/gate-report.md")
    compare.add_argument("--mode", choices=["advisory", "strict"], default="advisory")
    compare.set_defaults(func=run_compare)

    capture = subparsers.add_parser(
        "capture-baseline",
        help="Manually turn a reviewed summary.json into a new baseline.json (never run automatically)",
    )
    capture.add_argument("--from", dest="from_path", required=True)
    capture.add_argument("--out", default="tests/performance/baseline.json")
    capture.add_argument("--captured-by", default=None)
    capture.add_argument("--git-sha", default=None)
    capture.add_argument("--runner", default="github-actions ubuntu-latest")
    capture.set_defaults(func=run_capture_baseline)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except GateError as error:
        print(f"perf_gate error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
