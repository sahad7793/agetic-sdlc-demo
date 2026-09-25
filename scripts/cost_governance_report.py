#!/usr/bin/env python3
"""Read-only, non-monetary Azure cost-governance reporting.

Reports tag compliance, resource inventory, and budget/Action Group
*existence* only. This script never requests, reads, stores, or prints a
dollar amount, spend figure, budget threshold, or forecast: it deliberately
uses `az resource list`, `az consumption budget list`, and
`az monitor action-group list` only for counts/names/tags, and any amount
fields present in raw Azure CLI output are stripped before evidence is ever
serialized. See docs/agentic-sdlc.md > Cost governance > Manual governance
report.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


SCHEMA_VERSION = 1
REQUIRED_TAGS = ("application", "environment", "costCenter", "owner")
DEFAULT_RESOURCE_GROUPS = (
    "rg-taskmanagement-shared",
    "rg-taskmanagement-staging-centralus",
    "rg-taskmanagement-production-westus2",
    "rg-taskmanagement-staging",
    "rg-taskmanagement-production",
    "rg-taskmanagement-production-centralus",
)


class AzCliError(RuntimeError):
    def __init__(self, args, stderr):
        self.args_display = " ".join(args)
        super().__init__(f"az CLI failed: {self.args_display}\n{stderr.strip()}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


class AzureCli:
    """Thin wrapper around the `az` CLI. Every call here is read-only (list/show)."""

    def run_json(self, args):
        result = subprocess.run(
            ["az", *args, "--output", "json"],
            capture_output=True, text=True, timeout=120, check=False,
        )
        if result.returncode != 0:
            raise AzCliError(args, result.stderr)
        text = result.stdout.strip()
        return json.loads(text) if text else []


def resource_group_inventory(az, resource_group):
    """Resource names/types/tags for one resource group. No cost data is requested."""
    resources = az.run_json([
        "resource", "list", "--resource-group", resource_group,
        "--query", "[].{name:name, type:type, tags:tags}",
    ])
    compliant = 0
    by_type = {}
    for resource in resources:
        tags = resource.get("tags") or {}
        by_type[resource["type"]] = by_type.get(resource["type"], 0) + 1
        if all(tags.get(tag) for tag in REQUIRED_TAGS):
            compliant += 1
    return {
        "resource_group": resource_group,
        "total_resources": len(resources),
        "tag_compliant_resources": compliant,
        "resource_types": by_type,
    }


def budget_existence(az, resource_group):
    """Whether any budget is configured for this scope. Amount fields are never read."""
    budgets = az.run_json(["consumption", "budget", "list", "--resource-group", resource_group])
    return {"resource_group": resource_group, "budget_count": len(budgets)}


def action_group_existence(az, resource_group):
    groups = az.run_json(["monitor", "action-group", "list", "--resource-group", resource_group])
    return {"resource_group": resource_group, "action_group_count": len(groups)}


def collect(az, resource_groups, collected_at):
    inventory = [resource_group_inventory(az, rg) for rg in resource_groups]
    budgets = [budget_existence(az, rg) for rg in resource_groups]
    action_groups = [action_group_existence(az, rg) for rg in resource_groups]
    return {
        "schema_version": SCHEMA_VERSION,
        "collected_at": collected_at,
        "required_tags": list(REQUIRED_TAGS),
        "resource_groups": inventory,
        "budgets": budgets,
        "action_groups": action_groups,
    }


def tag_compliance_percent(evidence):
    total = sum(rg["total_resources"] for rg in evidence["resource_groups"])
    compliant = sum(rg["tag_compliant_resources"] for rg in evidence["resource_groups"])
    if total == 0:
        return None  # No resources observed; not the same as 100% or 0% compliance.
    return round(100 * compliant / total, 1)


def render(evidence):
    percent = tag_compliance_percent(evidence)
    percent_display = "N/A (no resources observed)" if percent is None else f"{percent}%"
    lines = [
        "# Azure cost-governance report",
        "",
        f"Collected: {evidence['collected_at']}",
        "",
        "This report contains no dollar amounts, spend figures, budget thresholds, "
        "or Cost Management billing data. It reports tag compliance, resource "
        "inventory, and budget/Action Group existence only.",
        "",
        f"**Overall tag compliance ({', '.join(evidence['required_tags'])}): {percent_display}**",
        "",
        "| Resource group | Resources | Tag-compliant | Budget configured | Action Group configured |",
        "| --- | --- | --- | --- | --- |",
    ]
    budgets_by_rg = {row["resource_group"]: row["budget_count"] for row in evidence["budgets"]}
    groups_by_rg = {row["resource_group"]: row["action_group_count"] for row in evidence["action_groups"]}
    for rg in evidence["resource_groups"]:
        name = rg["resource_group"]
        budget = "Yes" if budgets_by_rg.get(name, 0) > 0 else "No"
        action_group = "Yes" if groups_by_rg.get(name, 0) > 0 else "No"
        lines.append(
            f"| {name} | {rg['total_resources']} | {rg['tag_compliant_resources']} | {budget} | {action_group} |"
        )
    lines.extend([
        "",
        "Resource-type breakdown, required-role guidance, and manual budget/alert "
        "setup are documented in "
        "[docs/agentic-sdlc.md > Cost governance](https://github.com/sahad7793/agetic-sdlc-demo/blob/main/docs/agentic-sdlc.md#cost-governance).",
    ])
    return "\n".join(lines) + "\n"


def build_report(evidence):
    return {
        "schema_version": evidence["schema_version"],
        "collected_at": evidence["collected_at"],
        "required_tags": evidence["required_tags"],
        "tag_compliance_percent": tag_compliance_percent(evidence),
        "resource_groups": evidence["resource_groups"],
        "budgets": evidence["budgets"],
        "action_groups": evidence["action_groups"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect_parser = subparsers.add_parser("collect", help="Read-only, non-monetary collection")
    collect_parser.add_argument("--output", type=Path, required=True)
    collect_parser.add_argument(
        "--resource-group", dest="resource_groups", action="append",
        help="Repeatable. Defaults to the six documented rg-taskmanagement-* groups.",
    )
    args = parser.parse_args()
    try:
        resource_groups = args.resource_groups or list(DEFAULT_RESOURCE_GROUPS)
        require(bool(resource_groups), "At least one resource group is required")
        az = AzureCli()
        collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        evidence = collect(az, resource_groups, collected_at)
        report = build_report(evidence)
        args.output.mkdir(parents=True, exist_ok=True)
        for name, content in (
            ("evidence.json", json.dumps(evidence, indent=2) + "\n"),
            ("report.json", json.dumps(report, indent=2) + "\n"),
            ("report.md", render(report)),
        ):
            (args.output / name).write_text(content, encoding="utf-8")
        print(f"Read-only, non-monetary report written to {args.output}")
    except (AzCliError, ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        print(f"Cost governance report failed: {error}", file=sys.stderr)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as file:
                file.write(f"### Cost governance report failed\n\n{error}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
