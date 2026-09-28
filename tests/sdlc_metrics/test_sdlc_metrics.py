import copy
import io
from pathlib import Path
import sys
import unittest
import urllib.error
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import sdlc_metrics as metrics
REPOSITORY = "example/repo"
START = "2026-09-18T00:00:00Z"
END = "2026-09-25T00:00:00Z"


def evidence():
    return {
        "schema_version": 2, "repository": REPOSITORY,
        "repository_created_at": "2026-09-23T12:00:00Z",
        "collected_at": "2026-09-25T12:00:00Z",
        "collection_finished_at": "2026-09-25T12:01:00Z",
        "collector_commit": "a" * 40,
        "pr_activity_available_from": START, "issues_available_from": START,
        "prs": [], "issues": [], "attempts": [], "deployment_jobs": [],
        "alerts": {
            "codeql": {"available": True, "reason": None, "items": []},
            "dependabot": {"available": False, "reason": "HTTP 403: denied", "items": []},
        },
    }


def pr(number=1, **kwargs):
    return {
        "number": number, "created_at": "2026-09-23T12:00:00Z",
        "merged_at": "2026-09-24T12:00:00Z",
        "state": "MERGED", "base": "main", "author": "maintainer", "author_is_bot": False,
        "title": "Add feature", "is_revert": False, "merged_by": "maintainer",
        "issues": [], "reviews": [], "commits": [], **kwargs,
    }


def issue(number=1, **kwargs):
    return {"number": number, "repository": REPOSITORY,
            "created_at": "2026-09-23T00:00:00Z", **kwargs}


def attempt(**kwargs):
    return {
        "workflow": "ci", "run_id": 10, "attempt": 1, "event": "pull_request",
        "branch": "feature", "started_at": "2026-09-24T12:00:00Z",
        "status": "completed", "conclusion": "success", **kwargs,
    }


def job(**kwargs):
    return {
        "id": 11, "run_id": 10, "attempt": 1, "environment": "staging",
        "started_at": "2026-09-24T12:00:00Z", "completed_at": "2026-09-24T12:05:00Z",
        "status": "completed", "conclusion": "success", **kwargs,
    }


class MetricTests(unittest.TestCase):
    def test_utc_half_open_window_and_timezone_conversion(self):
        self.assertTrue(metrics.within(START, START, END))
        self.assertFalse(metrics.within(END, START, END))
        self.assertFalse(metrics.within(None, START, END))
        self.assertTrue(metrics.within("2026-09-25T00:59:59+01:00", START, END))
        with self.assertRaises(ValueError):
            metrics.timestamp("2026-09-25")

    def test_empty_and_partial_periods_are_explicit(self):
        report = metrics.build_report(evidence(), baseline=True)
        self.assertEqual(report["current"]["observed_hours"], 36)
        self.assertEqual(report["previous"]["observed_hours"], 0)
        self.assertTrue(report["current"]["partial_repository_history"])
        self.assertIsNone(report["current"]["pr_cycle"]["median_hours"])
        self.assertIsNone(report["current"]["workflows"]["ci"]["success_percent"])
        self.assertIsNone(report["initial_baseline"]["deployments"]["staging"]["successful_completions_per_day"])

    def test_cycle_cohorts_exclusions_and_median(self):
        data = evidence()
        data["prs"] = [
            pr(1), pr(2, created_at="2026-09-24T00:00:00Z", author="dependabot"),
            pr(3, base="release"), pr(4, merged_at=None, state="OPEN"),
            pr(5, created_at="2026-09-25T00:00:00Z"),
            pr(6, merged_at=END),
        ]
        result = metrics.period(data, START, END)
        self.assertEqual(result["merged_prs"], 3)
        self.assertEqual(result["pr_cycle"], {"samples": 2, "median_hours": 18})
        self.assertEqual(result["excluded"]["negative_pr_durations"], 1)
        self.assertEqual(result["pr_cycle_by_author"]["dependabot"]["median_hours"], 12)

    def test_agent_rework_medians_and_revert_count(self):
        agent_pr = pr(
            1, author="copilot-swe-agent[bot]", is_revert=True,
            reviews=[
                {"state": "CHANGES_REQUESTED", "submitted_at": "2026-09-23T13:00:00Z",
                 "commit_id": "a", "user": "reviewer"},
                {"state": "APPROVED", "submitted_at": "2026-09-24T00:00:00Z",
                 "commit_id": "c", "user": "approver"},
            ],
            commits=[{"sha": "a"}, {"sha": "b"}, {"sha": "c"}],
        )
        result = metrics.period({**evidence(), "prs": [agent_pr]}, START, END)
        rework = result["rework_by_authorship"]["agent"]
        self.assertEqual(rework["review_rounds"], {"samples": 1, "median": 2})
        self.assertEqual(rework["changes_requested_reviews"], {"samples": 1, "median": 1})
        self.assertEqual(rework["commits_after_first_review"], {"samples": 1, "median": 2})
        self.assertEqual(rework["pr_title_starts_with_revert"], 1)
        self.assertEqual(result["rework_by_authorship"]["human"]["merged_prs"], 0)

    def test_authorship_separates_copilot_humans_unknowns_and_automation(self):
        data = evidence()
        data["prs"] = [
            pr(1, author="copilot-swe-agent[bot]", author_is_bot=True),
            pr(2, author="maintainer", commits=[{"copilot_coauthored": True}]),
            pr(3, author="dependabot[bot]", author_is_bot=True,
               commits=[{"copilot_coauthored": True}]),
            pr(4, author="renovate[bot]", author_is_bot=True),
            pr(5, author="github-actions[bot]", author_is_bot=True),
            pr(6, author="maintainer"),
            pr(7, author=None),
        ]
        groups = metrics.period(data, START, END)["rework_by_authorship"]
        self.assertEqual(groups["agent"]["merged_prs"], 2)
        self.assertEqual(groups["automation"]["merged_prs"], 3)
        self.assertEqual(groups["human"]["merged_prs"], 1)
        self.assertEqual(groups["unknown"]["merged_prs"], 1)
        self.assertEqual(sum(group["merged_prs"] for group in groups.values()), 7)
        report = metrics.build_report(data)
        self.assertIn("Automation PRs: median review rounds", metrics.render(report))

    def test_defect_counts_and_per_pr_authorship_use_explicit_and_heuristic_attribution(self):
        data = evidence()
        first = pr(
            1, merged_at="2026-09-20T00:00:00Z", author="copilot-swe-agent[bot]",
            commits=[{"copilot_coauthored": False}],
        )
        second = pr(
            2, merged_at="2026-09-23T00:00:00Z", author="human",
            issues=[issue(2, created_at="2026-09-23T01:00:00Z")],
        )
        data["prs"] = [first, second]
        data["issues"] = [
            {"number": 1, "created_at": "2026-09-22T00:00:00Z", "labels": ["bug"]},
            {"number": 2, "created_at": "2026-09-23T01:00:00Z", "labels": ["incident", "sev2"]},
            {"number": 3, "created_at": "2026-09-19T00:00:00Z", "labels": ["sev1"]},
        ]
        result = metrics.period(data, START, END)["escaped_defects"]
        self.assertEqual(result["opened"], {"bugs": 1, "incidents": 2, "unique_issues": 3})
        self.assertEqual(
            [(item["number"], item["bugs"], item["incidents"], item["attribution"])
             for item in result["per_merged_pr"]],
            [
                (1, 1, 0, ["heuristic_30_day_temporal_proximity"]),
                (2, 0, 1, ["explicit_closing_issue_reference"]),
            ],
        )
        self.assertEqual(result["by_authorship"]["agent"]["bugs"], 1)
        self.assertEqual(result["by_authorship"]["human"]["incidents"], 1)
        self.assertEqual(result["unattributed_issues"], 1)

    def test_issues_and_rework_outside_retention_are_unavailable_not_zero(self):
        data = evidence()
        data["issues_available_from"] = "2026-09-24T00:00:00Z"
        data["pr_activity_available_from"] = "2026-09-24T00:00:00Z"
        result = metrics.period(data, START, END)
        self.assertFalse(result["escaped_defects"]["available"])
        self.assertIsNone(result["rework_by_authorship"])
        self.assertFalse(result["rework"]["available"])

    def test_synthetic_incidents_are_excluded_from_open_and_attributed_defects(self):
        data = evidence()
        data["prs"] = [pr(1, author="copilot-swe-agent[bot]")]
        data["issues"] = [
            issue(1, labels=["incident", "sev4"],
                  title="[INCIDENT][Sev4][staging] TEST ONLY - synthetic drill"),
            issue(2, labels=["incident", "sev4"], title="[TEST] workflow verification"),
            issue(3, labels=["incident", "sev4", "synthetic"], title="Synthetic verification"),
            issue(4, labels=["incident", "sev4"], created_at="2026-09-24T13:00:00Z",
                  title="Production alert"),
        ]
        result = metrics.period(data, START, END)["escaped_defects"]
        self.assertEqual(result["opened"], {"bugs": 0, "incidents": 1, "unique_issues": 1})
        self.assertEqual(result["excluded_synthetic_issues"], 3)
        self.assertEqual(result["by_authorship"]["agent"]["incidents"], 1)
        self.assertEqual(result["unattributed_issues"], 0)

    def test_agent_audit_trail_records_approvals_and_related_gh_aw_runs(self):
        data = evidence()
        agent_pr = pr(1, author="Copilot", merged_by="maintainer", reviews=[
            {"state": "APPROVED", "submitted_at": "2026-09-24T00:00:00Z",
             "commit_id": "a", "user": "approver"},
        ])
        run = attempt(
            workflow="issue_triage", run_id=42, run_url="https://github.com/example/repo/actions/runs/42",
            actor="copilot-swe-agent[bot]", pull_request_numbers=[1],
        )
        data["prs"] = [agent_pr]
        data["attempts"] = [run]
        audit = metrics.period(data, START, END)["agent_audit_trail"]
        self.assertEqual(audit["agent_authored_prs"][0]["approved_by"], ["approver"])
        self.assertEqual(audit["agent_authored_prs"][0]["merged_by"], "maintainer")
        self.assertEqual(audit["agent_authored_prs"][0]["gh_aw_runs"][0]["run_id"], 42)
        self.assertEqual(
            audit["agent_authored_prs"][0]["gh_aw_runs"][0]["run_url"],
            "https://github.com/example/repo/actions/runs/42",
        )
        self.assertEqual(audit["gh_aw_runs"][0]["pull_request_numbers"], [1])
        self.assertNotIn("prompt", str(audit).lower())

    def test_graphql_bot_type_counts_as_automation_authorship(self):
        self.assertEqual(metrics.authorship(pr(author="automation", author_is_bot=True)), "automation")

    def test_issue_links_deduplicate_and_exclude_other_repositories(self):
        data = evidence()
        data["prs"] = [
            pr(1, issues=[issue(), issue(repository="external/repo"), issue(2, created_at=END)]),
            pr(2, issues=[issue()], merged_at="2026-09-24T18:00:00Z"),
        ]
        result = metrics.period(data, START, END)
        self.assertEqual(result["issue_lead_time"], {"samples": 1, "median_hours": 36})
        self.assertEqual(result["linked_merged_prs"], 2)
        self.assertEqual(result["excluded"]["external_issue_references"], 1)
        self.assertEqual(result["excluded"]["negative_issue_durations"], 1)
        self.assertFalse(result["issue_to_deployment"]["available"])

    def test_body_references_count_for_coverage_but_not_closing_lead_time(self):
        data = evidence()
        data["prs"] = [
            pr(1, issues=[issue()], issue_references=[
                {"repository": REPOSITORY, "number": 11},
            ]),
            pr(2, issue_references=[{"repository": REPOSITORY, "number": 12}]),
            pr(3, issue_references=[{"repository": "external/repo", "number": 13}]),
        ]
        result = metrics.period(data, START, END)
        self.assertEqual(result["linked_merged_prs"], 2)
        self.assertEqual(result["link_coverage_denominator"], 3)
        self.assertEqual(result["excluded"]["external_issue_references"], 1)
        self.assertEqual(result["issue_lead_time"], {"samples": 1, "median_hours": 36})

    def test_earliest_issue_merge_outside_window_is_not_recounted(self):
        data = evidence()
        data["prs"] = [
            pr(1, issues=[issue(created_at="2026-09-01T00:00:00Z")],
               created_at="2026-09-01T00:00:00Z", merged_at="2026-09-17T12:00:00Z"),
            pr(2, issues=[issue(created_at="2026-09-01T00:00:00Z")]),
        ]
        result = metrics.period(data, START, END)
        self.assertEqual(result["issue_lead_time"]["samples"], 0)
        self.assertEqual(result["linked_merged_prs"], 1)

    def test_outcomes_include_all_failures_and_preserve_other_states(self):
        statuses = ["success", "failure", "timed_out", "startup_failure", "action_required",
                    "cancelled", "skipped", "neutral", "stale", "new_unknown_state"]
        records = [attempt(conclusion=state) for state in statuses]
        records.append(attempt(status="waiting", conclusion=None))
        result = metrics.outcomes(records)
        self.assertEqual(result["success_percent"], 20)
        self.assertEqual(result["decisive"], 5)
        self.assertEqual(result["counts"]["pending"], 1)
        self.assertEqual(result["counts"]["unknown"], 1)
        self.assertEqual(result["total"], 11)

    def test_reruns_and_ci_events_are_not_collapsed(self):
        data = evidence()
        data["attempts"] = [
            attempt(conclusion="failure"),
            attempt(attempt=2),
            attempt(run_id=20, event="push", branch="main"),
            attempt(run_id=30, event="workflow_dispatch"),
            attempt(run_id=40, workflow="issue_triage", conclusion="failure"),
        ]
        result = metrics.period(data, START, END)["workflows"]
        self.assertEqual(result["ci"]["rerun_attempts"], 1)
        self.assertEqual(result["ci"]["success_percent"], 75)
        self.assertEqual(result["ci"]["by_trigger"]["pull_request"]["success_percent"], 50)
        self.assertEqual(result["ci"]["by_trigger"]["main_push"]["total"], 1)
        self.assertEqual(result["issue_triage"]["counts"]["failure"], 1)

    def test_delivery_completion_windows_reused_jobs_and_waiting(self):
        data = evidence()
        data["repository_created_at"] = START
        data["deployment_jobs"] = [
            job(), job(attempt=2),
            job(id=12, conclusion="failure"),
            job(id=13, environment="production", status="waiting", conclusion=None, completed_at=None),
            job(id=14, environment="production", conclusion="skipped"),
            job(id=15, started_at="2026-09-17T12:00:00Z"),
            job(id=16, completed_at=END),
        ]
        result = metrics.period(data, START, END)
        self.assertEqual(result["deployments"]["staging"]["total"], 3)
        self.assertEqual(result["deployments"]["staging"]["successful_completions"], 2)
        self.assertEqual(result["deployments"]["staging"]["successful_completions_per_day"], 0.286)
        self.assertIsNone(result["deployments"]["production"]["success_percent"])
        report = metrics.build_report(data)
        self.assertEqual(report["inventory_at_collection"]["unfinished_deployment_jobs"][0]["status"], "waiting")

    def test_alert_inventory_is_distinct_from_events_and_denial(self):
        data = evidence()
        data["alerts"]["codeql"]["items"] = [{
            "number": 1, "state": "fixed", "created_at": "2026-09-01T00:00:00Z",
            "fixed_at": START, "dismissed_at": None,
        }]
        report = metrics.build_report(data)
        self.assertEqual(report["current"]["alerts"]["codeql"]["events"]["fixed_at"], 1)
        self.assertEqual(report["current"]["alerts"]["codeql"]["events"]["created_at"], 0)
        self.assertEqual(report["inventory_at_collection"]["alerts"]["codeql"]["states"], {"fixed": 1})
        self.assertIsNone(report["current"]["alerts"]["dependabot"]["events"])

    def test_dependabot_403_is_unavailable_not_zero(self):
        api = FakeApi([
            ({"created_at": START}, {}),
            ([], {}),
            *[({"workflow_runs": [], "total_count": 0}, {}) for _ in range(4)],
            ([], {}),
            metrics.ApiError(403, "dependabot/alerts"),
        ])
        with patch.object(metrics, "collect_prs", return_value=[]):
            result = metrics.collect(api, REPOSITORY, END, "abc")

        dependabot = result["alerts"]["dependabot"]
        self.assertFalse(dependabot["available"])
        self.assertEqual(dependabot["items"], [])
        report = metrics.build_report(result)
        self.assertIsNone(report["current"]["alerts"]["dependabot"]["events"])
        self.assertIn("HTTP 403", metrics.render(report))

    def test_render_is_deterministic_and_does_not_hide_missing_data(self):
        report = metrics.build_report(evidence(), baseline=True)
        output = metrics.render(report)
        self.assertEqual(output, metrics.render(report))
        self.assertIn("HTTP 403", output)
        self.assertIn("0 alerts returned", output)
        self.assertIn("N/A", output)
        self.assertIn("not proof", output)
        self.assertIn("Initial partial baseline", output)
        self.assertIn("N/A (repository did not exist)", output)
        self.assertIn("Agent audit trail", output)
        self.assertIn("not causal proof", output)
        self.assertNotIn("N/A%", output)
        self.assertEqual(metrics.display("@owner|<script>\n"), "&#64;owner&#124;&lt;script&gt; ")


class FakeApi(metrics.GitHub):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, endpoint, method="GET", data=None):
        self.calls.append((endpoint, method, data))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class ApiTests(unittest.TestCase):
    def test_recent_pr_activity_stores_only_normalized_review_and_commit_evidence(self):
        class ActivityApi:
            def pages(self, endpoint, key=None):
                if endpoint.endswith("/reviews"):
                    return [{
                        "state": "APPROVED", "submitted_at": "2026-09-24T00:00:00Z",
                        "commit_id": "sha1", "user": {"login": "reviewer"},
                    }]
                return [
                    {"sha": "sha1", "commit": {"committer": {"date": START},
                     "message": "change\n\nCo-authored-by: Copilot App <copilot@example.com>"}},
                    {"sha": "sha2", "commit": {"committer": {"date": END}, "message": "follow-up"}},
                ]

        target = pr(author="human")
        metrics.collect_recent_pr_activity(ActivityApi(), REPOSITORY, [target], END)
        self.assertTrue(target["commits"][0]["copilot_coauthored"])
        self.assertEqual(target["reviews"][0]["user"], "reviewer")
        self.assertNotIn("message", target["commits"][0])
        self.assertEqual(metrics.authorship(target), "agent")

    def test_labeled_defect_collection_keeps_only_normalized_fields_and_pr_references(self):
        api = FakeApi([
            ([{
                "number": 8, "created_at": "2026-09-24T00:00:00Z",
                "labels": [{"name": "Bug"}, {"name": "Sev2"}],
                "body": "not retained", "title": "private prompt text",
            }, {
                "number": 9, "created_at": "2026-09-24T00:00:00Z",
                "labels": [{"name": "enhancement"}],
            }, {
                "number": 10, "created_at": "2026-09-24T00:00:00Z",
                "labels": [], "pull_request": {"url": "not an issue"},
            }], {}),
            ([{
                "event": "cross-referenced",
                "source": {"issue": {
                    "number": 4, "html_url": f"https://github.com/{REPOSITORY}/pull/4",
                    "pull_request": {"url": "https://api.github.com/repos/example/repo/pulls/4"},
                }},
            }], {}),
        ])
        issues = metrics.collect_defect_issues(api, REPOSITORY, END)
        self.assertEqual(issues, [{
            "number": 8, "created_at": "2026-09-24T00:00:00Z",
            "labels": ["bug", "sev2"], "references_available": True,
            "referenced_pr_numbers": [4], "synthetic": False,
        }])

    def test_transport_retries_transient_failures_without_leaking_denial(self):
        api = metrics.GitHub.__new__(metrics.GitHub)
        api.token = "fake-token"
        response = io.BytesIO(b"[]")
        response.headers = {}
        error = urllib.error.HTTPError("https://api.github.com/test", 503, "unavailable", {}, None)
        with patch.object(metrics.urllib.request, "urlopen", side_effect=[error, response]) as request:
            with patch.object(metrics.time, "sleep") as sleep:
                self.assertEqual(api.request("test")[0], [])
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_rate_limit_exhaustion_is_not_optional_permission_denial(self):
        api = metrics.GitHub.__new__(metrics.GitHub)
        api.token = "fake-token"
        error = urllib.error.HTTPError(
            "https://api.github.com/test", 403, "limited", {"X-RateLimit-Remaining": "0"}, None
        )
        with patch.object(metrics.urllib.request, "urlopen", side_effect=error) as request:
            with patch.object(metrics.time, "sleep"):
                with self.assertRaises(metrics.ApiError) as raised:
                    api.request("test")
        self.assertEqual(raised.exception.status, 429)
        self.assertEqual(request.call_count, 3)

    def test_off_host_pagination_never_receives_token(self):
        api = metrics.GitHub.__new__(metrics.GitHub)
        api.token = "fake-token"
        with patch.object(metrics.urllib.request, "urlopen") as request:
            with self.assertRaises(ValueError):
                api.request("https://attacker.invalid/data")
        request.assert_not_called()

    def test_rest_pagination_and_total_count(self):
        api = FakeApi([
            ({"total_count": 2, "jobs": [{"id": 1}]},
             {"Link": '<https://api.github.com/jobs?page=2>; rel="next"'}),
            ({"total_count": 2, "jobs": [{"id": 2}]}, {}),
        ])
        self.assertEqual(api.pages("jobs", "jobs"), [{"id": 1}, {"id": 2}])

    def test_incomplete_or_malformed_data_is_not_empty_success(self):
        for response in [({"total_count": 2, "jobs": []}, {}), ({"jobs": None}, {})]:
            with self.subTest(response=response), self.assertRaises(ValueError):
                FakeApi([response]).pages("jobs", "jobs")

    def test_repeated_pagination_is_rejected(self):
        response = ([], {"Link": '<jobs?per_page=100>; rel="next"'})
        with self.assertRaises(ValueError):
            FakeApi([response]).pages("jobs")

    def test_graphql_errors_are_explicit(self):
        api = FakeApi([({"data": {"repository": None}, "errors": [{"message": "denied"}]}, {})])
        with self.assertRaises(ValueError):
            api.graphql("query", {})

    def test_nested_and_outer_graphql_pagination(self):
        def link(number):
            return {"number": number, "createdAt": START, "repository": {"nameWithOwner": REPOSITORY}}

        def connection(nodes, cursor=None):
            return {"nodes": nodes, "pageInfo": {"hasNextPage": bool(cursor), "endCursor": cursor}}

        raw_pr = {"number": 1, "title": "Example", "createdAt": START, "mergedAt": END,
                  "state": "MERGED", "baseRefName": "main", "author": None, "mergedBy": None,
                  "body": "Closes #4\nRefs #5\n<!-- Fixes #6 -->",
                  "closingIssuesReferences": connection([link(1)], "nested")}
        api = FakeApi([
            ({"data": {"repository": {"pullRequests": connection([raw_pr], "outer")}}}, {}),
            ({"data": {"repository": {"pullRequest": {"closingIssuesReferences": connection([link(2)])}}}}, {}),
            ({"data": {"repository": {"pullRequests": connection([])}}}, {}),
        ])
        result = metrics.collect_prs(api, "example", "repo")
        self.assertEqual([item["number"] for item in result[0]["issues"]], [1, 2])
        self.assertEqual(result[0]["issue_references"], [
            {"repository": REPOSITORY, "number": 4},
            {"repository": REPOSITORY, "number": 5},
        ])
        self.assertIsNone(result[0]["author"])
        self.assertEqual(len(api.calls), 3)

    def test_only_optional_403_and_404_become_unavailable(self):
        for status in (403, 404, 429, 500):
            with self.subTest(status=status):
                api = FakeApi([
                    ({"created_at": START}, {}),
                    ([], {}),
                    *[({"workflow_runs": [], "total_count": 0}, {}) for _ in range(4)],
                    metrics.ApiError(status, "alerts"),
                    ([], {}),
                ])
                with patch.object(metrics, "collect_prs", return_value=[]):
                    if status in (403, 404):
                        result = metrics.collect(api, REPOSITORY, END, "abc")
                        self.assertFalse(result["alerts"]["codeql"]["available"])
                        self.assertTrue(result["alerts"]["dependabot"]["available"])
                    else:
                        with self.assertRaises(metrics.ApiError):
                            metrics.collect(api, REPOSITORY, END, "abc")

    def test_collect_keeps_every_attempt_and_deduplicates_reused_jobs(self):
        class RunApi:
            def request(self, endpoint):
                if endpoint == f"repos/{REPOSITORY}":
                    return {"created_at": START}, {}
                number = int(endpoint.rsplit("/", 1)[-1])
                return {
                    "event": "workflow_run", "head_branch": "main", "run_started_at": START,
                    "status": "completed", "conclusion": "failure" if number == 1 else "success",
                }, {}

            def pages(self, endpoint, key=None):
                if endpoint.endswith("deploy.yml/runs"):
                    return [{"id": 10, "run_attempt": 2}]
                if endpoint.endswith("/jobs"):
                    return [{
                        "id": 42, "name": "Build and deploy staging", "started_at": START,
                        "completed_at": END, "status": "completed", "conclusion": "success",
                    }]
                return []

        with patch.object(metrics, "collect_prs", return_value=[]):
            result = metrics.collect(RunApi(), REPOSITORY, END, "abc")
        self.assertEqual([a["conclusion"] for a in result["attempts"]], ["failure", "success"])
        self.assertEqual(len(result["deployment_jobs"]), 1)

    def test_unmapped_deployment_job_fails_instead_of_disappearing(self):
        class RunApi:
            def request(self, endpoint):
                if endpoint == f"repos/{REPOSITORY}":
                    return {"created_at": START}, {}
                return {"event": "workflow_run", "head_branch": "main", "run_started_at": START,
                        "status": "completed", "conclusion": "success"}, {}

            def pages(self, endpoint, key=None):
                if endpoint.endswith("deploy.yml/runs"):
                    return [{"id": 10, "run_attempt": 1}]
                if endpoint.endswith("/jobs"):
                    return [{"name": "New unmapped deployment"}]
                return []

        with patch.object(metrics, "collect_prs", return_value=[]):
            with self.assertRaisesRegex(ValueError, "Unmapped"):
                metrics.collect(RunApi(), REPOSITORY, END, "abc")

    def test_rollback_workflow_is_not_queried_or_counted_as_delivery(self):
        endpoints = []

        class RunApi:
            def request(self, endpoint):
                return {"created_at": START}, {}

            def pages(self, endpoint, key=None):
                endpoints.append(endpoint)
                if endpoint.endswith("rollback.yml/runs"):
                    return [{"id": 999, "run_attempt": 1}]
                return []

        with patch.object(metrics, "collect_prs", return_value=[]):
            result = metrics.collect(RunApi(), REPOSITORY, END, "abc")
        self.assertTrue(any(endpoint.endswith("deploy.yml/runs") for endpoint in endpoints))
        self.assertFalse(any("rollback.yml" in endpoint for endpoint in endpoints))
        self.assertEqual(result["deployment_jobs"], [])
        self.assertEqual(result["attempts"], [])


class PublicationApi:
    def __init__(self):
        self.issue = {"body": metrics.DASHBOARD_MARKER, "user": {"login": "example"}}
        self.comments = []
        self.writes = []

    def request(self, endpoint, method="GET", data=None):
        if method == "GET":
            return copy.deepcopy(self.issue), {}
        self.writes.append((endpoint, method, data))
        if method == "POST":
            self.comments.append({"body": data["body"], "user": {"login": "github-actions[bot]"}})
        elif method == "PATCH":
            self.issue["body"] = data["body"]
        return {}, {}

    def pages(self, endpoint):
        return self.comments


class PublicationTests(unittest.TestCase):
    def test_rerun_preserves_original_archive(self):
        api = PublicationApi()
        report = metrics.build_report(evidence())
        metrics.publish(api, REPOSITORY, 28, report)
        original = api.comments[0]["body"]
        report["collected_at"] = "2026-09-25T13:00:00Z"
        metrics.publish(api, REPOSITORY, 28, report)
        self.assertEqual(len(api.comments), 1)
        self.assertEqual(api.comments[0]["body"], original)
        self.assertIn("13:00:00Z", api.issue["body"])

    def test_backfill_does_not_replace_newer_dashboard(self):
        api = PublicationApi()
        report = metrics.build_report(evidence())
        metrics.publish(api, REPOSITORY, 28, report)
        original = api.issue["body"]
        older = evidence()
        older["collected_at"] = "2026-09-24T12:00:00Z"
        metrics.publish(api, REPOSITORY, 28, metrics.build_report(older))
        self.assertEqual(api.issue["body"], original)
        self.assertEqual(len(api.comments), 2)

    def test_same_window_older_observation_does_not_replace_current(self):
        api = PublicationApi()
        report = metrics.build_report(evidence())
        metrics.publish(api, REPOSITORY, 28, report)
        original = api.issue["body"]
        report["collected_at"] = "2026-09-25T11:00:00Z"
        metrics.publish(api, REPOSITORY, 28, report)
        self.assertEqual(api.issue["body"], original)

    def test_foreign_repo_unmarked_issue_and_wrong_owner_refused(self):
        for mutation in ("repository", "marker", "owner", "pr"):
            with self.subTest(mutation=mutation):
                api = PublicationApi()
                report = metrics.build_report(evidence())
                if mutation == "repository":
                    report["repository"] = "foreign/repo"
                elif mutation == "marker":
                    api.issue["body"] = "Unrelated issue"
                elif mutation == "owner":
                    api.issue["user"]["login"] = "untrusted"
                else:
                    api.issue["pull_request"] = {}
                with self.assertRaises(ValueError):
                    metrics.publish(api, REPOSITORY, 28, report)
                self.assertEqual(api.writes, [])

    def test_duplicate_archive_is_an_explicit_error(self):
        api = PublicationApi()
        report = metrics.build_report(evidence())
        metrics.publish(api, REPOSITORY, 28, report)
        api.comments.append(copy.deepcopy(api.comments[0]))
        with self.assertRaises(ValueError):
            metrics.publish(api, REPOSITORY, 28, report)


if __name__ == "__main__":
    unittest.main()
