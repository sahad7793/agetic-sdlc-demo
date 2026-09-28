#!/usr/bin/env python3
"""Read-only GitHub metrics collection, deterministic reporting, and explicit publication."""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request

if __package__:
    from .issue_references import extract_issue_references
else:
    from issue_references import extract_issue_references


SCHEMA_VERSION = 2
API_ROOT = "https://api.github.com"
DASHBOARD_MARKER = "<!-- sdlc-metrics-dashboard:v1 -->"
DEFECT_ATTRIBUTION_DAYS = 30
# Deliberate allowlist: rollback.yml is recovery, not a regular delivery operation.
WORKFLOWS = {
    "ci": "ci.yml",
    "deploy": "deploy.yml",
    "issue_triage": "issue-triage.lock.yml",
    "weekly_report": "weekly-repo-report.lock.yml",
}
ENVIRONMENT_JOBS = {
    "Build and deploy staging": "staging",
    "Deploy production": "production",
}
FAILURES = {"failure", "timed_out", "startup_failure", "action_required"}
CONCLUSIONS = FAILURES | {"success", "cancelled", "skipped", "neutral", "stale"}
PROVENANCE_LIMITATION = (
    "Unavailable: automatic environment SHA and workflow_run head_sha do not "
    "independently identify the image built from the triggering CI SHA. "
    "No issue-to-deployment link is inferred."
)


class ApiError(RuntimeError):
    def __init__(self, status, endpoint):
        self.status = status
        super().__init__(f"GitHub API HTTP {status}: {endpoint}")


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamps must include a timezone")
    return result.astimezone(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def within(value, start, end):
    return value is not None and timestamp(start) <= timestamp(value) < timestamp(end)


def require(condition, message):
    if not condition:
        raise ValueError(message)


class GitHub:
    def __init__(self):
        self.token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not self.token:
            self.token = subprocess.check_output(
                ["gh", "auth", "token"], text=True, stderr=subprocess.DEVNULL
            ).strip()
        require(bool(self.token), "GitHub authentication is required")

    def request(self, endpoint, method="GET", data=None):
        url = endpoint if endpoint.startswith("https://") else f"{API_ROOT}/{endpoint}"
        require(url.startswith(f"{API_ROOT}/"), "Refusing non-GitHub API destination")
        body = None if data is None else json.dumps(data).encode()
        request = urllib.request.Request(
            url, data=body, method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "sdlc-metrics",
            },
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    return json.load(response), response.headers
            except urllib.error.HTTPError as error:
                rate_limited = error.code == 429 or (
                    error.code == 403
                    and (error.headers.get("X-RateLimit-Remaining") == "0"
                         or error.headers.get("Retry-After") is not None)
                )
                if attempt < 2 and (rate_limited or error.code in {500, 502, 503, 504}):
                    delay = min(60, max(1, int(error.headers.get("Retry-After", 2 ** attempt))))
                    time.sleep(delay)
                    continue
                # A rate limit must not masquerade as optional-source permission denial.
                raise ApiError(429 if rate_limited else error.code, endpoint) from None
        raise RuntimeError("Unreachable retry state")

    def pages(self, endpoint, key=None):
        separator = "&" if "?" in endpoint else "?"
        next_url = f"{endpoint}{separator}per_page=100"
        visited = set()
        result = []
        total = None
        while next_url:
            require(next_url not in visited, "Repeated pagination URL")
            visited.add(next_url)
            data, headers = self.request(next_url)
            if key:
                require(isinstance(data, dict) and isinstance(data.get(key), list),
                        f"Malformed paginated response: {endpoint}")
                if "total_count" in data:
                    total = data["total_count"]
                page = data[key]
            else:
                require(isinstance(data, list), f"Expected array: {endpoint}")
                page = data
            result.extend(page)
            link = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("Link", ""))
            next_url = link.group(1) if link else None
        if total is not None:
            require(len(result) == total, f"Incomplete or changing pagination: {endpoint}")
        return result

    def graphql(self, query, variables):
        data, _ = self.request("graphql", "POST", {"query": query, "variables": variables})
        require(not data.get("errors") and isinstance(data.get("data"), dict),
                "GitHub GraphQL response incomplete or denied")
        return data["data"]


def collect_prs(api, owner, name):
    fields = """number title body createdAt mergedAt state baseRefName author { login __typename }
        mergedBy { login }
        closingIssuesReferences(first:100) {
          nodes { number createdAt repository { nameWithOwner } }
          pageInfo { hasNextPage endCursor }
        }"""
    query = """query($owner:String!,$name:String!,$cursor:String) {
      repository(owner:$owner,name:$name) {
        pullRequests(first:100,after:$cursor,orderBy:{field:CREATED_AT,direction:ASC}) {
          nodes { """ + fields + """ }
          pageInfo { hasNextPage endCursor }
        }
      }
    }"""
    prs = []
    cursor = None
    seen = set()
    while True:
        connection = api.graphql(query, {"owner": owner, "name": name, "cursor": cursor})[
            "repository"]["pullRequests"]
        for pr in connection["nodes"]:
            links = pr.pop("closingIssuesReferences")
            issues = list(links["nodes"])
            nested_seen = set()
            while links["pageInfo"]["hasNextPage"]:
                nested_cursor = links["pageInfo"]["endCursor"]
                require(nested_cursor and nested_cursor not in nested_seen,
                        "Incomplete closing-issue pagination")
                nested_seen.add(nested_cursor)
                nested_query = """query($owner:String!,$name:String!,$number:Int!,$cursor:String!) {
                  repository(owner:$owner,name:$name) {
                    pullRequest(number:$number) {
                      closingIssuesReferences(first:100,after:$cursor) {
                        nodes { number createdAt repository { nameWithOwner } }
                        pageInfo { hasNextPage endCursor }
                      }
                    }
                  }
                }"""
                links = api.graphql(nested_query, {
                    "owner": owner, "name": name, "number": pr["number"], "cursor": nested_cursor,
                })["repository"]["pullRequest"]["closingIssuesReferences"]
                issues.extend(links["nodes"])
            prs.append({
                "number": pr["number"], "created_at": pr["createdAt"],
                "merged_at": pr["mergedAt"], "state": pr["state"],
                "base": pr["baseRefName"],
                "author": pr["author"]["login"] if pr["author"] else None,
                "author_is_bot": bool(pr["author"] and pr["author"]["__typename"] == "Bot"),
                "is_revert": pr["title"].lower().startswith("revert"),
                "merged_by": pr["mergedBy"]["login"] if pr["mergedBy"] else None,
                "issue_references": [
                    {"repository": issue_repository, "number": number}
                    for issue_repository, number in extract_issue_references(
                        pr.get("body"), default_repository=f"{owner}/{name}"
                    )
                ],
                "issues": [{"number": issue["number"], "created_at": issue["createdAt"],
                            "repository": issue["repository"]["nameWithOwner"]} for issue in issues],
            })
        if not connection["pageInfo"]["hasNextPage"]:
            break
        cursor = connection["pageInfo"]["endCursor"]
        require(cursor and cursor not in seen, "Incomplete pull-request pagination")
        seen.add(cursor)
    require(len({pr["number"] for pr in prs}) == len(prs), "Duplicate pull request data")
    return prs


def collect_recent_pr_activity(api, repository, prs, collected_at):
    root = f"repos/{repository}"
    end = timestamp(collected_at)
    start = end.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=14)
    for pr in prs:
        if not any(within(pr[field], iso(start), collected_at)
                   for field in ("created_at", "merged_at")):
            continue
        reviews = api.pages(f"{root}/pulls/{pr['number']}/reviews")
        commits = api.pages(f"{root}/pulls/{pr['number']}/commits")
        pr["reviews"] = [{
            "state": review["state"], "submitted_at": review["submitted_at"],
            "commit_id": review["commit_id"],
            "user": review["user"]["login"] if review.get("user") else None,
        } for review in reviews if review.get("submitted_at")]
        pr["commits"] = [{
            "sha": commit["sha"], "committed_at": commit["commit"]["committer"]["date"],
            "copilot_coauthored": bool(re.search(
                r"(?im)^Co-authored-by:\s*.*\bCopilot\b", commit["commit"]["message"]
            )),
        } for commit in commits]


def collect_defect_issues(api, repository, collected_at):
    root = f"repos/{repository}"
    since = iso(timestamp(collected_at).replace(hour=0, minute=0, second=0, microsecond=0)
                - timedelta(days=14))
    issues = api.pages(f"{root}/issues?state=all&since={since}")
    result = []
    for issue in issues:
        if "pull_request" in issue:
            continue
        labels = sorted({
            label["name"].strip().lower() for label in issue.get("labels", [])
            if isinstance(label, dict) and isinstance(label.get("name"), str)
        })
        if "bug" in labels or {"incident", "sev1", "sev2", "sev3", "sev4"} & set(labels):
            references_available = True
            referenced_pr_numbers = set()
            try:
                timeline = api.pages(f"{root}/issues/{issue['number']}/timeline")
            except ApiError as error:
                if error.status not in {403, 404}:
                    raise
                references_available = False
            else:
                prefix = f"https://github.com/{repository}/pull/"
                for event in timeline:
                    source = event.get("source")
                    source_issue = source.get("issue") if isinstance(source, dict) else None
                    if not isinstance(source_issue, dict) or not source_issue.get("pull_request"):
                        continue
                    url = source_issue.get("html_url", "")
                    match = re.fullmatch(re.escape(prefix) + r"(\d+)", url, re.IGNORECASE)
                    if match:
                        referenced_pr_numbers.add(int(match.group(1)))
            result.append({
                "number": issue["number"], "created_at": issue["created_at"],
                "labels": labels, "references_available": references_available,
                "referenced_pr_numbers": sorted(referenced_pr_numbers),
                "synthetic": is_synthetic_issue({
                    "labels": labels, "title": issue.get("title", ""),
                }),
            })
    return result


def collect(api, repository, collected_at, collector_commit):
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository),
            "Expected owner/repository")
    owner, name = repository.split("/")
    root = f"repos/{repository}"
    metadata, _ = api.request(root)
    activity_start = iso(
        timestamp(collected_at).replace(hour=0, minute=0, second=0, microsecond=0)
        - timedelta(days=14)
    )
    evidence = {
        "schema_version": SCHEMA_VERSION, "repository": repository,
        "repository_created_at": metadata["created_at"],
        "collected_at": collected_at, "collector_commit": collector_commit,
        "pr_activity_available_from": activity_start, "issues_available_from": activity_start,
        "prs": collect_prs(api, owner, name), "issues": [], "attempts": [],
        "deployment_jobs": [], "alerts": {},
    }
    collect_recent_pr_activity(api, repository, evidence["prs"], collected_at)
    evidence["issues"] = collect_defect_issues(api, repository, collected_at)
    for kind, path in WORKFLOWS.items():
        runs = api.pages(f"{root}/actions/workflows/{path}/runs", "workflow_runs")
        require(len({run["id"] for run in runs}) == len(runs), f"Duplicate runs for {path}")
        for run in runs:
            for attempt_number in range(1, run["run_attempt"] + 1):
                endpoint = f"{root}/actions/runs/{run['id']}/attempts/{attempt_number}"
                attempt, _ = api.request(endpoint)
                evidence["attempts"].append({
                    "workflow": kind, "run_id": run["id"], "attempt": attempt_number,
                    "event": attempt["event"], "branch": attempt["head_branch"],
                    "started_at": attempt["run_started_at"], "status": attempt["status"],
                    "conclusion": attempt["conclusion"],
                    "run_url": run.get("html_url"),
                    "actor": run["actor"]["login"] if run.get("actor") else None,
                    "pull_request_numbers": sorted({
                        item["number"] for item in run.get("pull_requests", [])
                        if isinstance(item.get("number"), int)
                    }),
                })
                if kind == "deploy":
                    for job in api.pages(f"{endpoint}/jobs", "jobs"):
                        require(job["name"] in ENVIRONMENT_JOBS,
                                f"Unmapped deployment job: {job['name']}")
                        evidence["deployment_jobs"].append({
                            "id": job["id"], "run_id": run["id"], "attempt": attempt_number,
                            "environment": ENVIRONMENT_JOBS[job["name"]],
                            "started_at": job["started_at"], "completed_at": job["completed_at"],
                            "status": job["status"], "conclusion": job["conclusion"],
                        })
    # Jobs can be reused by a rerun of failed jobs; a reused job is not a new delivery.
    unique_jobs = {}
    for job in evidence["deployment_jobs"]:
        unique_jobs[job["id"]] = job
    evidence["deployment_jobs"] = list(unique_jobs.values())
    for kind, endpoint in {
        "codeql": "code-scanning/alerts?tool_name=CodeQL&ref=refs/heads/main",
        "dependabot": "dependabot/alerts",
    }.items():
        try:
            alerts = api.pages(f"{root}/{endpoint}")
        except ApiError as error:
            if error.status not in {403, 404}:
                raise
            evidence["alerts"][kind] = {
                "available": False,
                "reason": f"HTTP {error.status}: denied or feature unavailable for this credential",
                "items": [],
            }
            continue
        evidence["alerts"][kind] = {
            "available": True, "reason": None,
            "items": [{
                "number": alert["number"], "state": alert["state"] or "unknown",
                "created_at": alert["created_at"], "fixed_at": alert.get("fixed_at"),
                "dismissed_at": alert.get("dismissed_at"),
            } for alert in alerts],
        }
    evidence["collection_finished_at"] = iso(datetime.now(timezone.utc))
    return evidence


def outcomes(records):
    counts = Counter()
    for item in records:
        if item["status"] != "completed":
            counts["pending"] += 1
        else:
            conclusion = item["conclusion"]
            counts[conclusion if conclusion in CONCLUSIONS else "unknown"] += 1
    decisive = counts["success"] + sum(counts[state] for state in FAILURES)
    return {
        "total": len(records), "counts": dict(sorted(counts.items())),
        "decisive": decisive, "successes": counts["success"],
        "success_percent": round(100 * counts["success"] / decisive, 2) if decisive else None,
    }


def durations(values):
    return {"samples": len(values), "median_hours": round(statistics.median(values), 3) if values else None}


def medians(values):
    return {"samples": len(values), "median": round(statistics.median(values), 3) if values else None}


def hours(start, end):
    return (timestamp(end) - timestamp(start)).total_seconds() / 3600


def author_group(pr):
    if pr["author"] is None:
        return "unknown"
    if pr["author"] in {"dependabot", "dependabot[bot]"}:
        return "dependabot"
    if pr["author"] in {"copilot-swe-agent", "copilot-swe-agent[bot]"}:
        return "copilot_authored"
    return "other_or_unknown"


def authorship(pr):
    login = (pr["author"] or "").lower()
    if login in {
        "dependabot", "dependabot[bot]", "renovate", "renovate[bot]",
        "github-actions", "github-actions[bot]", "app/github-actions",
    }:
        return "automation"
    if "copilot" in login:
        return "agent"
    if any(commit.get("copilot_coauthored") for commit in pr.get("commits", [])):
        return "agent"
    if pr.get("author_is_bot") or login.endswith("[bot]"):
        return "automation"
    return "human" if pr["author"] else "unknown"


def issue_categories(issue):
    labels = set(issue["labels"])
    return {
        "bug": "bug" in labels,
        "incident": bool(labels & {"incident", "sev1", "sev2", "sev3", "sev4"}),
    }


def is_synthetic_issue(issue):
    labels = {label.strip().lower() for label in issue.get("labels", [])}
    if issue.get("synthetic") or labels & {"test", "drill", "synthetic"}:
        return True
    title = issue.get("title", "")
    if re.match(r"^\[(?:test|drill|synthetic)\](?:\s|$)", title, re.IGNORECASE):
        return True
    incident_title = re.sub(
        r"^\[incident\](?:\[[^\]]+\]){0,2}\s*", "", title, count=1, flags=re.IGNORECASE
    )
    return bool(re.match(
        r"^(?:\[(?:test|drill|synthetic)\](?:\s|$)|"
        r"(?:test only|synthetic|validation[- ]only|drill)\b)",
        incident_title, re.IGNORECASE,
    ))


def rework_metrics(prs):
    valid_review_states = {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}
    rounds = []
    changes_requested = []
    commits_after_review = []
    per_pr = []
    for pr in prs:
        reviews = [review for review in pr.get("reviews", [])
                   if review["state"] in valid_review_states]
        rounds.append(len(reviews))
        requested = sum(review["state"] == "CHANGES_REQUESTED" for review in reviews)
        changes_requested.append(requested)
        commits_count = None
        if reviews:
            first_review = min(reviews, key=lambda review: timestamp(review["submitted_at"]))
            commits = pr.get("commits", [])
            head_index = next((index for index, commit in enumerate(commits)
                               if commit["sha"] == first_review["commit_id"]), None)
            if head_index is not None:
                commits_count = max(0, len(commits) - head_index - 1)
                commits_after_review.append(commits_count)
        per_pr.append({
            "number": pr["number"], "author": pr["author"], "authorship": authorship(pr),
            "review_rounds": len(reviews), "changes_requested_reviews": requested,
            "commits_after_first_review": commits_count, "is_revert": pr["is_revert"],
        })
    return {
        "merged_prs": len(prs),
        "review_rounds": medians(rounds),
        "changes_requested_reviews": medians(changes_requested),
        "commits_after_first_review": medians(commits_after_review),
        "commits_after_first_review_unavailable": sum(item["commits_after_first_review"] is None
                                                       for item in per_pr),
        "pr_title_starts_with_revert": sum(pr["is_revert"] for pr in prs),
        "per_merged_pr": per_pr,
    }


def escaped_defect_metrics(evidence, selected, merged, start, end):
    if timestamp(start) < timestamp(evidence.get("issues_available_from", start)):
        return {
            "available": False,
            "reason": "Labeled issue evidence is retained for the last 14 days",
        }
    period_issues = [
        issue for issue in evidence.get("issues", [])
        if within(issue["created_at"], start, end)
    ]
    excluded_synthetic = sum(is_synthetic_issue(issue) for issue in period_issues)
    issues = [issue for issue in period_issues if not is_synthetic_issue(issue)]
    categories = {issue["number"]: issue_categories(issue) for issue in issues}
    opened = {
        "bugs": sum(categories[number]["bug"] for number in categories),
        "incidents": sum(categories[number]["incident"] for number in categories),
        "unique_issues": len(issues),
    }
    references_available = all(issue.get("references_available", True) for issue in issues)
    counts = {
        group: {"bugs": 0, "incidents": 0, "unique_issues": 0, "merged_prs": 0}
        for group in ("agent", "human", "unknown", "automation")
    }
    pr_counts = {
        pr["number"]: {"bugs": 0, "incidents": 0, "unique_issues": 0, "attribution": set()}
        for pr in selected
    }
    selected_by_number = {pr["number"]: pr for pr in selected}
    attributed_issues = set()
    for issue in issues:
        prior = [pr for pr in merged
                 if timestamp(pr["merged_at"]) < timestamp(issue["created_at"])]
        explicit_numbers = set(issue.get("referenced_pr_numbers", []))
        explicit_numbers.update(
            pr["number"] for pr in selected
            if any(link["number"] == issue["number"]
                   and link["repository"].lower() == evidence["repository"].lower()
                   for link in pr["issues"])
        )
        explicit = [
            pr for pr in selected
            if pr["number"] in explicit_numbers
            and timestamp(pr["merged_at"]) < timestamp(issue["created_at"])
        ]
        if explicit:
            attributed = max(explicit, key=lambda pr: timestamp(pr["merged_at"]))
            method = "explicit_closing_issue_reference"
        else:
            recent = [pr for pr in prior
                      if hours(pr["merged_at"], issue["created_at"]) <= DEFECT_ATTRIBUTION_DAYS * 24]
            if not recent:
                continue
            attributed = max(recent, key=lambda pr: timestamp(pr["merged_at"]))
            method = "heuristic_30_day_temporal_proximity"
        if attributed["number"] not in selected_by_number:
            continue
        category = categories[issue["number"]]
        pr_result = pr_counts[attributed["number"]]
        pr_result["bugs"] += category["bug"]
        pr_result["incidents"] += category["incident"]
        pr_result["unique_issues"] += 1
        pr_result["attribution"].add(method)
        attributed_issues.add(issue["number"])
    per_pr = []
    for pr in selected:
        result = pr_counts[pr["number"]]
        group = authorship(pr)
        group_counts = counts[group]
        group_counts["merged_prs"] += 1
        for field in ("bugs", "incidents", "unique_issues"):
            group_counts[field] += result[field]
        per_pr.append({
            "number": pr["number"], "author": pr["author"], "authorship": group,
            "bugs": result["bugs"], "incidents": result["incidents"],
            "unique_issues": result["unique_issues"],
            "attribution": sorted(result["attribution"]) or ["none"],
        })
    return {
        "available": True,
        "references_available": references_available,
        "opened": opened,
        "by_authorship": counts,
        "per_merged_pr": per_pr,
        "unattributed_issues": len(set(categories) - attributed_issues),
        "excluded_synthetic_issues": excluded_synthetic,
        "attribution_window_days": DEFECT_ATTRIBUTION_DAYS,
        "attribution_method": (
            ("Explicit issue-to-PR references are available. " if references_available else
             "Explicit issue-to-PR references are unavailable for one or more issues. ")
            + "Otherwise, heuristically attribute to the latest preceding main merge within "
            "30 days; temporal proximity does not establish causation."
        ),
    }


def period(evidence, start, end, baseline=False):
    repository = evidence["repository"]
    observed_start = max(timestamp(start), timestamp(evidence["repository_created_at"]))
    observed_hours = max(0, (timestamp(end) - observed_start).total_seconds() / 3600)
    partial = observed_hours < hours(start, end)
    merged = [pr for pr in evidence["prs"] if pr["base"] == "main" and pr["merged_at"]]
    selected = [pr for pr in merged if within(pr["merged_at"], start, end)]
    invalid_cycles = [pr for pr in selected if hours(pr["created_at"], pr["merged_at"]) < 0]
    valid = [pr for pr in selected if pr not in invalid_cycles]
    eligible_rework = [pr for pr in selected if "reviews" in pr and "commits" in pr]
    earliest = {}
    linked_prs = set()
    external_reference_count = 0
    invalid_links = 0
    for pr in merged:
        valid_references = set()
        external_references = set()
        for issue in pr["issues"]:
            issue_repository = issue["repository"].lower()
            if issue_repository != repository.lower():
                external_references.add((issue_repository, issue["number"]))
                continue
            valid_references.add(issue["number"])
            duration = hours(issue["created_at"], pr["merged_at"])
            if duration < 0:
                if pr in selected:
                    invalid_links += 1
                continue
            previous = earliest.get(issue["number"])
            if previous is None or timestamp(pr["merged_at"]) < timestamp(previous["merged_at"]):
                earliest[issue["number"]] = {"merged_at": pr["merged_at"], "hours": duration}
        for reference in pr.get("issue_references", []):
            issue_repository = (reference.get("repository") or repository).lower()
            if issue_repository == repository.lower():
                valid_references.add(reference["number"])
            else:
                external_references.add((issue_repository, reference["number"]))
        if pr in selected:
            if valid_references:
                linked_prs.add(pr["number"])
            external_reference_count += len(external_references)
    lead_times = [link["hours"] for link in earliest.values() if within(link["merged_at"], start, end)]
    workflow_metrics = {}
    for kind in ("ci", "issue_triage", "weekly_report"):
        attempts = [item for item in evidence["attempts"]
                    if item["workflow"] == kind and within(item["started_at"], start, end)]
        workflow_metrics[kind] = outcomes(attempts)
        workflow_metrics[kind]["rerun_attempts"] = sum(item["attempt"] > 1 for item in attempts)
        if kind == "ci":
            workflow_metrics[kind]["by_trigger"] = {
                "pull_request": outcomes([item for item in attempts if item["event"] == "pull_request"]),
                "main_push": outcomes([item for item in attempts
                                       if item["event"] == "push" and item["branch"] == "main"]),
                "other": outcomes([item for item in attempts if item["event"] != "pull_request"
                                   and not (item["event"] == "push" and item["branch"] == "main")]),
            }
    deployments = {}
    for environment in ("staging", "production"):
        jobs = {job["id"]: job for job in evidence["deployment_jobs"]
                if job["environment"] == environment}
        started = [job for job in jobs.values() if within(job["started_at"], start, end)]
        successes = [job for job in jobs.values() if job["conclusion"] == "success"
                     and within(job["completed_at"], start, end)]
        deployments[environment] = outcomes(started)
        deployments[environment]["successful_completions"] = len(successes)
        deployments[environment]["successful_completions_per_day"] = (
            None if baseline or partial else round(len(successes) / 7, 3)
        )
    dependabot = [pr for pr in evidence["prs"] if author_group(pr) == "dependabot"]
    alert_metrics = {}
    for kind, source in evidence["alerts"].items():
        alert_metrics[kind] = {
            "available": source["available"], "reason": source["reason"],
            "events": {
                field: sum(within(alert[field], start, end) for alert in source["items"])
                for field in ("created_at", "fixed_at", "dismissed_at")
            } if source["available"] else None,
        }
    return {
        "start": start, "end": end, "observed_hours": round(observed_hours, 3),
        "partial_repository_history": partial,
        "merged_prs": len(selected),
        "pr_cycle": durations([hours(pr["created_at"], pr["merged_at"]) for pr in valid]),
        "pr_cycle_by_author": {
            group: durations([hours(pr["created_at"], pr["merged_at"]) for pr in valid
                              if author_group(pr) == group])
            for group in ("dependabot", "copilot_authored", "other_or_unknown")
        },
        "rework": (
            {"available": False, "reason": "PR review/commit evidence is retained for the last 14 days"}
            if timestamp(start) < timestamp(evidence.get("pr_activity_available_from", start))
            else {"available": True, **rework_metrics(eligible_rework)}
        ),
        "rework_by_authorship": ({
            group: rework_metrics([pr for pr in eligible_rework if authorship(pr) == group])
            for group in ("agent", "human", "unknown", "automation")
        } if timestamp(start) >= timestamp(evidence.get("pr_activity_available_from", start))
            else None),
        "escaped_defects": escaped_defect_metrics(evidence, selected, merged, start, end),
        "issue_lead_time": durations(lead_times),
        "linked_merged_prs": len(linked_prs), "link_coverage_denominator": len(selected),
        "issue_to_deployment": {"available": False, "reason": PROVENANCE_LIMITATION},
        "excluded": {"external_issue_references": external_reference_count,
                     "negative_issue_durations": invalid_links,
                     "negative_pr_durations": len(invalid_cycles)},
        "workflows": workflow_metrics, "deployments": deployments,
        "agent_audit_trail": {
            "available": True,
            "agent_authored_prs": [{
                "number": pr["number"], "author": pr["author"],
                "approved_by": sorted({
                    review["user"] for review in pr.get("reviews", [])
                    if review["state"] == "APPROVED" and review["user"]
                }),
                "merged_by": pr["merged_by"],
                "gh_aw_runs": [{
                    "run_id": run["run_id"], "run_url": run["run_url"],
                } for run in evidence["attempts"]
                   if run["workflow"] in {"issue_triage", "weekly_report"}
                   and within(run["started_at"], start, end)
                   and pr["number"] in run.get("pull_request_numbers", [])],
            } for pr in evidence["prs"]
               if authorship(pr) == "agent"
               and (within(pr["created_at"], start, end) or within(pr["merged_at"], start, end))],
            "gh_aw_runs": [{
                "workflow": run["workflow"], "run_id": run["run_id"],
                "run_url": run.get("run_url"), "actor": run.get("actor"),
                "started_at": run["started_at"], "pull_request_numbers": run.get("pull_request_numbers", []),
            } for run in evidence["attempts"]
               if run["workflow"] in {"issue_triage", "weekly_report"}
               and within(run["started_at"], start, end)],
        },
        "dependabot_prs": {
            "opened": sum(within(pr["created_at"], start, end) for pr in dependabot),
            "merged": sum(within(pr["merged_at"], start, end) for pr in dependabot if pr["base"] == "main"),
        },
        "alerts": alert_metrics,
    }


def build_report(evidence, baseline=False):
    require(evidence["schema_version"] == SCHEMA_VERSION, "Unsupported evidence schema")
    end = timestamp(evidence["collected_at"]).replace(hour=0, minute=0, second=0, microsecond=0)
    report = {
        "schema_version": SCHEMA_VERSION,
        **{key: evidence[key] for key in (
            "repository", "repository_created_at", "collected_at", "collector_commit",
            "collection_finished_at",
        )},
        "current": period(evidence, iso(end - timedelta(days=7)), iso(end)),
        "previous": period(evidence, iso(end - timedelta(days=14)), iso(end - timedelta(days=7))),
        "inventory_at_collection": {
            "dependabot_open_prs": sum(author_group(pr) == "dependabot" and pr["state"] == "OPEN"
                                      for pr in evidence["prs"]),
            "unfinished_deployment_jobs": [
                {"id": job["id"], "environment": job["environment"], "status": job["status"]}
                for job in evidence["deployment_jobs"] if job["status"] != "completed"
            ],
            "alerts": {
                kind: {"available": source["available"], "reason": source["reason"],
                       "states": dict(Counter(item["state"] for item in source["items"]))
                       if source["available"] else None}
                for kind, source in evidence["alerts"].items()
            },
        },
        "evidence_counts": {
            "prs": len(evidence["prs"]), "workflow_attempts": len(evidence["attempts"]),
            "deployment_jobs": len(evidence["deployment_jobs"]), "issues": len(evidence["issues"]),
        },
    }
    if baseline:
        report["initial_baseline"] = period(
            evidence, evidence["repository_created_at"], evidence["collected_at"], baseline=True
        )
    return report


def display(value):
    if value is None:
        return "N/A"
    # Reports never interpolate issue text, mentions, or arbitrary Markdown from API data.
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace(
        "|", "&#124;").replace("@", "&#64;").replace("\r", " ").replace("\n", " ")


def duration_cell(value):
    return f"{display(value['median_hours'])} h (n={value['samples']})"


def median_cell(value):
    return f"{display(value['median'])} (n={value['samples']})"


def outcome_cell(value):
    counts = ", ".join(f"{display(key)}={count}" for key, count in value["counts"].items()) or "no attempts"
    percentage = f"{value['success_percent']}%" if value["success_percent"] is not None else "N/A"
    return (f"{percentage} ({value['successes']}/{value['decisive']} decisive; "
            f"{value['total']} total); {counts}")


def render(report):
    current, previous = report["current"], report["previous"]
    lines = [
        "### SDLC metrics",
        f"Repository: `{display(report['repository'])}`. Schema: {SCHEMA_VERSION}.",
        f"Collection started: {report['collected_at']}; finished: {report['collection_finished_at']}.",
        f"Collector commit: `{display(report['collector_commit'])}`.",
        "",
        "> [!WARNING]",
        "> Descriptive evidence, not proof that agents caused improvement. Small samples, incomplete "
        "issue links, author attribution, and retained history limit interpretation.",
        "",
        f"Current: **[{current['start']}, {current['end']})**.",
        f"Previous: **[{previous['start']}, {previous['end']})**.",
        f"Repository created: {report['repository_created_at']}. "
        f"Observed hours: current {current['observed_hours']}, previous {previous['observed_hours']}.",
        "A period predating repository creation is partial/unavailable as a comparison, not a zero-activity baseline.",
        "",
        "| Metric | Current | Previous |",
        "| --- | --- | --- |",
    ]

    def row(label, get):
        cells = [get(p) if p["observed_hours"] else "N/A (repository did not exist)"
                 for p in (current, previous)]
        lines.append(f"| {label} | {cells[0]} | {cells[1]} |")

    row("Main PRs merged", lambda p: str(p["merged_prs"]))
    row("Median PR creation to merge", lambda p: duration_cell(p["pr_cycle"]))
    for group in ("dependabot", "copilot_authored", "other_or_unknown"):
        row(f"PR cycle: {group}", lambda p, group=group: duration_cell(p["pr_cycle_by_author"][group]))
    row("Median linked issue creation to first main merge", lambda p: duration_cell(p["issue_lead_time"]))
    row("Issue-link coverage (merged PRs)", lambda p: (
        f"{p['linked_merged_prs']}/{p['link_coverage_denominator']}"
        if p["link_coverage_denominator"] else "N/A (0/0)"
    ))
    row("Excluded external issue references", lambda p: str(p["excluded"]["external_issue_references"]))
    row("Excluded negative issue / PR durations", lambda p: (
        f"{p['excluded']['negative_issue_durations']} / {p['excluded']['negative_pr_durations']}"
    ))
    for field, label in (
        ("review_rounds", "Median review rounds"),
        ("changes_requested_reviews", "Median changes-requested reviews"),
        ("commits_after_first_review", "Median commits after first reviewed commit"),
    ):
        row(label, lambda p, field=field: (
            median_cell(p["rework"][field]) if p["rework"]["available"]
            else display(p["rework"]["reason"])
        ))
    row("Merged PRs titled Revert", lambda p: (
        str(p["rework"]["pr_title_starts_with_revert"]) if p["rework"]["available"]
        else display(p["rework"]["reason"])
    ))
    for group in ("agent", "human", "unknown"):
        row(f"{group.title()} PRs: median review rounds", lambda p, group=group: (
            median_cell(p["rework_by_authorship"][group]["review_rounds"])
            if p["rework_by_authorship"] is not None else display(p["rework"]["reason"])
        ))
        row(f"{group.title()} PRs: median changes-requested reviews", lambda p, group=group: (
            median_cell(p["rework_by_authorship"][group]["changes_requested_reviews"])
            if p["rework_by_authorship"] is not None else display(p["rework"]["reason"])
        ))
        row(f"{group.title()} PRs: median commits after first reviewed commit", lambda p, group=group: (
            median_cell(p["rework_by_authorship"][group]["commits_after_first_review"])
            if p["rework_by_authorship"] is not None else display(p["rework"]["reason"])
        ))
        row(f"{group.title()} merged PRs reverted", lambda p, group=group: (
            str(p["rework_by_authorship"][group]["pr_title_starts_with_revert"])
            if p["rework_by_authorship"] is not None else display(p["rework"]["reason"])
        ))
    group = "automation"
    row("Automation PRs: median review rounds", lambda p: (
        median_cell(p["rework_by_authorship"][group]["review_rounds"])
        if p["rework_by_authorship"] is not None else display(p["rework"]["reason"])
    ))
    row("Automation PRs: median changes-requested reviews", lambda p: (
        median_cell(p["rework_by_authorship"][group]["changes_requested_reviews"])
        if p["rework_by_authorship"] is not None else display(p["rework"]["reason"])
    ))
    row("Automation PRs: median commits after first reviewed commit", lambda p: (
        median_cell(p["rework_by_authorship"][group]["commits_after_first_review"])
        if p["rework_by_authorship"] is not None else display(p["rework"]["reason"])
    ))
    row("Automation merged PRs reverted", lambda p: (
        str(p["rework_by_authorship"][group]["pr_title_starts_with_revert"])
        if p["rework_by_authorship"] is not None else display(p["rework"]["reason"])
    ))
    row("New bug issues opened", lambda p: (
        str(p["escaped_defects"]["opened"]["bugs"]) if p["escaped_defects"]["available"]
        else display(p["escaped_defects"]["reason"])
    ))
    row("New incident issues opened", lambda p: (
        str(p["escaped_defects"]["opened"]["incidents"]) if p["escaped_defects"]["available"]
        else display(p["escaped_defects"]["reason"])
    ))
    row("Excluded synthetic/test/drill issues", lambda p: (
        str(p["escaped_defects"]["excluded_synthetic_issues"])
        if p["escaped_defects"]["available"] else display(p["escaped_defects"]["reason"])
    ))
    row("Escaped defects by agent-authored PRs (bugs / incidents / unique issues)", lambda p: (
        f"{p['escaped_defects']['by_authorship']['agent']['bugs']} / "
        f"{p['escaped_defects']['by_authorship']['agent']['incidents']} / "
        f"{p['escaped_defects']['by_authorship']['agent']['unique_issues']}"
        if p["escaped_defects"]["available"] else display(p["escaped_defects"]["reason"])
    ))
    row("Escaped defects by human-authored PRs (bugs / incidents / unique issues)", lambda p: (
        f"{p['escaped_defects']['by_authorship']['human']['bugs']} / "
        f"{p['escaped_defects']['by_authorship']['human']['incidents']} / "
        f"{p['escaped_defects']['by_authorship']['human']['unique_issues']}"
        if p["escaped_defects"]["available"] else display(p["escaped_defects"]["reason"])
    ))
    row("Escaped defects by unknown-authorship PRs (bugs / incidents / unique issues)", lambda p: (
        f"{p['escaped_defects']['by_authorship']['unknown']['bugs']} / "
        f"{p['escaped_defects']['by_authorship']['unknown']['incidents']} / "
        f"{p['escaped_defects']['by_authorship']['unknown']['unique_issues']}"
        if p["escaped_defects"]["available"] else display(p["escaped_defects"]["reason"])
    ))
    row("Escaped defects by automation-authored PRs (bugs / incidents / unique issues)", lambda p: (
        f"{p['escaped_defects']['by_authorship']['automation']['bugs']} / "
        f"{p['escaped_defects']['by_authorship']['automation']['incidents']} / "
        f"{p['escaped_defects']['by_authorship']['automation']['unique_issues']}"
        if p["escaped_defects"]["available"] else display(p["escaped_defects"]["reason"])
    ))
    row("Agent-authored PRs and gh-aw runs in audit trail", lambda p: (
        f"{len(p['agent_audit_trail']['agent_authored_prs'])} PRs / "
        f"{len(p['agent_audit_trail']['gh_aw_runs'])} runs"
    ))
    for kind in ("ci", "issue_triage", "weekly_report"):
        row(f"{kind} reliability", lambda p, kind=kind: outcome_cell(p["workflows"][kind]))
        row(f"{kind} rerun attempts", lambda p, kind=kind: str(p["workflows"][kind]["rerun_attempts"]))
    for trigger in ("pull_request", "main_push", "other"):
        row(f"CI: {trigger}", lambda p, trigger=trigger: outcome_cell(p["workflows"]["ci"]["by_trigger"][trigger]))
    for environment in ("staging", "production"):
        row(f"{environment} delivery reliability", lambda p, env=environment: outcome_cell(p["deployments"][env]))
        row(f"{environment} successful deliveries / per day", lambda p, env=environment: (
            f"{p['deployments'][env]['successful_completions']} / "
            f"{display(p['deployments'][env]['successful_completions_per_day'])}"
        ))
    row("Dependabot PRs opened / merged to main", lambda p: (
        f"{p['dependabot_prs']['opened']} / {p['dependabot_prs']['merged']}"
    ))
    for kind in ("codeql", "dependabot"):
        row(f"{kind} alerts created / fixed / dismissed", lambda p, kind=kind: (
            " / ".join(str(p["alerts"][kind]["events"][field]) for field in ("created_at", "fixed_at", "dismissed_at"))
            if p["alerts"][kind]["available"] else display(p["alerts"][kind]["reason"])
        ))
    lines.extend(["", "### Rework by merged PR", ""])
    if current["rework"]["available"]:
        lines.extend([
            "| PR | Author | Split | Review rounds | Changes requested | Commits after first reviewed commit | Revert |",
            "| --- | --- | --- | ---: | ---: | ---: | --- |",
        ])
        for item in current["rework"]["per_merged_pr"]:
            lines.append(
                f"| #{item['number']} | {display(item['author'])} | {item['authorship']} | "
                f"{item['review_rounds']} | {item['changes_requested_reviews']} | "
                f"{display(item['commits_after_first_review'])} | "
                f"{'yes' if item['is_revert'] else 'no'} |"
            )
        lines.append(
            f"\nCommits after first reviewed commit unavailable for "
            f"{current['rework']['commits_after_first_review_unavailable']} PR(s)."
        )
    else:
        lines.append(display(current["rework"]["reason"]))

    lines.extend(["", "### Escaped-defect candidates by merged PR", ""])
    if current["escaped_defects"]["available"]:
        lines.extend([
            "| PR | Author | Split | Bugs | Incidents | Unique issues | Attribution |",
            "| --- | --- | --- | ---: | ---: | ---: | --- |",
        ])
        for item in current["escaped_defects"]["per_merged_pr"]:
            lines.append(
                f"| #{item['number']} | {display(item['author'])} | {item['authorship']} | "
                f"{item['bugs']} | {item['incidents']} | {item['unique_issues']} | "
                f"{', '.join(item['attribution'])} |"
            )
        lines.append(
            f"\nUnattributed opened defect issues: {current['escaped_defects']['unattributed_issues']}. "
            f"{current['escaped_defects']['attribution_method']}"
        )
    else:
        lines.append(display(current["escaped_defects"]["reason"]))

    audit = current["agent_audit_trail"]
    lines.extend(["", "### Agent audit trail", "",
                  "Read-only evidence for the current window; full prompts and tool-call logs are not included.",
                  "", "**Agent-authored PRs**", "",
                  "| PR | Author | Approved by | Merged by | Matching gh-aw run IDs / URLs |",
                  "| --- | --- | --- | --- | --- |"])
    for item in audit["agent_authored_prs"]:
        runs = ", ".join(
            f"`{run['run_id']}` {display(run['run_url'])}" for run in item["gh_aw_runs"]
        ) or "none observed"
        lines.append(
            f"| #{item['number']} | {display(item['author'])} | "
            f"{', '.join(display(user) for user in item['approved_by']) or 'none observed'} | "
            f"{display(item['merged_by'])} | {runs} |"
        )
    if not audit["agent_authored_prs"]:
        lines.append("| None observed | — | — | — | — |")
    lines.extend(["", "**gh-aw workflow runs**", "",
                  "| Workflow | Run ID | URL | Actor | Started | PR numbers |",
                  "| --- | ---: | --- | --- | --- | --- |"])
    for run in audit["gh_aw_runs"]:
        lines.append(
            f"| {run['workflow']} | {run['run_id']} | {display(run['run_url'])} | "
            f"{display(run['actor'])} | {run['started_at']} | "
            f"{', '.join(f'#{number}' for number in run['pull_request_numbers']) or '—'} |"
        )
    if not audit["gh_aw_runs"]:
        lines.append("| None observed | — | — | — | — | — |")
    lines.extend([
        "", "### Coverage and interpretation", "",
        f"- **Issue-to-deployment:** {PROVENANCE_LIMITATION}",
        "- Reliability = success / (success + failure + timed_out + startup_failure + action_required). "
        "Cancellation, skipped, neutral, stale, pending and unknown are shown separately, not failures.",
        "- Reliability cohorts use attempt/job start time; outcomes reflect collection-time observations, "
        "not reconstructed period-end state. Repeated collection can revise older cohorts.",
        "- Delivery frequency uses successful job completion time; reused job IDs count once. "
        "Per-day rates are withheld for partial periods. Production success means the pipeline "
        "completed, not verified runtime health.",
        "- Issue-link coverage includes valid same-repository references parsed from PR bodies using the "
        "same `Fixes`/`Closes`/`Resolves`/`Refs`/`References` or GitHub issue-URL syntax as the PR check. "
        "External-repository URLs are excluded and counted. Linked issue creation-to-merge lead time is "
        "narrower: only GitHub's same-repository `closingIssuesReferences` (actual closing links) qualify; "
        "each issue uses its earliest linked main merge, and body-only references never invent a creation "
        "timestamp or closing event.",
        "- Defect counts include issues labeled `bug` and incidents labeled `incident` or `sev1`-`sev4`, "
        "opened in the reporting window, except issues marked `test`, `drill`, or `synthetic`, or whose "
        "title clearly starts with a test/drill marker. Apply the `synthetic` label to test incidents; "
        "the title fallback also excludes legacy issues such as validation drills. Per-PR attribution "
        "prefers an explicit same-repository closing-issue "
        "reference; otherwise it is a **heuristic** assigning the issue to the latest preceding main merge "
        "within 30 days. Temporal proximity is not causal proof. The opened count includes attributed and "
        "unattributed issues; defects linked to multiple PRs are assigned once.",
        "- Agent PRs are authored by a Copilot bot or contain a `Co-authored-by: Copilot` commit trailer, "
        "unless authored by a known automation bot. Other bot accounts are automation, named non-bot authors "
        "are human, and missing authors are unknown. "
        "Review rounds count submitted "
        "non-comment reviews; commits after the first review count commits after the SHA it reviewed, a proxy "
        "for post-review rework. Reverted PRs have titles starting with `Revert`.",
        "- Agent audit data lists PR number, author, approving reviewers, merger, and matching gh-aw run IDs/URLs; "
        "it also lists gh-aw workflow runs in the window. It contains no prompts, secrets, or tool-call logs.",
        "- CI includes build, tests and CodeQL together; execution success is not a defect or vulnerability count. "
        "Agentic execution success is not advice quality or proof of a published report.",
        "- GitHub APIs are not transactional; observations span the collection interval. "
        "Deleted/expired runs and missing historical state cannot be recovered. "
        "Alert event timestamps show available latest transitions, not a complete event log.",
        "- Review/commit and labeled-issue evidence is retained for the last 14 days. Older "
        "baseline periods show these metrics as unavailable rather than zero.",
        "", "### Current inventory (not historical period-end state)", "",
        f"- Open Dependabot PRs: {report['inventory_at_collection']['dependabot_open_prs']}.",
    ])
    inventory = report["inventory_at_collection"]
    unfinished = Counter(f"{job['environment']}: {job['status']}" for job in inventory["unfinished_deployment_jobs"])
    lines.append("- Unfinished delivery jobs: " + (
        "; ".join(f"{display(key)}={count}" for key, count in sorted(unfinished.items())) or "none"
    ) + ". Waiting for approval is not failure.")
    for kind, source in inventory["alerts"].items():
        states = ", ".join(f"{display(state)}={count}" for state, count in sorted((source["states"] or {}).items()))
        lines.append(f"- {kind} alert inventory: " + (
            states or "0 alerts returned" if source["available"] else display(source["reason"])
        ) + ".")
    if "initial_baseline" in report:
        base = report["initial_baseline"]
        lines.extend([
            "", "### Initial partial baseline", "",
            f"Available repository history: **[{base['start']}, {base['end']})** "
            f"({base['observed_hours']} hours); not a pre-agentic control period.",
            f"- Main merged PRs: {base['merged_prs']}; median cycle: {duration_cell(base['pr_cycle'])}.",
            f"- Linked issue median: {duration_cell(base['issue_lead_time'])}; "
            f"linked PR coverage: {base['linked_merged_prs']}/{base['link_coverage_denominator']}.",
            f"- CI: {outcome_cell(base['workflows']['ci'])}.",
        ])
        for env in ("staging", "production"):
            lines.append(f"- {env}: {base['deployments'][env]['successful_completions']} successful "
                         f"delivery completions; {outcome_cell(base['deployments'][env])}.")
        lines.append("Full baseline breakdown is in the accompanying JSON; no per-day extrapolation is applied.")
    lines.extend([
        "", "### Sources", "",
        f"Evidence: {report['evidence_counts']['prs']} PRs, "
        f"{report['evidence_counts']['issues']} labeled issues, "
        f"{report['evidence_counts']['workflow_attempts']} workflow attempts, "
        f"{report['evidence_counts']['deployment_jobs']} unique delivery jobs.",
        f"[Metric definitions and runbook](https://github.com/{report['repository']}/blob/main/docs/agentic-sdlc.md#sdlc-metrics-dashboard) "
        f"| [Workflow runs](https://github.com/{report['repository']}/actions/workflows/sdlc-metrics.yml)",
    ])
    return "\n".join(lines) + "\n"


def snapshot_marker(report):
    return f"<!-- sdlc-metrics-period:{report['current']['end']} -->"


def publish(api, repository, issue_number, report):
    require(report["repository"] == repository, "Report repository does not match publication target")
    require(report["schema_version"] == SCHEMA_VERSION, "Unsupported report schema")
    root = f"repos/{repository}/issues/{issue_number}"
    issue, _ = api.request(root)
    require("pull_request" not in issue and DASHBOARD_MARKER in (issue["body"] or ""),
            "Target is not an explicitly marked dashboard issue")
    require(issue["user"]["login"] in {repository.split("/")[0], "github-actions[bot]"},
            "Dashboard issue must be bootstrapped by the repository owner or GitHub Actions")
    markdown = render(report)
    require(len(markdown.encode()) < 60000, "Report exceeds safe issue-body size")
    marker = snapshot_marker(report)
    comments = api.pages(f"{root}/comments")
    existing = [comment for comment in comments if marker in comment["body"]
                and comment["user"]["login"] in {repository.split("/")[0], "github-actions[bot]"}]
    require(len(existing) <= 1, "Multiple archived snapshots for the same reporting window")
    if not existing:
        api.request(f"{root}/comments", "POST", {
            "body": f"{marker}\n{markdown}\nOriginal snapshot for this window; reruns do not replace this archive."
        })
    old_period = re.search(r"<!-- sdlc-metrics-period:([^ ]+) -->", issue["body"] or "")
    if old_period and timestamp(old_period.group(1)) > timestamp(report["current"]["end"]):
        return  # Archive a backfill without replacing the newest dashboard.
    observed = re.search(r"<!-- sdlc-metrics-observed:([^ ]+) -->", issue["body"] or "")
    if old_period and old_period.group(1) == report["current"]["end"] and observed:
        if timestamp(observed.group(1)) > timestamp(report["collected_at"]):
            return
    api.request(root, "PATCH", {
        "body": (f"{DASHBOARD_MARKER}\n{marker}\n"
                 f"<!-- sdlc-metrics-observed:{report['collected_at']} -->\n{markdown}\n"
                 "Dated comments preserve the original snapshot for each window; this body is the latest observation.")
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    preview = subparsers.add_parser("collect", help="Read-only live collection")
    preview.add_argument("--repository", required=True)
    preview.add_argument("--output", type=Path, required=True)
    preview.add_argument("--baseline", action="store_true")
    publication = subparsers.add_parser("publish", help="Update an explicitly bootstrapped issue")
    publication.add_argument("--repository", required=True)
    publication.add_argument("--issue", required=True, type=int)
    publication.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    try:
        api = GitHub()
        if args.command == "collect":
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
            evidence = collect(api, args.repository, iso(datetime.now(timezone.utc)), commit)
            report = build_report(evidence, args.baseline)
            args.output.mkdir(parents=True, exist_ok=True)
            for name, content in (
                ("evidence.json", json.dumps(evidence, indent=2) + "\n"),
                ("report.json", json.dumps(report, indent=2) + "\n"),
                ("report.md", render(report)),
            ):
                (args.output / name).write_text(content, encoding="utf-8")
            print(f"Read-only report written to {args.output}")
        else:
            require(args.issue > 0, "Expected a positive issue number")
            publish(api, args.repository, args.issue, json.loads(args.report.read_text(encoding="utf-8")))
            print(f"Dashboard issue {args.issue} updated or preserved if already newer")
    except (ApiError, ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        print(f"SDLC metrics failed: {error}", file=sys.stderr)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as file:
                guidance = ("Collection failed; last good dashboard preserved."
                            if args.command == "collect"
                            else "Publication failed; inspect the dashboard before retrying.")
                file.write(f"### SDLC metrics failed\n\n{display(error)}\n\n{guidance}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
