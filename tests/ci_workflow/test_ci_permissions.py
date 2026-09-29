from pathlib import Path
import re
import unittest


WORKFLOW = (Path(__file__).resolve().parents[2]
            / ".github" / "workflows" / "ci.yml")


class CiWorkflowPermissionTests(unittest.TestCase):
    def test_ci_grants_only_required_permissions(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        match = re.search(
            r"(?m)^permissions:\n(?P<body>(?:  [a-z-]+: [a-z]+\n)+)",
            text,
        )
        self.assertIsNotNone(match)
        permissions = dict(re.findall(
            r"(?m)^  ([a-z-]+): ([a-z]+)$", match.group("body")
        ))
        self.assertEqual(
            permissions,
            {
                "actions": "read",
                "contents": "read",
                "security-events": "write",
            },
        )

    def test_codeql_analysis_does_not_upload_results(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r"(?m)^      - name: Analyze CodeQL database\n"
            r"        uses: github/codeql-action/analyze@v4\n"
            r"        with:\n"
            r"          upload: false$",
        )

    def test_coverage_report_runs_after_tests_and_before_artifact_upload(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        test_step = text.index("      - name: Test with coverage")
        report_step = text.index("      - name: Report test coverage")
        upload_step = text.index("      - name: Upload test results")

        self.assertLess(test_step, report_step)
        self.assertLess(report_step, upload_step)
        self.assertIn(
            "run: python3 scripts/coverage_report.py --results-directory TestResults "
            "--report TestResults/coverage-summary.md",
            text[report_step:upload_step],
        )
        self.assertIn("path: TestResults", text[upload_step:])


if __name__ == "__main__":
    unittest.main()
