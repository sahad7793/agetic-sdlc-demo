"""Summarize Coverlet Cobertura reports for local use and GitHub Actions."""

import argparse
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


def read_coverage(path):
    try:
        root = ET.parse(path).getroot()
        if root.tag.rsplit("}", 1)[-1] != "coverage":
            raise ValueError("expected a Cobertura <coverage> root element")
        values = tuple(
            int(root.attrib[name])
            for name in ("lines-covered", "lines-valid", "branches-covered", "branches-valid")
        )
    except (ET.ParseError, KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Invalid Cobertura report {path}: {error}") from error

    lines_covered, lines_valid, branches_covered, branches_valid = values
    if min(values) < 0:
        raise ValueError(f"Invalid Cobertura report {path}: coverage counts cannot be negative")
    if lines_valid == 0 or lines_covered > lines_valid or branches_covered > branches_valid:
        raise ValueError(f"Invalid Cobertura report {path}: inconsistent coverage counts")
    return values


def collect_coverage(results_directory):
    reports = sorted(results_directory.rglob("coverage.cobertura.xml"))
    if not reports:
        raise FileNotFoundError(
            f"No coverage.cobertura.xml reports found under {results_directory}"
        )

    totals = [0, 0, 0, 0]
    for report in reports:
        for index, count in enumerate(read_coverage(report)):
            totals[index] += count
    return tuple(totals)


def render_report(coverage):
    lines_covered, lines_valid, branches_covered, branches_valid = coverage
    line_rate = lines_covered / lines_valid * 100
    branch_rate = (
        f"{branches_covered / branches_valid * 100:.1f}%"
        if branches_valid
        else "N/A"
    )
    return "\n".join(
        [
            "### .NET test coverage",
            "",
            "| Metric | Covered | Valid | Coverage |",
            "| --- | ---: | ---: | ---: |",
            f"| Lines | {lines_covered} | {lines_valid} | {line_rate:.1f}% |",
            f"| Branches | {branches_covered} | {branches_valid} | {branch_rate} |",
            "",
            "Advisory only: no pass/fail threshold is applied.",
            "",
        ]
    )


def write_report(results_directory, report_path):
    report = render_report(collect_coverage(results_directory))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write(report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-directory", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    try:
        write_report(args.results_directory, args.report)
    except (OSError, ValueError) as error:
        print(f"Coverage report failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
