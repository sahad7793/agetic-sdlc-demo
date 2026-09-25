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


SCHEMA_VERSION = 1
API_ROOT = "https://api.github.com"
DASHBOARD_MARKER = "<!-- sdlc-metrics-dashboard:v1 -->"
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
    fields = """number createdAt mergedAt state baseRefName author { login }
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


def collect(api, repository, collected_at, collector_commit):
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository),
            "Expected owner/repository")
    owner, name = repository.split("/")
    root = f"repos/{repository}"
    metadata, _ = api.request(root)
    evidence = {
        "schema_version": SCHEMA_VERSION, "repository": repository,
        "repository_created_at": metadata["created_at"],
        "collected_at": collected_at, "collector_commit": collector_commit,
        "prs": collect_prs(api, owner, name), "attempts": [], "deployment_jobs": [],
        "alerts": {},
    }
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


def hours(start, end):
    return (timestamp(end) - timestamp(start)).total_seconds() / 3600


def author_group(pr):
    if pr["author"] in {"dependabot", "dependabot[bot]"}:
        return "dependabot"
    if pr["author"] in {"copilot-swe-agent", "copilot-swe-agent[bot]"}:
        return "copilot_authored"
    return "other_or_unknown"


def period(evidence, start, end, baseline=False):
    repository = evidence["repository"]
    observed_start = max(timestamp(start), timestamp(evidence["repository_created_at"]))
    observed_hours = max(0, (timestamp(end) - observed_start).total_seconds() / 3600)
    partial = observed_hours < hours(start, end)
    merged = [pr for pr in evidence["prs"] if pr["base"] == "main" and pr["merged_at"]]
    selected = [pr for pr in merged if within(pr["merged_at"], start, end)]
    invalid_cycles = [pr for pr in selected if hours(pr["created_at"], pr["merged_at"]) < 0]
    valid = [pr for pr in selected if pr not in invalid_cycles]
    earliest = {}
    linked_prs = set()
    external_links = 0
    invalid_links = 0
    for pr in merged:
        for issue in pr["issues"]:
            if issue["repository"].lower() != repository.lower():
                if pr in selected:
                    external_links += 1
                continue
            duration = hours(issue["created_at"], pr["merged_at"])
            if duration < 0:
                if pr in selected:
                    invalid_links += 1
                continue
            if pr in selected:
                linked_prs.add(pr["number"])
            previous = earliest.get(issue["number"])
            if previous is None or timestamp(pr["merged_at"]) < timestamp(previous["merged_at"]):
                earliest[issue["number"]] = {"merged_at": pr["merged_at"], "hours": duration}
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
        "issue_lead_time": durations(lead_times),
        "linked_merged_prs": len(linked_prs), "link_coverage_denominator": len(selected),
        "issue_to_deployment": {"available": False, "reason": PROVENANCE_LIMITATION},
        "excluded": {"external_issue_references": external_links,
                     "negative_issue_durations": invalid_links,
                     "negative_pr_durations": len(invalid_cycles)},
        "workflows": workflow_metrics, "deployments": deployments,
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
            "deployment_jobs": len(evidence["deployment_jobs"]),
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
        "- Same-repository explicit closing links only; each issue uses its earliest linked main merge. "
        "Unlinked work is not assigned a guessed lead time.",
        "- CI includes build, tests and CodeQL together; execution success is not a defect or vulnerability count. "
        "Agentic execution success is not advice quality or proof of a published report.",
        "- GitHub APIs are not transactional; observations span the collection interval. "
        "Deleted/expired runs and missing historical state cannot be recovered. "
        "Alert event timestamps show available latest transitions, not a complete event log.",
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
