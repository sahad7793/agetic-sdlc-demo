import importlib.util
from pathlib import Path
import unittest


SCRIPT = (Path(__file__).resolve().parents[2]
          / "scripts" / "issue_link_check.py")
ROOT = SCRIPT.parents[1]
SPEC = importlib.util.spec_from_file_location("issue_link_check", SCRIPT)
issue_link_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(issue_link_check)


class PullRequestBodyTests(unittest.TestCase):
    def test_accepts_issue_closing_and_reference_keywords(self):
        for text in ("Fixes #12", "Closes #123", "Refs #1234", "Resolves: #9"):
            with self.subTest(text=text):
                self.assertIsNone(issue_link_check.validate_pull_request_body(text, "author"))

    def test_accepts_full_github_issue_url_but_not_pull_request_url(self):
        self.assertIsNone(issue_link_check.validate_pull_request_body(
            "Tracking: https://github.com/org/repo/issues/123", "author"))
        self.assertIsNotNone(issue_link_check.validate_pull_request_body(
            "https://github.com/org/repo/issues/123bad", "author"))
        self.assertIsNotNone(issue_link_check.validate_pull_request_body(
            "Tracking: https://github.com/org/repo/pull/123", "author"))

    def test_requires_a_reference_or_nonblank_no_issue_reason(self):
        for body in ("", "This change has no issue.", "No-Issue:", "No-Issue:   ",
                     "No-Issue: <reason>"):
            with self.subTest(body=body):
                self.assertIsNotNone(issue_link_check.validate_pull_request_body(body, "author"))
        self.assertIsNone(issue_link_check.validate_pull_request_body(
            "No-Issue: documentation-only correction", "author"))

    def test_no_issue_in_template_comment_does_not_exempt(self):
        body = "<!-- Add No-Issue: a brief reason if needed. -->\nDescription"
        self.assertIsNotNone(issue_link_check.validate_pull_request_body(body, "author"))
        template = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
        self.assertIsNotNone(issue_link_check.validate_pull_request_body(template, "author"))

    def test_dependabot_is_explicitly_exempt(self):
        self.assertIsNone(issue_link_check.validate_pull_request_body("", "dependabot[bot]"))
        self.assertIsNone(issue_link_check.validate_pull_request_body("", "Dependabot[Bot]"))


class WorkflowContractTests(unittest.TestCase):
    def test_workflow_is_read_only_and_checks_trusted_base_content(self):
        workflow = (ROOT / ".github" / "workflows" / "issue-link-check.yml").read_text(
            encoding="utf-8")
        self.assertIn("types: [opened, edited, synchronize, reopened]", workflow)
        self.assertIn("permissions:\n  contents: read\n  pull-requests: read", workflow)
        self.assertIn("ref: ${{ github.event.pull_request.base.sha }}", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("run: python3 scripts/issue_link_check.py", workflow)
        self.assertIn("if: hashFiles('scripts/issue_link_check.py') != ''", workflow)
        self.assertIn("Report bootstrap-only skip", workflow)
        self.assertIn("no PR content was executed", workflow)
        self.assertNotIn("pull_request_target", workflow)
        self.assertNotIn("github.event.pull_request.head.sha", workflow)


if __name__ == "__main__":
    unittest.main()
