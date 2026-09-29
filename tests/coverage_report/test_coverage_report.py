import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import coverage_report


class CoverageReportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def write_coverage(self, path, lines_covered, lines_valid, branches_covered, branches_valid):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '<coverage lines-covered="{}" lines-valid="{}" branches-covered="{}" branches-valid="{}" />'.format(
                lines_covered, lines_valid, branches_covered, branches_valid
            ),
            encoding="utf-8",
        )

    def test_collect_coverage_aggregates_counts_before_calculating_percentages(self):
        self.write_coverage(self.root / "one/coverage.cobertura.xml", 3, 4, 1, 2)
        self.write_coverage(self.root / "two/coverage.cobertura.xml", 1, 6, 0, 2)

        report = coverage_report.render_report(coverage_report.collect_coverage(self.root))

        self.assertIn("| Lines | 4 | 10 | 40.0% |", report)
        self.assertIn("| Branches | 1 | 4 | 25.0% |", report)
        self.assertIn("Advisory only: no pass/fail threshold is applied.", report)

    def test_render_report_marks_branch_coverage_unavailable_without_branches(self):
        report = coverage_report.render_report((4, 5, 0, 0))

        self.assertIn("| Branches | 0 | 0 | N/A |", report)

    def test_missing_coverage_report_fails_clearly(self):
        with self.assertRaisesRegex(FileNotFoundError, "No coverage.cobertura.xml"):
            coverage_report.collect_coverage(self.root)

    def test_invalid_coverage_counts_are_rejected(self):
        report_path = self.root / "coverage.cobertura.xml"
        self.write_coverage(report_path, 5, 4, 0, 0)

        with self.assertRaisesRegex(ValueError, "inconsistent coverage counts"):
            coverage_report.read_coverage(report_path)

    def test_write_report_publishes_to_report_file_and_step_summary(self):
        self.write_coverage(self.root / "coverage.cobertura.xml", 4, 5, 1, 2)
        report_path = self.root / "output/coverage-summary.md"
        summary_path = self.root / "step-summary.md"
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary_path)}):
            coverage_report.write_report(self.root, report_path)

        self.assertEqual(report_path.read_text(encoding="utf-8"), summary_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
