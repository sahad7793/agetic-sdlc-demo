import importlib.util
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location(
    "dependency_governance_report",
    Path(__file__).resolve().parents[2] / "scripts" / "dependency_governance_report.py",
)
governance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(governance)

NOW = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)
NOW_ISO = governance.iso(NOW)


def fake_dependabot_alert(number=1, state="open", severity="high", created_at=None, package="lodash"):
    return {
        "number": number,
        "state": state,
        "created_at": created_at or NOW_ISO,
        "html_url": f"https://github.com/sahad7793/agetic-sdlc-demo/security/dependabot/{number}",
        "security_advisory": {"severity": severity},
        "dependency": {
            "package": {"name": package, "ecosystem": "npm"},
            "scope": "runtime",
        },
    }


def fake_codeql_alert(number=100, state="open", severity="medium", created_at=None, rule="js/sql-injection"):
    return {
        "number": number,
        "state": state,
        "created_at": created_at or NOW_ISO,
        "html_url": f"https://github.com/sahad7793/agetic-sdlc-demo/security/code-scanning/{number}",
        "rule": {"id": rule, "security_severity_level": severity},
    }


def fake_dependabot_pr(number=1, title="Bump lodash", created_at=None, draft=False):
    return {
        "number": number,
        "title": title,
        "created_at": created_at or NOW_ISO,
        "html_url": f"https://github.com/sahad7793/agetic-sdlc-demo/pull/{number}",
        "draft": draft,
        "user": {"login": "dependabot[bot]"},
    }


class FakeApiError(Exception):
    def __init__(self, status):
        self.status = status


class GitHubClientTests(unittest.TestCase):
    def test_pages_returns_single_page(self):
        api = governance.GitHub()
        data = [{"id": 1}, {"id": 2}]
        with patch.object(api, "request", return_value=(data, {})):
            result = api.pages("repos/owner/repo/issues")
            self.assertEqual(result, data)

    def test_pages_follows_next_link(self):
        api = governance.GitHub()
        with patch.object(api, "request") as mock_request:
            mock_request.side_effect = [
                ([{"id": 1}], {"Link": '<https://api.github.com/repos/owner/repo/issues?page=2>; rel="next"'}),
                ([{"id": 2}], {}),
            ]
            result = api.pages("repos/owner/repo/issues")
            self.assertEqual(result, [{"id": 1}, {"id": 2}])
            self.assertEqual(mock_request.call_count, 2)

    def test_pages_handles_empty_response(self):
        api = governance.GitHub()
        with patch.object(api, "request", return_value=([], {})):
            result = api.pages("repos/owner/repo/issues")
            self.assertEqual(result, [])

    def test_request_raises_api_error_on_403(self):
        api = governance.GitHub()
        api.token = "test-token"
        with patch("urllib.request.urlopen") as mock_urlopen:
            error = __import__("urllib.error").error.HTTPError("", 403, "", {}, None)
            mock_urlopen.side_effect = error
            with self.assertRaises(governance.ApiError) as ctx:
                api.request("repos/owner/repo/issues")
            self.assertEqual(ctx.exception.status, 403)

    def test_request_raises_api_error_on_404(self):
        api = governance.GitHub()
        api.token = "test-token"
        with patch("urllib.request.urlopen") as mock_urlopen:
            error = __import__("urllib.error").error.HTTPError("", 404, "", {}, None)
            mock_urlopen.side_effect = error
            with self.assertRaises(governance.ApiError) as ctx:
                api.request("repos/owner/repo/issues")
            self.assertEqual(ctx.exception.status, 404)


class SeverityBucketingTests(unittest.TestCase):
    def test_dependabot_severity_extracts_from_security_advisory(self):
        alert = fake_dependabot_alert(severity="critical")
        self.assertEqual(governance.dependabot_severity(alert), "critical")

    def test_dependabot_severity_defaults_to_unknown_when_missing(self):
        alert = fake_dependabot_alert()
        alert["security_advisory"] = {}
        self.assertEqual(governance.dependabot_severity(alert), "unknown")

    def test_dependabot_severity_normalizes_case(self):
        alert = fake_dependabot_alert(severity="HIGH")
        self.assertEqual(governance.dependabot_severity(alert), "high")

    def test_codeql_severity_extracts_from_rule(self):
        alert = fake_codeql_alert(severity="high")
        self.assertEqual(governance.codeql_severity(alert), "high")

    def test_codeql_severity_defaults_to_unknown_when_missing(self):
        alert = fake_codeql_alert()
        alert["rule"] = {}
        self.assertEqual(governance.codeql_severity(alert), "unknown")

    def test_codeql_severity_invalid_value_becomes_unknown(self):
        alert = fake_codeql_alert(severity="invalid")
        self.assertEqual(governance.codeql_severity(alert), "unknown")


class AlertSourceCollectionTests(unittest.TestCase):
    def test_collect_alert_source_success(self):
        api = Mock()
        api.pages.return_value = [fake_dependabot_alert(number=1, severity="high")]
        result = governance.collect_alert_source(api, "repos/owner/repo", "dependabot",
                                                  "dependabot/alerts?state=open")
        self.assertTrue(result["available"])
        self.assertIsNone(result["reason"])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["package"], "lodash")

    def test_collect_alert_source_403_marked_unavailable(self):
        api = Mock()
        api.pages.side_effect = governance.ApiError(403, "endpoint")
        result = governance.collect_alert_source(api, "repos/owner/repo", "dependabot",
                                                  "dependabot/alerts?state=open")
        self.assertFalse(result["available"])
        self.assertIn("403", result["reason"])
        self.assertEqual(result["items"], [])

    def test_collect_alert_source_404_marked_unavailable(self):
        api = Mock()
        api.pages.side_effect = governance.ApiError(404, "endpoint")
        result = governance.collect_alert_source(api, "repos/owner/repo", "dependabot",
                                                  "dependabot/alerts?state=open")
        self.assertFalse(result["available"])
        self.assertIn("404", result["reason"])
        self.assertEqual(result["items"], [])

    def test_collect_alert_source_other_error_raised(self):
        api = Mock()
        api.pages.side_effect = governance.ApiError(500, "endpoint")
        with self.assertRaises(governance.ApiError):
            governance.collect_alert_source(api, "repos/owner/repo", "dependabot",
                                            "dependabot/alerts?state=open")

    def test_collect_alert_source_codeql(self):
        api = Mock()
        api.pages.return_value = [fake_codeql_alert(number=100, severity="high", rule="js/sql-injection")]
        result = governance.collect_alert_source(api, "repos/owner/repo", "codeql",
                                                  "code-scanning/alerts?state=open&tool_name=CodeQL")
        self.assertTrue(result["available"])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["rule"], "js/sql-injection")


class DependabotPrCollectionTests(unittest.TestCase):
    def test_collect_dependabot_prs_filters_by_author(self):
        api = Mock()
        human_pr = fake_dependabot_pr()
        human_pr["user"]["login"] = "developer@example.com"
        api.pages.return_value = [human_pr, fake_dependabot_pr(number=2)]
        result = governance.collect_dependabot_pull_requests(api, "repos/owner/repo")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["number"], 2)

    def test_collect_dependabot_prs_includes_draft_flag(self):
        api = Mock()
        api.pages.return_value = [fake_dependabot_pr(draft=True)]
        result = governance.collect_dependabot_pull_requests(api, "repos/owner/repo")
        self.assertTrue(result[0]["draft"])

    def test_collect_dependabot_prs_empty_when_no_bots(self):
        api = Mock()
        api.pages.return_value = []
        result = governance.collect_dependabot_pull_requests(api, "repos/owner/repo")
        self.assertEqual(result, [])


class AlertSummaryTests(unittest.TestCase):
    def test_alert_summary_counts_by_severity(self):
        source = {
            "available": True,
            "reason": None,
            "items": [
                {
                    "number": 1,
                    "state": "open",
                    "severity": "critical",
                    "created_at": NOW_ISO,
                    "html_url": "https://example.com/1",
                },
                {
                    "number": 2,
                    "state": "open",
                    "severity": "high",
                    "created_at": NOW_ISO,
                    "html_url": "https://example.com/2",
                },
                {
                    "number": 3,
                    "state": "open",
                    "severity": "high",
                    "created_at": NOW_ISO,
                    "html_url": "https://example.com/3",
                },
            ],
        }
        result = governance.alert_summary(source, NOW)
        self.assertEqual(result["by_severity"]["critical"], 1)
        self.assertEqual(result["by_severity"]["high"], 2)
        self.assertEqual(result["total_open"], 3)

    def test_alert_summary_computes_oldest_days(self):
        old_time = NOW - timedelta(days=30)
        old_iso = governance.iso(old_time)
        source = {
            "available": True,
            "reason": None,
            "items": [
                {"state": "open", "severity": "high", "created_at": old_iso},
                {"state": "open", "severity": "low", "created_at": NOW_ISO},
            ],
        }
        result = governance.alert_summary(source, NOW)
        self.assertEqual(result["oldest_open_days"], 30)

    def test_alert_summary_ignores_closed_alerts(self):
        source = {
            "available": True,
            "reason": None,
            "items": [
                {"state": "open", "severity": "high", "created_at": NOW_ISO},
                {"state": "dismissed", "severity": "critical", "created_at": NOW_ISO},
            ],
        }
        result = governance.alert_summary(source, NOW)
        self.assertEqual(result["total_open"], 1)

    def test_alert_summary_unavailable_source(self):
        source = {
            "available": False,
            "reason": "HTTP 403: denied",
            "items": [],
        }
        result = governance.alert_summary(source, NOW)
        self.assertFalse(result["available"])
        self.assertEqual(result["total_open"], 0)


class TopAlertsTests(unittest.TestCase):
    def test_top_alerts_sorts_by_severity_then_age(self):
        evidence = {
            "alerts": {
                "dependabot": {
                    "available": True,
                    "items": [
                        {
                            "number": 1,
                            "state": "open",
                            "severity": "low",
                            "created_at": NOW_ISO,
                            "html_url": "https://example.com/1",
                        },
                        {
                            "number": 2,
                            "state": "open",
                            "severity": "critical",
                            "created_at": governance.iso(NOW - timedelta(days=5)),
                            "html_url": "https://example.com/2",
                        },
                        {
                            "number": 3,
                            "state": "open",
                            "severity": "high",
                            "created_at": governance.iso(NOW - timedelta(days=10)),
                            "html_url": "https://example.com/3",
                        },
                    ],
                },
                "codeql": {"available": False, "items": []},
            }
        }
        result = governance.top_alerts(evidence, NOW)
        # Should be: critical first (by severity), then high (older), then low (newer)
        self.assertEqual(result[0]["severity"], "critical")
        self.assertEqual(result[1]["severity"], "high")
        self.assertEqual(result[2]["severity"], "low")

    def test_top_alerts_respects_limit(self):
        items = [
            {
                "number": i,
                "state": "open",
                "severity": "high",
                "created_at": NOW_ISO,
                "html_url": f"https://example.com/{i}",
            }
            for i in range(20)
        ]
        evidence = {
            "alerts": {
                "dependabot": {"available": True, "items": items},
                "codeql": {"available": False, "items": []},
            }
        }
        result = governance.top_alerts(evidence, NOW, limit=5)
        self.assertEqual(len(result), 5)

    def test_top_alerts_skips_unavailable_sources(self):
        evidence = {
            "alerts": {
                "dependabot": {
                    "available": False,
                    "items": [
                        {
                            "number": 1,
                            "state": "open",
                            "severity": "critical",
                            "created_at": NOW_ISO,
                            "html_url": "https://example.com/1",
                        }
                    ],
                },
                "codeql": {
                    "available": True,
                    "items": [
                        {
                            "number": 100,
                            "state": "open",
                            "severity": "high",
                            "created_at": NOW_ISO,
                            "html_url": "https://example.com/100",
                        }
                    ],
                },
            }
        }
        result = governance.top_alerts(evidence, NOW)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["kind"], "codeql")


class BuildReportTests(unittest.TestCase):
    def test_build_report_aggregates_all_data(self):
        evidence = {
            "schema_version": 1,
            "repository": "owner/repo",
            "collected_at": NOW_ISO,
            "alerts": {
                "dependabot": {
                    "available": True,
                    "reason": None,
                    "items": [
                        {
                            "number": 1,
                            "state": "open",
                            "severity": "high",
                            "created_at": NOW_ISO,
                            "html_url": "https://example.com/1",
                        }
                    ],
                },
                "codeql": {
                    "available": True,
                    "reason": None,
                    "items": [
                        {
                            "number": 100,
                            "state": "open",
                            "severity": "low",
                            "created_at": NOW_ISO,
                            "html_url": "https://example.com/100",
                        }
                    ],
                },
            },
            "dependabot_pull_requests": [fake_dependabot_pr(number=10)],
        }
        report = governance.build_report(evidence)
        self.assertEqual(report["repository"], "owner/repo")
        self.assertIn("dependabot", report["alerts"])
        self.assertIn("codeql", report["alerts"])
        self.assertEqual(report["dependabot_pull_requests"]["open_count"], 1)
        self.assertIsNotNone(report["top_alerts"])

    def test_build_report_no_prs(self):
        evidence = {
            "schema_version": 1,
            "repository": "owner/repo",
            "collected_at": NOW_ISO,
            "alerts": {
                "dependabot": {"available": True, "reason": None, "items": []},
                "codeql": {"available": True, "reason": None, "items": []},
            },
            "dependabot_pull_requests": [],
        }
        report = governance.build_report(evidence)
        self.assertEqual(report["dependabot_pull_requests"]["open_count"], 0)
        self.assertIsNone(report["dependabot_pull_requests"]["oldest_open_days"])


class RenderTests(unittest.TestCase):
    def test_render_includes_read_only_disclaimer(self):
        report = {
            "repository": "owner/repo",
            "collected_at": NOW_ISO,
            "alerts": {
                "dependabot": {
                    "available": True,
                    "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0},
                    "total_open": 0,
                    "oldest_open_days": None,
                },
                "codeql": {
                    "available": True,
                    "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0},
                    "total_open": 0,
                    "oldest_open_days": None,
                },
            },
            "top_alerts": [],
            "dependabot_pull_requests": {"open_count": 0, "oldest_open_days": None, "items": []},
        }
        markdown = governance.render(report)
        self.assertIn("read-only", markdown.lower())
        self.assertIn("auto-merge", markdown.lower())

    def test_render_reports_unavailable_source(self):
        report = {
            "repository": "owner/repo",
            "collected_at": NOW_ISO,
            "alerts": {
                "dependabot": {
                    "available": False,
                    "reason": "HTTP 403: denied",
                    "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0},
                    "total_open": 0,
                    "oldest_open_days": None,
                },
                "codeql": {
                    "available": True,
                    "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0},
                    "total_open": 0,
                    "oldest_open_days": None,
                },
            },
            "top_alerts": [],
            "dependabot_pull_requests": {"open_count": 0, "oldest_open_days": None, "items": []},
        }
        markdown = governance.render(report)
        self.assertIn("dependabot", markdown.lower())
        self.assertIn("403", markdown)

    def test_render_includes_top_alerts_table(self):
        report = {
            "repository": "owner/repo",
            "collected_at": NOW_ISO,
            "alerts": {
                "dependabot": {
                    "available": True,
                    "by_severity": {"critical": 1, "high": 0, "medium": 0, "low": 0, "unknown": 0},
                    "total_open": 1,
                    "oldest_open_days": 5,
                },
                "codeql": {
                    "available": True,
                    "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0},
                    "total_open": 0,
                    "oldest_open_days": None,
                },
            },
            "top_alerts": [
                {
                    "kind": "dependabot",
                    "number": 1,
                    "severity": "critical",
                    "created_at": NOW_ISO,
                    "package": "lodash",
                    "html_url": "https://example.com/alert/1",
                },
            ],
            "dependabot_pull_requests": {"open_count": 0, "oldest_open_days": None, "items": []},
        }
        markdown = governance.render(report)
        self.assertIn("lodash", markdown)
        self.assertIn("critical", markdown)

    def test_render_pr_table_with_items(self):
        report = {
            "repository": "owner/repo",
            "collected_at": NOW_ISO,
            "alerts": {
                "dependabot": {
                    "available": True,
                    "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0},
                    "total_open": 0,
                    "oldest_open_days": None,
                },
                "codeql": {
                    "available": True,
                    "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0},
                    "total_open": 0,
                    "oldest_open_days": None,
                },
            },
            "top_alerts": [],
            "dependabot_pull_requests": {
                "open_count": 1,
                "oldest_open_days": 3,
                "items": [fake_dependabot_pr(number=42, title="Bump lodash", draft=False)],
            },
        }
        markdown = governance.render(report)
        self.assertIn("| 42 |", markdown)
        self.assertIn("Bump lodash", markdown)

    def test_render_pr_draft_flag_shown(self):
        report = {
            "repository": "owner/repo",
            "collected_at": NOW_ISO,
            "alerts": {
                "dependabot": {
                    "available": True,
                    "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0},
                    "total_open": 0,
                    "oldest_open_days": None,
                },
                "codeql": {
                    "available": True,
                    "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0},
                    "total_open": 0,
                    "oldest_open_days": None,
                },
            },
            "top_alerts": [],
            "dependabot_pull_requests": {
                "open_count": 1,
                "oldest_open_days": 1,
                "items": [fake_dependabot_pr(number=5, title="Test", draft=True)],
            },
        }
        markdown = governance.render(report)
        self.assertIn("(draft)", markdown)


if __name__ == "__main__":
    unittest.main()
