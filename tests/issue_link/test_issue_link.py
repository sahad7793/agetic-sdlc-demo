import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch


SCRIPT = (Path(__file__).resolve().parents[2]
          / "scripts" / "issue_link_check.py")
ROOT = SCRIPT.parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
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

    def test_shared_parser_returns_visible_local_and_external_references(self):
        references = issue_link_check.extract_issue_references(
            "Closes #12\nRefs #13\nhttps://github.com/other/project/issues/14\n"
            "<!-- Fixes #15 -->",
            default_repository="Org/Repo",
        )
        self.assertEqual(references, [
            ("org/repo", 12),
            ("org/repo", 13),
            ("other/project", 14),
        ])

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

    def test_reports_approved_label_from_issue_metadata(self):
        output = io.StringIO()
        fetch_issue = lambda *_: ("open", {"stage:plan-approved"})

        results = issue_link_check.report_plan_approval_status(
            "Closes #12", "Org/Repo", "token", "https://api.github.com",
            output=output, fetch_issue=fetch_issue)

        self.assertEqual(results, [("org/repo#12", "Approved (open)", True, None)])
        self.assertIn("::notice", output.getvalue())
        self.assertIn("stage:plan-approved", output.getvalue())

    def test_unapproved_issue_warns_but_does_not_change_validation(self):
        output = io.StringIO()
        fetch_issue = lambda *_: ("open", set())

        results = issue_link_check.report_plan_approval_status(
            "Closes #12", "Org/Repo", "token", "https://api.github.com",
            output=output, fetch_issue=fetch_issue)

        self.assertEqual(results[0][2], False)
        self.assertIn("::warning", output.getvalue())
        self.assertIn("does not block the pull request", output.getvalue())
        self.assertIsNone(issue_link_check.validate_pull_request_body("Closes #12", "author"))

    def test_closed_issue_is_approved_only_when_label_is_present(self):
        output = io.StringIO()
        fetch_issue = lambda *_: ("closed", set())

        results = issue_link_check.report_plan_approval_status(
            "Closes #12", "Org/Repo", "token", "https://api.github.com",
            output=output, fetch_issue=fetch_issue)

        self.assertEqual(results[0], ("org/repo#12", "Not approved (closed)", False, None))
        self.assertIn("::warning", output.getvalue())

    def test_reports_each_distinct_linked_issue(self):
        output = io.StringIO()
        fetch_issue = lambda repository, number, *_: (
            "open", {"stage:plan-approved"} if number == 12 else set())

        results = issue_link_check.report_plan_approval_status(
            "Closes #12\nRefs #13\nRefs #12", "Org/Repo", "token",
            "https://api.github.com", output=output, fetch_issue=fetch_issue)

        self.assertEqual([result[0] for result in results],
                         ["org/repo#12", "org/repo#13"])
        self.assertEqual(output.getvalue().count("::notice"), 1)
        self.assertEqual(output.getvalue().count("::warning"), 1)

    def test_pr_body_cannot_supply_approval_label(self):
        output = io.StringIO()
        fetch_issue = lambda *_: ("open", set())

        results = issue_link_check.report_plan_approval_status(
            "Closes #12\nstage:plan-approved", "Org/Repo", "token",
            "https://api.github.com", output=output, fetch_issue=fetch_issue)

        self.assertFalse(results[0][2])
        self.assertIn("::warning", output.getvalue())

    def test_unreadable_issue_is_an_advisory_warning(self):
        output = io.StringIO()

        def fail_to_fetch(*_):
            raise issue_link_check.IssueMetadataError("GitHub returned HTTP 404.")

        results = issue_link_check.report_plan_approval_status(
            "Closes #12", "Org/Repo", "token", "https://api.github.com",
            output=output, fetch_issue=fail_to_fetch)

        self.assertEqual(results[0][1], "Unable to verify")
        self.assertIn("::warning", output.getvalue())
        self.assertIsNone(issue_link_check.validate_pull_request_body("Closes #12", "author"))

    def test_external_issue_lookup_does_not_receive_repository_token(self):
        calls = []

        def fetch_issue(repository, number, token, api_url):
            calls.append((repository, number, token, api_url))
            return "open", set()

        issue_link_check.report_plan_approval_status(
            "Refs https://github.com/other/project/issues/14",
            "Org/Repo", "token", "https://api.github.com",
            output=io.StringIO(), fetch_issue=fetch_issue)

        self.assertEqual(calls, [
            ("other/project", 14, None, "https://api.github.com")
        ])

    def test_issue_metadata_lookup_uses_get_and_read_only_token(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = (
            b'{"state":"open","labels":[{"name":"stage:plan-approved"}]}')

        with patch.object(issue_link_check.urllib.request, "urlopen",
                          return_value=response) as urlopen:
            state, labels = issue_link_check.get_issue_metadata(
                "org/repo", 12, "token", "https://api.github.com")

        request = urlopen.call_args.args[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.get_header("Authorization"), "Bearer token")
        self.assertEqual((state, labels), ("open", {"stage:plan-approved"}))


class WorkflowContractTests(unittest.TestCase):
    def test_workflow_is_read_only_and_checks_trusted_base_content(self):
        workflow = (ROOT / ".github" / "workflows" / "issue-link-check.yml").read_text(
            encoding="utf-8")
        self.assertIn("types: [opened, edited, synchronize, reopened]", workflow)
        self.assertIn(
            "permissions:\n  contents: read\n  issues: read\n  pull-requests: read",
            workflow)
        self.assertIn("ref: ${{ github.event.pull_request.base.sha }}", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("run: python3 scripts/issue_link_check.py", workflow)
        self.assertIn("if: hashFiles('scripts/issue_link_check.py') != ''", workflow)
        self.assertIn("Report bootstrap-only skip", workflow)
        self.assertIn("no PR content was executed", workflow)
        self.assertIn("GITHUB_TOKEN: ${{ github.token }}", workflow)
        self.assertNotIn("issues: write", workflow)
        self.assertNotIn("pull-requests: write", workflow)
        self.assertNotIn("pull_request_target", workflow)
        self.assertNotIn("github.event.pull_request.head.sha", workflow)


if __name__ == "__main__":
    unittest.main()
