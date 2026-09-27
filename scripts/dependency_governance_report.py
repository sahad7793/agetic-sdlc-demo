#!/usr/bin/env python3
"""Read-only dependency-governance and security-alert triage report.

Collects the *current, open* inventory of Dependabot and CodeQL alerts plus
open Dependabot pull requests, and renders a deterministic (no LLM) markdown
report. This script never dismisses or fixes an alert, never merges, closes,
or comments on a pull request, and never changes any repository setting —
see docs/agentic-sdlc.md > Dependency governance for the human-controlled
review process this report is meant to support.

Fails collection closed rather than reporting a fabricated "0 alerts" when an
alert source can't be read (e.g. security-events not enabled/authorized for
this credential): such sources are marked unavailable with the HTTP reason,
mirroring scripts/sdlc_metrics.py's alert-inventory handling.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request


SCHEMA_VERSION = 1
API_ROOT = "https://api.github.com"
# Deliberately excludes secret-scanning alerts: unlike code-scanning/dependabot
# alerts, that endpoint's required token permission is not part of the
# documented GITHUB_TOKEN permission set, so it is left for a future
# iteration once the required permission model is confirmed (see
# docs/agentic-sdlc.md > Dependency governance).
ALERT_SOURCES = {
    "dependabot": "dependabot/alerts?state=open",
    "codeql": "code-scanning/alerts?state=open&tool_name=CodeQL&ref=refs/heads/main",
}
SEVERITY_ORDER = ("critical", "high", "medium", "low", "unknown")
SEVERITY_RANK = {name: rank for rank, name in enumerate(SEVERITY_ORDER)}
TOP_ALERTS_LIMIT = 15
DEPENDABOT_AUTHORS = {"dependabot[bot]"}


class ApiError(RuntimeError):
    def __init__(self, status, endpoint):
        self.status = status
        super().__init__(f"GitHub API HTTP {status}: {endpoint}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamps must include a timezone")
    return result.astimezone(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def age_in_days(created_at, now):
    return (now - timestamp(created_at)).days


class GitHub:
    """Thin read-only wrapper around the GitHub REST API. GET only, never mutates."""

    def __init__(self):
        self.token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        require(bool(self.token), "GitHub authentication (GH_TOKEN/GITHUB_TOKEN) is required")

    def request(self, endpoint):
        url = endpoint if endpoint.startswith("https://") else f"{API_ROOT}/{endpoint}"
        require(url.startswith(f"{API_ROOT}/"), "Refusing non-GitHub API destination")
        request = urllib.request.Request(
            url, method="GET",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "dependency-governance-report",
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
                raise ApiError(429 if rate_limited else error.code, endpoint) from None
        raise RuntimeError("Unreachable retry state")

    def pages(self, endpoint):
        separator = "&" if "?" in endpoint else "?"
        next_url = f"{endpoint}{separator}per_page=100"
        visited = set()
        result = []
        while next_url:
            require(next_url not in visited, "Repeated pagination URL")
            visited.add(next_url)
            data, headers = self.request(next_url)
            require(isinstance(data, list), f"Expected array: {endpoint}")
            result.extend(data)
            link = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("Link", ""))
            next_url = link.group(1) if link else None
        return result


def dependabot_severity(alert):
    severity = ((alert.get("security_advisory") or {}).get("severity") or "").lower()
    return severity if severity in SEVERITY_RANK else "unknown"


def codeql_severity(alert):
    severity = ((alert.get("rule") or {}).get("security_severity_level") or "").lower()
    return severity if severity in SEVERITY_RANK else "unknown"


def collect_alert_source(api, root, kind, endpoint):
    try:
        alerts = api.pages(f"{root}/{endpoint}")
    except ApiError as error:
        if error.status not in {403, 404}:
            raise
        return {
            "available": False,
            "reason": f"HTTP {error.status}: denied or feature unavailable for this credential",
            "items": [],
        }
    severity_of = dependabot_severity if kind == "dependabot" else codeql_severity
    items = []
    for alert in alerts:
        item = {
            "number": alert["number"],
            "state": alert["state"] or "unknown",
            "severity": severity_of(alert),
            "created_at": alert["created_at"],
            "html_url": alert.get("html_url"),
        }
        if kind == "dependabot":
            dependency = alert.get("dependency") or {}
            package = dependency.get("package") or {}
            item["package"] = package.get("name")
            item["ecosystem"] = package.get("ecosystem")
            item["scope"] = dependency.get("scope")
        else:
            item["rule"] = (alert.get("rule") or {}).get("id")
        items.append(item)
    return {"available": True, "reason": None, "items": items}


def collect_dependabot_pull_requests(api, root):
    pulls = api.pages(f"{root}/pulls?state=open")
    prs = []
    for pull in pulls:
        author = (pull.get("user") or {}).get("login")
        if author not in DEPENDABOT_AUTHORS:
            continue
        prs.append({
            "number": pull["number"],
            "title": pull["title"],
            "created_at": pull["created_at"],
            "html_url": pull["html_url"],
            "draft": bool(pull.get("draft")),
        })
    return prs


def collect(api, repository, collected_at):
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository),
            "Expected owner/repository")
    root = f"repos/{repository}"
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "repository": repository,
        "collected_at": collected_at,
        "alerts": {
            kind: collect_alert_source(api, root, kind, endpoint)
            for kind, endpoint in ALERT_SOURCES.items()
        },
        "dependabot_pull_requests": collect_dependabot_pull_requests(api, root),
    }
    return evidence


def alert_summary(source, now):
    by_severity = {name: 0 for name in SEVERITY_ORDER}
    oldest_days = None
    for item in source["items"]:
        if item["state"] != "open":
            continue
        by_severity[item["severity"]] += 1
        days = age_in_days(item["created_at"], now)
        oldest_days = days if oldest_days is None else max(oldest_days, days)
    return {
        "available": source["available"],
        "reason": source["reason"],
        "by_severity": by_severity,
        "total_open": sum(by_severity.values()),
        "oldest_open_days": oldest_days,
    }


def top_alerts(evidence, now, limit=TOP_ALERTS_LIMIT):
    """Most severe, then oldest, open alerts across every available source."""
    candidates = []
    for kind, source in evidence["alerts"].items():
        if not source["available"]:
            continue
        for item in source["items"]:
            if item["state"] != "open":
                continue
            candidates.append({"kind": kind, **item})
    candidates.sort(key=lambda item: (
        SEVERITY_RANK[item["severity"]], -age_in_days(item["created_at"], now),
    ))
    return candidates[:limit]


def build_report(evidence):
    now = timestamp(evidence["collected_at"])
    alerts = {kind: alert_summary(source, now) for kind, source in evidence["alerts"].items()}
    prs = evidence["dependabot_pull_requests"]
    oldest_pr_days = max((age_in_days(pr["created_at"], now) for pr in prs), default=None)
    return {
        "schema_version": evidence["schema_version"],
        "repository": evidence["repository"],
        "collected_at": evidence["collected_at"],
        "alerts": alerts,
        "top_alerts": top_alerts(evidence, now),
        "dependabot_pull_requests": {
            "open_count": len(prs),
            "oldest_open_days": oldest_pr_days,
            "items": sorted(prs, key=lambda pr: pr["created_at"]),
        },
    }


def display(value):
    return value if value else "Not applicable"


def render(report):
    lines = [
        "# Dependency governance report",
        "",
        f"Repository: {report['repository']}",
        f"Collected: {report['collected_at']}",
        "",
        "This report is **read-only**: it never dismisses or fixes an alert, "
        "never merges, closes, or comments on a pull request, and never "
        "changes a repository setting. Triage and remediation remain a human "
        "decision — see "
        "[docs/agentic-sdlc.md > Dependency governance]"
        "(https://github.com/sahad7793/agetic-sdlc-demo/blob/main/docs/agentic-sdlc.md#dependency-governance) "
        "for the review process.",
        "",
        "## Open alert inventory",
        "",
        "| Source | Available | Critical | High | Medium | Low | Unknown | Total open | Oldest open (days) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for kind, summary in report["alerts"].items():
        if not summary["available"]:
            lines.append(f"| {kind} | No ({display(summary['reason'])}) | - | - | - | - | - | - | - |")
            continue
        counts = summary["by_severity"]
        oldest = summary["oldest_open_days"]
        oldest_display = "N/A (no open alerts)" if oldest is None else str(oldest)
        lines.append(
            f"| {kind} | Yes | {counts['critical']} | {counts['high']} | {counts['medium']} | "
            f"{counts['low']} | {counts['unknown']} | {summary['total_open']} | {oldest_display} |"
        )
    lines.extend(["", "## Needs attention (most severe, then oldest, open alerts)", ""])
    if report["top_alerts"]:
        lines.append("| Source | Severity | # | Package/rule | Opened | Link |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for alert in report["top_alerts"]:
            reference = alert.get("package") or alert.get("rule") or "-"
            lines.append(
                f"| {alert['kind']} | {alert['severity']} | {alert['number']} | {reference} | "
                f"{alert['created_at']} | {display(alert.get('html_url'))} |"
            )
    else:
        lines.append("None of the available sources report an open alert.")
    prs = report["dependabot_pull_requests"]
    lines.extend([
        "",
        "## Open Dependabot pull requests",
        "",
        f"Open count: {prs['open_count']}. Oldest open: "
        + ("N/A (none open)" if prs["oldest_open_days"] is None else f"{prs['oldest_open_days']} days")
        + ".",
        "",
    ])
    if prs["items"]:
        lines.append("| # | Title | Opened | Link |")
        lines.append("| --- | --- | --- | --- |")
        for pull in prs["items"]:
            title = pull["title"] + (" (draft)" if pull["draft"] else "")
            lines.append(f"| {pull['number']} | {title} | {pull['created_at']} | {pull['html_url']} |")
    lines.extend([
        "",
        "No pull request listed above was merged, approved, or modified by this "
        "report — Dependabot auto-merge is intentionally not configured for this "
        "repository; every dependency update requires human review and a green "
        "CI run, same as any other pull request.",
    ])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect_parser = subparsers.add_parser("collect", help="Read-only collection and report rendering")
    collect_parser.add_argument("--repository", required=True, help="owner/repo")
    collect_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        api = GitHub()
        collected_at = iso(datetime.now(timezone.utc))
        evidence = collect(api, args.repository, collected_at)
        report = build_report(evidence)
        args.output.mkdir(parents=True, exist_ok=True)
        for name, content in (
            ("evidence.json", json.dumps(evidence, indent=2) + "\n"),
            ("report.json", json.dumps(report, indent=2) + "\n"),
            ("report.md", render(report)),
        ):
            (args.output / name).write_text(content, encoding="utf-8")
        print(f"Read-only dependency governance report written to {args.output}")
    except (ApiError, ValueError, KeyError, OSError) as error:
        print(f"Dependency governance report failed: {error}", file=sys.stderr)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as file:
                file.write(f"### Dependency governance report failed\n\n{error}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
