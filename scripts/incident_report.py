#!/usr/bin/env python3
"""Translate a human-supplied, forwarded alert into a structured GitHub incident issue.

This is deliberately deterministic (no LLM): every field in the created issue is either
verbatim operator input, a static per-severity SLA policy target, or a static doc/tool
link. Nothing here queries or fabricates telemetry, and nothing here mutates Azure
resources or triggers another workflow (e.g. Rollback) automatically.
"""

from datetime import datetime, timedelta, timezone
import html
import json
import os
from pathlib import Path
import subprocess
import sys


# Severity -> (label slug, acknowledge-by minutes, mitigate-by minutes). These are
# static response-time *targets* set by policy, not a measurement of anything.
SEVERITIES = {
    "Sev1 - Critical": ("sev1", 15, 60),
    "Sev2 - High": ("sev2", 30, 240),
    "Sev3 - Moderate": ("sev3", 120, 480),
    "Sev4 - Low": ("sev4", 480, 2880),
}

LABELS = {
    "incident": ("d93f0b", "A production/staging incident tracked via the incident-response workflow"),
    "sev1": ("b60205", "Sev1 - Critical incident severity"),
    "sev2": ("d93f0b", "Sev2 - High incident severity"),
    "sev3": ("fbca04", "Sev3 - Moderate incident severity"),
    "sev4": ("c2e0c6", "Sev4 - Low incident severity"),
}

NOT_PROVIDED = "Not provided — add during triage (e.g. from Application Insights transaction search)."


def require(condition, message):
    if not condition:
        raise ValueError(message)


def command(*args, timeout=60, input_text=None):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                             input=input_text, check=False)
    require(result.returncode == 0, f"{args[0]} failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout


def existing_label_names():
    payload = json.loads(command("gh", "label", "list", "--limit", "200", "--json", "name"))
    return {row["name"] for row in payload}


def ensure_labels(names):
    """Idempotently create any of the required labels that do not already exist."""
    present = existing_label_names()
    for name in names:
        if name in present:
            continue
        color, description = LABELS[name]
        command("gh", "label", "create", name, "--color", color, "--description", description, "--force")


def optional(value):
    value = (value or "").strip()
    return value if value else NOT_PROVIDED


def sla_deadline(now, minutes):
    return (now + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M UTC")


def sanitize_title_fragment(value):
    """Collapse newlines/control chars so a hostile title can't span multiple
    issue-title lines or otherwise break rendering. Content is still trusted
    verbatim otherwise (workflow_dispatch already requires repo write access)."""
    return " ".join(value.split())


def build_title(environment, severity, alert_title):
    sev_tag = severity.split(" - ", 1)[0]
    return f"[INCIDENT][{sev_tag}][{environment}] {sanitize_title_fragment(alert_title)}"


def code_fence(text):
    """Return a fence of backticks longer than any backtick run already in
    ``text``, so verbatim alert text can never prematurely close the fence."""
    longest_run = 0
    current_run = 0
    for char in text:
        if char == "`":
            current_run += 1
            longest_run = max(longest_run, current_run)
        else:
            current_run = 0
    return "`" * max(3, longest_run + 1)


def build_body(inputs, now, run_url, actor):
    severity = inputs["severity"]
    require(severity in SEVERITIES, f"Unknown severity: {severity}")
    _, ack_minutes, mitigate_minutes = SEVERITIES[severity]

    lines = [
        "## Summary",
        "",
        f"- **Environment:** {inputs['environment']}",
        f"- **Severity:** {severity}",
        f"- **Alert source:** {inputs['alert_source']}",
        "",
        "## Alert context",
        "",
        f"- **Correlation / operation ID:** {optional(inputs['correlation_id'])}",
        f"- **Log query:** {optional(inputs['log_query_link'])}",
        f"- **Azure Portal alert:** {optional(inputs['azure_alert_link'])}",
        "",
        "**Forwarded alert text (verbatim, as submitted):**",
        "",
        code_fence(inputs["alert_summary"]),
        inputs["alert_summary"],
        code_fence(inputs["alert_summary"]),
        "",
        "## Response timing targets (policy, not a measurement)",
        "",
        "These are static SLA targets for this severity, computed from this issue's "
        "creation time. They are not derived from any monitoring data.",
        "",
        f"- **Acknowledge by:** {sla_deadline(now, ack_minutes)} (target: {ack_minutes} min)",
        f"- **Mitigate by:** {sla_deadline(now, mitigate_minutes)} (target: {mitigate_minutes} min)",
        "",
        "## Ownership",
        "",
        f"- **Primary responder:** @{actor} (the operator who filed this incident)",
        "- **Additional owners:** _edit this issue to add responders as they join._",
        "",
        "## Triage checklist",
        "",
        "- [ ] Acknowledge and confirm scope (affected environment, users, endpoints)",
        "- [ ] Correlate the alert with the observability workbook / Application Insights "
        "using the correlation ID and log query above",
        "- [ ] Review recent Deploy history for the affected environment for a likely cause",
        "- [ ] Decide whether this is a rollback candidate "
        "(see `docs/agentic-sdlc.md` → Rollback)",
        "- [ ] If rolling back, dispatch the **Rollback** workflow with a `reason` that "
        "references this issue number — do not perform Azure changes outside that workflow",
        "- [ ] Record the incident timeline and resolution in this issue",
        "- [ ] For Sev1/Sev2, close with a short postmortem note before closing the issue",
        "",
        "## Runbook & tooling links",
        "",
        "- Rollback workflow: Actions → Rollback → Run workflow "
        "(see `docs/agentic-sdlc.md#rollback`)",
        "- Observability workbook and alerts: `docs/agentic-sdlc.md#observability`",
        "- This incident-response workflow: `docs/agentic-sdlc.md#incident-response`",
        "",
        "## Provenance",
        "",
        f"- **Filed by:** @{actor}",
        f"- **Workflow run:** {run_url}",
        f"- **Filed at:** {now.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "- **Raw dispatch inputs are echoed above verbatim for audit purposes.**",
    ]
    return "\n".join(lines) + "\n"


def read_inputs():
    inputs = {
        "environment": os.environ["INPUT_ENVIRONMENT"],
        "severity": os.environ["INPUT_SEVERITY"],
        "alert_source": os.environ["INPUT_ALERT_SOURCE"],
        "alert_title": os.environ["INPUT_ALERT_TITLE"],
        "alert_summary": os.environ["INPUT_ALERT_SUMMARY"],
        "correlation_id": os.environ.get("INPUT_CORRELATION_ID", ""),
        "log_query_link": os.environ.get("INPUT_LOG_QUERY_LINK", ""),
        "azure_alert_link": os.environ.get("INPUT_AZURE_ALERT_LINK", ""),
    }
    require(inputs["environment"] in {"staging", "production", "shared"}, "Invalid environment.")
    require(inputs["severity"] in SEVERITIES, "Invalid severity.")
    require(inputs["alert_title"].strip() and len(inputs["alert_title"]) <= 200,
            "A nonblank alert title of at most 200 characters is required.")
    require(inputs["alert_summary"].strip() and len(inputs["alert_summary"]) <= 4000,
            "A nonblank alert summary of at most 4000 characters is required.")
    return inputs


def save_audit(path, audit):
    path.write_text(json.dumps(audit, indent=2) + "\n")


def perform(path):
    audit = {"outcome": "No issue created; resolution incomplete",
             "environment": os.environ.get("INPUT_ENVIRONMENT", ""),
             "severity": os.environ.get("INPUT_SEVERITY", ""),
             "alert_title": os.environ.get("INPUT_ALERT_TITLE", ""),
             "actor": os.environ.get("ACTOR", "")}
    save_audit(path, audit)
    try:
        inputs = read_inputs()
        now = datetime.now(timezone.utc)
        actor = os.environ["ACTOR"]
        title = build_title(inputs["environment"], inputs["severity"], inputs["alert_title"])
        body = build_body(inputs, now, os.environ["RUN_URL"], actor)
        label_slug, _, _ = SEVERITIES[inputs["severity"]]
        ensure_labels({"incident", label_slug})
        body_file = path.with_suffix(".body.md")
        body_file.write_text(body)
        output = command("gh", "issue", "create", "--title", title, "--body-file", str(body_file),
                          "--label", "incident", "--label", label_slug, "--assignee", actor)
        issue_url = output.strip().splitlines()[-1] if output.strip() else ""
        audit.update({"outcome": "Succeeded: incident issue created", "title": title,
                      "issue_url": issue_url})
        save_audit(path, audit)
    except (ValueError, KeyError, subprocess.SubprocessError, OSError) as error:
        audit["error"] = str(error)
        audit["outcome"] = "Failed: " + audit["outcome"]
        save_audit(path, audit)
        raise


def summary(path):
    audit = json.loads(path.read_text()) if path.exists() else {
        "outcome": "Incident filing did not complete; inspect checkout/job logs.",
        "environment": os.environ.get("INPUT_ENVIRONMENT", ""),
        "severity": os.environ.get("INPUT_SEVERITY", ""),
        "alert_title": os.environ.get("INPUT_ALERT_TITLE", ""),
        "actor": os.environ.get("ACTOR", ""),
    }
    lines = ["## Incident filing audit", ""]
    for key in ("outcome", "issue_url", "title", "environment", "severity", "alert_title",
                "actor", "error"):
        value = str(audit.get(key) or "Not resolved / not applicable")
        lines.append(f"<p><strong>{key}</strong></p><pre>{html.escape(value)}</pre>")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as output:
        output.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    try:
        require(len(sys.argv) == 3 and sys.argv[1] in {"run", "summary"},
                "Usage: incident_report.py run|summary AUDIT_JSON")
        (perform if sys.argv[1] == "run" else summary)(Path(sys.argv[2]))
    except (ValueError, KeyError, subprocess.SubprocessError, OSError) as error:
        print(f"Incident filing error: {error}", file=sys.stderr)
        sys.exit(1)
