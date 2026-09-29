#!/usr/bin/env python3
"""Build a bounded advisory retrospective from an SDLC metrics report."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
import sys


REPORT_SCHEMA_VERSION = 3
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 16 * 1024
MINIMUM_MERGED_PRS = 5
FAILURE_STATES = ("action_required", "failure", "startup_failure", "timed_out")
METRIC_LIMIT = 5
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


class RetrospectiveError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise RetrospectiveError(message)


def _date(value):
    _require(isinstance(value, str), "Expected an ISO-8601 timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RetrospectiveError("Invalid ISO-8601 timestamp") from error
    _require(result.tzinfo is not None, "Timestamps must include a timezone")
    return result.astimezone(timezone.utc)


def _iso(value):
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _count(value, field):
    _require(type(value) is int and value >= 0, f"Invalid count: {field}")
    return value


def _number(value, field):
    _require(
        type(value) in (int, float) and math.isfinite(value),
        f"Invalid numeric value: {field}",
    )
    return float(value)


def _window(report, name):
    period = report.get(name)
    _require(isinstance(period, dict), f"Missing {name} reporting window")
    start, end = _date(period.get("start")), _date(period.get("end"))
    _require(end - start == timedelta(days=7), f"{name} is not a seven-day window")
    _require(
        type(period.get("partial_repository_history")) is bool,
        f"Invalid partial-history marker for {name}",
    )
    _number(period.get("observed_hours"), f"{name}.observed_hours")
    return period, start, end


def _metric(name, current, previous, direction):
    current_value, current_samples = current
    previous_value, previous_samples = previous
    if current_value is None or previous_value is None:
        return None
    if min(current_samples, previous_samples) < 1:
        return None
    delta = round(current_value - previous_value, 3)
    change = "unchanged" if delta == 0 else (
        "worsened" if (delta < 0 if direction == "higher" else delta > 0) else "improved"
    )
    return {
        "name": name,
        "current": round(current_value, 3),
        "previous": round(previous_value, 3),
        "delta": delta,
        "current_samples": current_samples,
        "previous_samples": previous_samples,
        "change": change,
    }


def _sample(metric, value_key="median_hours"):
    if not isinstance(metric, dict):
        return None, 0
    count = metric.get("samples")
    if type(count) is not int or count < 0:
        return None, 0
    value = metric.get(value_key)
    if value is None:
        return None, count
    return _number(value, "median_hours"), count


def _summary_counts(period):
    workflows = period.get("workflows")
    deployments = period.get("deployments")
    _require(isinstance(workflows, dict), "Missing workflow metrics")
    _require(isinstance(deployments, dict), "Missing deployment metrics")
    ci = workflows.get("ci")
    _require(isinstance(ci, dict), "Missing CI metrics")
    ci_pr = ci.get("by_trigger", {}).get("pull_request")
    _require(isinstance(ci_pr, dict), "Missing pull-request CI metrics")
    ci_counts = ci_pr.get("counts")
    _require(isinstance(ci_counts, dict), "Missing pull-request CI outcome counts")
    counts = {
        "merged_prs": _count(period.get("merged_prs"), "merged_prs"),
        "ci_pull_request_attempts": _count(ci_pr.get("total"), "ci_pull_request_attempts"),
        "ci_pull_request_decisive": _count(ci_pr.get("decisive"), "ci_pull_request_decisive"),
        "ci_pull_request_successes": _count(ci_pr.get("successes"), "ci_pull_request_successes"),
        "staging_jobs": _count(deployments.get("staging", {}).get("total"), "staging_jobs"),
        "production_jobs": _count(deployments.get("production", {}).get("total"), "production_jobs"),
    }
    counts["ci_pull_request_failures"] = sum(
        _count(ci_counts.get(state, 0), f"ci_pull_request.{state}") for state in FAILURE_STATES
    )
    _require(
        counts["ci_pull_request_successes"] <= counts["ci_pull_request_decisive"],
        "CI successes exceed decisive attempts",
    )
    return counts


def analyze(report):
    """Return only allowlisted aggregates; never copy arbitrary source strings."""
    _require(isinstance(report, dict), "Expected a metrics report object")
    _require(report.get("schema_version") == REPORT_SCHEMA_VERSION, "Unsupported metrics schema")
    repository = report.get("repository")
    _require(
        isinstance(repository, str) and REPOSITORY_PATTERN.fullmatch(repository),
        "Invalid repository identifier",
    )
    current, current_start, current_end = _window(report, "current")
    previous, previous_start, previous_end = _window(report, "previous")
    _require(previous_end == current_start, "Reporting windows are not contiguous")
    output = {
        "schema_version": 1,
        "repository": repository,
        "status": "insufficient_data",
        "minimum_merged_prs": MINIMUM_MERGED_PRS,
        "window": {
            "current": {"start": _iso(current_start), "end": _iso(current_end)},
            "previous": {"start": _iso(previous_start), "end": _iso(previous_end)},
        },
        "population": "main-target pull requests and workflow attempts in the source windows",
        "counts": {"current": _summary_counts(current), "previous": _summary_counts(previous)},
        "comparisons": [],
        "missing_data": [],
        "signals": [],
        "limitations": [
            "Descriptive comparisons do not establish that agents or workflow changes caused outcomes.",
            "The source report reflects collection-time observations and retained GitHub history.",
            "Do not use these aggregate measures to assess individual productivity.",
        ],
    }
    incomplete_windows = [
        label for label, period in (("current", current), ("previous", previous))
        if period.get("partial_repository_history") is not False
        or period.get("observed_hours") != 168
    ]
    if incomplete_windows:
        output["missing_data"].append(
            "Incomplete repository history or reporting window: "
            + ", ".join(incomplete_windows) + "."
        )
        return output
    if current["merged_prs"] < MINIMUM_MERGED_PRS:
        output["missing_data"].append(
            f"Current window has fewer than {MINIMUM_MERGED_PRS} merged PRs; "
            "no recommendations are eligible."
        )
        return output

    if current_start.weekday() != 0 or previous_start.weekday() != 0:
        output["missing_data"].append("Source windows are not aligned to complete UTC weeks.")
        return output
    if previous["merged_prs"] == 0:
        output["missing_data"].append(
            "Previous comparison window has no merged PRs; trend comparisons are unavailable."
        )
        return output

    comparison_results = [
        ("PR cycle", _metric(
            "median_pr_cycle_hours",
            _sample(current.get("pr_cycle")),
            _sample(previous.get("pr_cycle")),
            "lower",
        )),
    ]
    current_ci = current["workflows"]["ci"]["by_trigger"]["pull_request"]
    previous_ci = previous["workflows"]["ci"]["by_trigger"]["pull_request"]
    for item, ci in (("current", current_ci), ("previous", previous_ci)):
        decisive = _count(ci.get("decisive"), f"{item}.ci.decisive")
        successes = _count(ci.get("successes"), f"{item}.ci.successes")
        _require(successes <= decisive, f"{item} CI successes exceed decisive attempts")
    ci_metric = _metric(
        "pull_request_ci_success_percent",
        (
            round(100 * current_ci["successes"] / current_ci["decisive"], 3)
            if current_ci["decisive"] else None,
            current_ci["decisive"],
        ),
        (
            round(100 * previous_ci["successes"] / previous_ci["decisive"], 3)
            if previous_ci["decisive"] else None,
            previous_ci["decisive"],
        ),
        "higher",
    )
    comparison_results.append(("pull-request CI", ci_metric))

    def coverage(period):
        denominator = _count(period.get("link_coverage_denominator"), "link_coverage_denominator")
        numerator = _count(period.get("linked_merged_prs"), "linked_merged_prs")
        _require(numerator <= denominator, "Linked PRs exceed the coverage denominator")
        return (round(100 * numerator / denominator, 3) if denominator else None, denominator)

    comparison_results.append(("issue-link coverage", _metric(
        "issue_link_coverage_percent",
        coverage(current),
        coverage(previous),
        "higher",
    )))

    rework_metric = None
    for period, label in ((current, "current"), (previous, "previous")):
        rework = period.get("rework")
        if not isinstance(rework, dict) or rework.get("available") is not True:
            output["missing_data"].append(f"{label} rework metrics are unavailable.")
    if (
        isinstance(current.get("rework"), dict)
        and current["rework"].get("available") is True
        and isinstance(previous.get("rework"), dict)
        and previous["rework"].get("available") is True
    ):
        rework_metric = _metric(
            "median_changes_requested_reviews",
            _sample(current["rework"].get("changes_requested_reviews"), "median"),
            _sample(previous["rework"].get("changes_requested_reviews"), "median"),
            "lower",
        )
    comparison_results.append(("rework", rework_metric))

    review_values = []
    for period, label in ((current, "current"), (previous, "previous")):
        effort = period.get("review_effort")
        if not isinstance(effort, dict) or effort.get("available") is not True:
            output["missing_data"].append(f"{label} human-review metrics are unavailable.")
            review_values.append(None)
            continue
        metric = effort.get("first_human_review_hours")
        review_values.append(_sample(metric))
    review_metric = None
    if all(value is not None for value in review_values):
        review_metric = _metric(
            "median_first_human_review_hours",
            review_values[0],
            review_values[1],
            "lower",
        )
    comparison_results.append(("human-review latency", review_metric))

    output["comparisons"] = [
        item for _, item in comparison_results if item is not None
    ][:METRIC_LIMIT]
    omitted = [label for label, item in comparison_results if item is None]
    if omitted:
        output["missing_data"].append("Unavailable comparisons: " + ", ".join(omitted[:METRIC_LIMIT]) + ".")
    if not output["comparisons"]:
        output["status"] = "insufficient_data"
        return output

    output["signals"] = [
        {"metric": item["name"], "direction": item["change"]}
        for item in output["comparisons"] if item["change"] != "unchanged"
    ]
    output["status"] = "eligible" if output["signals"] else "no_change"
    return output


def render_markdown(result, fixture=False):
    """Render a compact, data-only prompt brief with hard output bounds."""
    lines = [
        "### Advisory SDLC retrospective",
        f"- Repository: `{result['repository']}`",
        f"- Evidence windows: current [{result['window']['current']['start']}, "
        f"{result['window']['current']['end']}); previous "
        f"[{result['window']['previous']['start']}, {result['window']['previous']['end']})",
        f"- Status: **{result['status']}**",
        f"- Minimum merged PRs in current window: {result['minimum_merged_prs']}",
        f"- Fixture data: {'yes; not repository evidence' if fixture else 'no'}",
        "",
        "| Comparison | Previous | Current | Samples (previous/current) | Descriptive change |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for item in result["comparisons"]:
        lines.append(
            f"| {item['name']} | {item['previous']} | {item['current']} | "
            f"{item['previous_samples']}/{item['current_samples']} | {item['change']} |"
        )
    if not result["comparisons"]:
        lines.append("| None | N/A | N/A | 0/0 | insufficient data |")
    lines.extend(["", "### Activity counts", ""])
    for label, period in (("Previous", "previous"), ("Current", "current")):
        counts = result["counts"][period]
        lines.append(
            f"- {label}: {counts['merged_prs']} merged PRs; "
            f"{counts['ci_pull_request_attempts']} pull-request CI attempts "
            f"({counts['ci_pull_request_successes']} successes, "
            f"{counts['ci_pull_request_failures']} failure/action-required outcomes); "
            f"{counts['staging_jobs']} staging and {counts['production_jobs']} production jobs."
        )
    lines.extend(["", "### Missing data and caveats", ""])
    lines.extend(f"- {item}" for item in result["missing_data"])
    lines.extend(f"- {item}" for item in result["limitations"])
    if not result["missing_data"]:
        lines.append("- No missing metric source was identified in the selected comparisons.")
    text = "\n".join(lines) + "\n"
    _require(len(text.encode("utf-8")) <= MAX_OUTPUT_BYTES, "Retrospective output exceeds size limit")
    return text


def _read_json(path):
    _require(path.stat().st_size <= MAX_INPUT_BYTES, "Input file exceeds size limit")
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture_report():
    def period(start, cycle, ci_successes, linked, review_count, review_hours):
        start_time = _date(start)
        end = (start_time + timedelta(days=7)).isoformat().replace("+00:00", "Z")
        return {
            "start": start,
            "end": end,
            "observed_hours": 168,
            "partial_repository_history": False,
            "merged_prs": 8,
            "pr_cycle": {"samples": 8, "median_hours": cycle},
            "workflows": {"ci": {"by_trigger": {"pull_request": {
                "total": 10,
                "decisive": 10,
                "successes": ci_successes,
                "counts": {"success": ci_successes, "failure": 10 - ci_successes},
            }}}},
            "deployments": {"staging": {"total": 2}, "production": {"total": 1}},
            "linked_merged_prs": linked,
            "link_coverage_denominator": 8,
            "rework": {"available": True, "changes_requested_reviews": {
                "samples": 8, "median": 1,
            }},
            "review_effort": {"available": True, "first_human_review_hours": {
                "samples": review_count, "median_hours": review_hours,
            }},
        }

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "repository": "fixture/example",
        "current": period("2026-09-14T00:00:00Z", 12, 6, 2, 7, 4),
        "previous": period("2026-09-07T00:00:00Z", 10, 8, 3, 7, 2),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        _require(args.fixture or args.report is not None, "--report is required outside fixture mode")
        source = _fixture_report() if args.fixture else _read_json(args.report)
        result = analyze(source)
        result["fixture_data"] = args.fixture
        rendered = render_markdown(result, fixture=args.fixture)
        payload = json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
        _require(len(payload.encode("utf-8")) <= MAX_OUTPUT_BYTES, "Retrospective JSON exceeds size limit")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        summary = Path(str(args.output) + ".md")
        summary.write_text(rendered, encoding="utf-8")
        print(f"Retrospective status: {result['status']}; report written to {args.output}")
    except (OSError, json.JSONDecodeError, RetrospectiveError) as error:
        print(f"SDLC retrospective failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
