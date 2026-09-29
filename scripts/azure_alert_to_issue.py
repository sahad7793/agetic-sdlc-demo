#!/usr/bin/env python3
"""Poll fired Azure Monitor alerts and optionally create deduplicated GitHub issues."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote, urlparse


API_VERSION = "2019-03-01"
MAX_SCOPES = 10
MAX_PAGES_PER_SCOPE = 5
MAX_ALERTS_PER_RUN = 500
MAX_EXISTING_ISSUES = 1000
MAX_EXISTING_LABELS = 1000
FINGERPRINT_MARKER = "<!-- azure-alert-fingerprint:"
SEVERITIES = {
    "Sev0": ("Sev1", "Critical", "sev1"),
    "Sev1": ("Sev2", "High", "sev2"),
    "Sev2": ("Sev3", "Moderate", "sev3"),
    "Sev3": ("Sev4", "Low", "sev4"),
    "Sev4": ("Sev4", "Low", "sev4"),
}
ENVIRONMENTS = {"staging", "production", "shared"}
RESOURCE_GROUP_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,90}$")
GUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class AlertPollError(RuntimeError):
    """A safe, non-payload-bearing error suitable for workflow logs."""


def require(condition, message):
    if not condition:
        raise ValueError(message)


def parse_scopes(value):
    scopes = []
    seen = set()
    for entry in (value or "").split(","):
        if not entry.strip():
            continue
        require("=" in entry, "Alert scopes must use environment=resource-group entries.")
        environment, resource_group = (part.strip() for part in entry.split("=", 1))
        require(environment in ENVIRONMENTS, "Alert scope has an unsupported environment.")
        require(bool(RESOURCE_GROUP_PATTERN.fullmatch(resource_group)),
                "Alert scope has an invalid resource group name.")
        scope_key = (environment, resource_group.lower())
        require(scope_key not in seen, "Alert scopes cannot repeat a resource group.")
        seen.add(scope_key)
        scopes.append({"environment": environment, "resource_group": resource_group})
    require(0 < len(scopes) <= MAX_SCOPES, "Configure between 1 and 10 alert scopes.")
    return scopes


def validate_configuration(environ):
    require(environ.get("AZURE_ALERTS_ENABLED", "").lower() == "true",
            "Azure alert polling is not explicitly enabled.")
    subscription_id = environ.get("AZURE_SUBSCRIPTION_ID", "")
    client_id = environ.get("AZURE_CLIENT_ID", "")
    tenant_id = environ.get("AZURE_TENANT_ID", "")
    require(bool(GUID_PATTERN.fullmatch(subscription_id)), "Azure subscription ID is missing or invalid.")
    require(bool(GUID_PATTERN.fullmatch(client_id)), "Azure client ID is missing or invalid.")
    require(bool(GUID_PATTERN.fullmatch(tenant_id)), "Azure tenant ID is missing or invalid.")
    return subscription_id, parse_scopes(environ.get("AZURE_ALERT_SCOPES", ""))


def fingerprint(alert_id):
    require(isinstance(alert_id, str) and 0 < len(alert_id) <= 4096,
            "Alert is missing a valid identity.")
    normalized = alert_id.strip()
    require(bool(normalized), "Alert is missing a valid identity.")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def fired_time(value):
    require(isinstance(value, str) and value.strip(), "Alert is missing its start timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Alert has an invalid fired timestamp.") from error
    require(parsed.tzinfo is not None, "Alert fired timestamp must include a timezone.")
    return parsed.astimezone(timezone.utc)


def normalize_alert_rule(value):
    if not isinstance(value, str):
        return "Not provided"
    value = value.strip()
    if "://" in value or value.startswith("/") or "?" in value or "#" in value:
        return "Not provided"
    value = re.sub(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "REDACTED",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"(?i)\b(password|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;]+",
        lambda match: f"{match.group(1)}=REDACTED",
        value,
    )
    value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~-]+", "Bearer REDACTED", value)
    value = "".join(
        char if char.isascii() and (char.isalnum() or char in " ._-()`") else " "
        for char in value
    )
    value = " ".join(value.split())
    return value[:120] if value else "Not provided"


def map_alert(item, environment, resource_group):
    if not isinstance(item, dict):
        return None
    properties = item.get("properties")
    if not isinstance(properties, dict):
        return None
    essentials = properties.get("essentials")
    if not isinstance(essentials, dict) or essentials.get("monitorCondition") != "Fired":
        return None

    alert_id = essentials.get("alertId") or item.get("id")
    try:
        alert_fingerprint = fingerprint(alert_id)
        fired_at = fired_time(essentials.get("startDateTime"))
    except ValueError:
        return None

    severity_value = essentials.get("severity")
    if not isinstance(severity_value, str):
        return None
    severity = SEVERITIES.get(severity_value)
    if severity is None:
        return None
    sev_tag, severity_name, label = severity
    alert_rule = normalize_alert_rule(essentials.get("alertRule"))
    return {
        "fingerprint": alert_fingerprint,
        "environment": environment,
        "resource_group": resource_group,
        "severity_tag": sev_tag,
        "severity_name": severity_name,
        "label": label,
        "fired_at": fired_at,
        "alert_rule": alert_rule,
    }


def response_items(payload):
    require(isinstance(payload, dict), "Azure returned an invalid alerts response.")
    items = payload.get("value")
    require(isinstance(items, list), "Azure returned an invalid alerts collection.")
    return items, payload.get("nextLink")


def validate_next_link(next_link, expected_path):
    parsed = urlparse(next_link)
    require(parsed.scheme == "https"
            and parsed.netloc in {"management.azure.com", "management.azure.com:443"}
            and parsed.path.lower() == expected_path.lower(),
            "Azure returned an invalid alerts pagination link.")
    return next_link


class AzureCli:
    """Read-only Azure CLI access for the Azure Monitor Alerts Management API."""

    def run_json(self, url):
        try:
            result = subprocess.run(
                ["az", "rest", "--method", "get", "--url", url, "--output", "json",
                 "--only-show-errors"],
                capture_output=True, text=True, timeout=60, check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise AlertPollError("Azure alert request could not be completed.") from error
        if result.returncode != 0:
            raise AlertPollError(f"Azure alert request failed (exit code {result.returncode}).")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise AlertPollError("Azure returned an invalid alerts response.") from error


def collect_alerts(azure, subscription_id, scopes):
    alerts = []
    for scope in scopes:
        expected_path = (
            f"/subscriptions/{subscription_id}/resourceGroups/"
            f"{quote(scope['resource_group'], safe='')}/providers/"
            "Microsoft.AlertsManagement/alerts"
        )
        url = (
            f"https://management.azure.com{expected_path}"
            f"?api-version={API_VERSION}&timeRange=1d&monitorCondition=Fired"
            "&includeContext=false&includeEgressConfig=false&pageCount=100"
            "&select=severity,monitorCondition,alertRule,startDateTime"
        )
        for page_number in range(MAX_PAGES_PER_SCOPE):
            payload = azure.run_json(url)
            items, next_link = response_items(payload)
            alerts.extend(
                (item, scope["environment"], scope["resource_group"]) for item in items
            )
            require(len(alerts) <= MAX_ALERTS_PER_RUN,
                    "Alert collection exceeded the per-run limit; no issues were created.")
            if not next_link:
                break
            require(page_number + 1 < MAX_PAGES_PER_SCOPE,
                    "Alert collection exceeded the page limit; no issues were created.")
            url = validate_next_link(next_link, expected_path)
    return alerts


def command(*args, timeout=60):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise AlertPollError("GitHub issue operation could not be completed.") from error
    if result.returncode != 0:
        raise AlertPollError(f"GitHub issue operation failed (exit code {result.returncode}).")
    return result.stdout


def marker_for(alert_fingerprint):
    return f"{FINGERPRINT_MARKER}{alert_fingerprint} -->"


def existing_fingerprints():
    payload = json.loads(command(
        "gh", "issue", "list", "--state", "all", "--limit", str(MAX_EXISTING_ISSUES),
        "--json", "body",
    ))
    require(isinstance(payload, list), "GitHub returned an invalid issues collection.")
    require(len(payload) < MAX_EXISTING_ISSUES,
            "Issue history exceeded the deduplication scan limit; no issues were created.")
    markers = set()
    for issue in payload:
        body = issue.get("body") if isinstance(issue, dict) else None
        if not isinstance(body, str):
            continue
        markers.update(re.findall(r"<!-- azure-alert-fingerprint:([0-9a-f]{64}) -->", body))
    return markers


def ensure_labels(labels):
    payload = json.loads(command(
        "gh", "label", "list", "--limit", str(MAX_EXISTING_LABELS), "--json", "name"
    ))
    require(isinstance(payload, list), "GitHub returned an invalid labels collection.")
    present = {row.get("name") for row in payload if isinstance(row, dict)}
    definitions = {
        "incident": ("d93f0b", "An incident tracked via the incident-response workflow"),
        "sev1": ("b60205", "Sev1 - Critical incident severity"),
        "sev2": ("d93f0b", "Sev2 - High incident severity"),
        "sev3": ("fbca04", "Sev3 - Moderate incident severity"),
        "sev4": ("c2e0c6", "Sev4 - Low incident severity"),
    }
    missing = labels - present
    require(not (len(payload) >= MAX_EXISTING_LABELS and missing),
            "Label inventory exceeded the scan limit; no issue was created.")
    for label in sorted(missing):
        color, description = definitions[label]
        command("gh", "label", "create", label, "--color", color,
                "--description", description, "--force")


def build_issue(alert):
    return (
        f"{FINGERPRINT_MARKER}{alert['fingerprint']} -->\n"
        "## Summary\n\n"
        "A scheduled, read-only Azure Monitor poll detected a fired alert instance.\n\n"
        f"- **Environment:** {alert['environment']}\n"
        f"- **Severity:** {alert['severity_tag']} - {alert['severity_name']}\n"
        f"- **Alert instance started:** {alert['fired_at'].strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        f"- **Alert fingerprint:** `{alert['fingerprint']}`\n\n"
        "Alert-controlled text (including rule names), descriptions, resource identifiers, "
        "custom dimensions, and alert context are intentionally omitted so untrusted "
        "monitor metadata is not passed to downstream issue automation. Verify the alert "
        "and affected scope in Azure Monitor before taking action.\n\n"
        "This workflow only reads Azure alert instances and creates this GitHub issue. "
        "It does not acknowledge or close alerts, change Azure resources, or trigger "
        "remediation. Assign an incident owner during triage.\n"
    )


def file_issue(alert):
    title = (
        f"[INCIDENT][{alert['severity_tag']}][{alert['environment']}] "
        f"Azure Monitor alert {alert['fingerprint'][:12]}"
    )
    ensure_labels({"incident", alert["label"]})
    body_path = None
    try:
        body_path = Path(os.environ["RUNNER_TEMP"]) / (
            f"azure-alert-{alert['fingerprint']}.md"
        )
        body_path.write_text(build_issue(alert), encoding="utf-8")
        command("gh", "issue", "create", "--title", title, "--body-file", str(body_path),
                "--label", "incident", "--label", alert["label"])
    finally:
        if body_path is not None:
            body_path.unlink(missing_ok=True)


def process(alerts, publish=False, azure=None, subscription_id=None, scopes=None,
            now=None, enforce_freshness=True):
    now = now or datetime.now(timezone.utc)
    if azure is not None:
        raw_alerts = collect_alerts(azure, subscription_id, scopes)
    else:
        raw_alerts = alerts

    normalized = []
    skipped = 0
    for item, environment, resource_group in raw_alerts:
        alert = map_alert(item, environment, resource_group)
        if alert is None or (
            enforce_freshness and alert["fired_at"] < now - timedelta(days=1)
        ):
            skipped += 1
            continue
        normalized.append(alert)
    require(len(normalized) <= MAX_ALERTS_PER_RUN,
            "Alert collection exceeded the per-run limit; no issues were created.")

    if not publish:
        return {"observed": len(raw_alerts), "fired": len(normalized),
                "skipped": skipped, "created": 0, "duplicates": 0}

    known = existing_fingerprints()
    seen = set()
    created = 0
    duplicates = 0
    for alert in normalized:
        alert_fingerprint = alert["fingerprint"]
        if alert_fingerprint in known or alert_fingerprint in seen:
            duplicates += 1
            continue
        file_issue(alert)
        seen.add(alert_fingerprint)
        created += 1
    return {"observed": len(raw_alerts), "fired": len(normalized),
            "skipped": skipped, "created": created, "duplicates": duplicates}


def write_configuration(output_path, environ):
    ready = True
    try:
        validate_configuration(environ)
    except ValueError:
        ready = False
    with output_path.open("a", encoding="utf-8") as output:
        output.write(f"ready={'true' if ready else 'false'}\n")
    if not ready:
        print("Azure alert polling is disabled or missing required environment configuration.")
    else:
        print("Azure alert polling configuration is present.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    config_parser = subparsers.add_parser("configuration", help="Validate opt-in workflow configuration")
    config_parser.add_argument("--output", type=Path, required=True)
    poll_parser = subparsers.add_parser("poll", help="Poll alerts or inspect a local fixture")
    poll_parser.add_argument("--fixture", type=Path, help="Local fixture; never reads Azure or writes GitHub")
    poll_parser.add_argument("--publish", action="store_true", help="Create deduplicated GitHub issues")
    args = parser.parse_args()
    try:
        if args.command == "configuration":
            return write_configuration(args.output, os.environ)
        require(not (args.fixture and args.publish),
                "A local fixture can never be used to publish an issue.")
        if args.fixture:
            payload = json.loads(args.fixture.read_text(encoding="utf-8"))
            items, _ = response_items(payload)
            alerts = [(item, "staging", "synthetic-fixture") for item in items]
            counts = process(alerts, enforce_freshness=False)
        else:
            subscription_id, scopes = validate_configuration(os.environ)
            counts = process(
                None, publish=args.publish, azure=AzureCli(),
                subscription_id=subscription_id, scopes=scopes,
            )
        print(
            "Azure alert poll completed: "
            f"observed={counts['observed']} fired={counts['fired']} "
            f"skipped={counts['skipped']} created={counts['created']} "
            f"duplicates={counts['duplicates']}."
        )
        return 0
    except (AlertPollError, ValueError, KeyError, TypeError, OSError,
            subprocess.SubprocessError, json.JSONDecodeError) as error:
        # Never include Azure response bodies or alert fields in action logs.
        print(f"Azure alert poll failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
