#!/usr/bin/env python3
"""Route issue lifecycle labels and synchronize their definitions."""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LABELS_PATH = ROOT / ".github" / "lifecycle-labels.json"
APPROVAL_STAGES = {"stage:spec-approved", "stage:plan-approved"}
WRITE_PERMISSIONS = {"write", "maintain", "admin"}
API_VERSION = "2022-11-28"


@dataclass(frozen=True)
class Transition:
    valid: bool
    reason: str
    allowed_stages: tuple = ()


def load_label_definitions():
    """Read the lifecycle stage labels and their routing owners."""
    with LABELS_PATH.open(encoding="utf-8") as labels_file:
        definitions = json.load(labels_file)
    labels = definitions.get("labels")
    if not isinstance(labels, list) or not labels:
        raise ValueError("Lifecycle label definitions must contain a non-empty labels list.")
    names = [label.get("name") for label in labels]
    if any(not isinstance(name, str) or not name.startswith("stage:") for name in names):
        raise ValueError("Every lifecycle label name must start with 'stage:'.")
    if len(names) != len(set(names)):
        raise ValueError("Lifecycle label names must be unique.")
    return labels


def is_bot_actor(actor):
    """Identify GitHub and Copilot bot logins without trusting display names."""
    login = (actor or "").strip().casefold()
    return (
        login.endswith("[bot]")
        or login == "github-actions"
        or login == "copilot"
    )


def allowed_next_stages(current_stage, stage_names):
    """Return valid next labels, including a return to intake when applicable."""
    ordered_stages = [name for name in stage_names if name != "stage:needs-spec"]
    if current_stage is None:
        return ("stage:needs-spec",)
    if current_stage not in stage_names:
        return ()

    stage_index = ordered_stages.index(current_stage) if current_stage in ordered_stages else -1
    next_stages = []
    if stage_index + 1 < len(ordered_stages):
        next_stages.append(ordered_stages[stage_index + 1])
    if current_stage != "stage:needs-spec":
        next_stages.append("stage:needs-spec")
    return tuple(next_stages)


def decide_transition(current_stages, requested_stage, actor, permission, stage_names):
    """Validate an attempted stage change and its human approval gate."""
    if requested_stage not in stage_names:
        return Transition(False, "unknown-stage")
    if len(current_stages) > 1:
        return Transition(False, "multiple-current-stages",
                          allowed_next_stages(None, stage_names))

    current_stage = current_stages[0] if current_stages else None
    allowed = allowed_next_stages(current_stage, stage_names)
    if requested_stage not in allowed:
        return Transition(False, "invalid-transition", allowed)
    if requested_stage in APPROVAL_STAGES:
        if is_bot_actor(actor):
            return Transition(False, "approval-by-bot", allowed)
        if permission == "unavailable":
            return Transition(False, "approval-permission-unavailable", allowed)
        if (permission or "").casefold() not in WRITE_PERMISSIONS:
            return Transition(False, "approval-without-write-permission", allowed)
    return Transition(True, "accepted", allowed)


class GitHubApi:
    """Small GitHub REST client for the narrowly scoped issue and label writes."""

    def __init__(self, token, repository, api_url="https://api.github.com"):
        self.token = token
        self.repository = repository
        self.api_url = api_url.rstrip("/")

    def request(self, method, path, payload=None, missing_ok=False, forbidden_ok=False):
        url = f"{self.api_url}{path}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("X-GitHub-Api-Version", API_VERSION)
        if data is not None:
            request.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                content = response.read()
                return json.loads(content) if content else None
        except urllib.error.HTTPError as error:
            if missing_ok and error.code == 404:
                return None
            if forbidden_ok and error.code == 403:
                return {"permission_check_unavailable": True}
            details = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"GitHub API {method} {path} failed with HTTP {error.code}: {details}"
            ) from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"GitHub API {method} {path} failed: {error.reason}") from error

    def issue(self, issue_number):
        return self.request("GET", f"/repos/{self.repository}/issues/{issue_number}")

    def collaborator_permission(self, actor):
        encoded_actor = urllib.parse.quote(actor, safe="")
        response = self.request(
            "GET",
            f"/repos/{self.repository}/collaborators/{encoded_actor}/permission",
            missing_ok=True,
            forbidden_ok=True,
        )
        if response and response.get("permission_check_unavailable"):
            return "unavailable"
        return response.get("permission") if response else None

    def remove_issue_label(self, issue_number, label):
        encoded_label = urllib.parse.quote(label, safe="")
        self.request(
            "DELETE",
            f"/repos/{self.repository}/issues/{issue_number}/labels/{encoded_label}",
        )

    def comment(self, issue_number, body):
        self.request(
            "POST",
            f"/repos/{self.repository}/issues/{issue_number}/comments",
            {"body": body},
        )

    def labels(self):
        labels = []
        page = 1
        while True:
            page_labels = self.request(
                "GET", f"/repos/{self.repository}/labels?per_page=100&page={page}"
            )
            labels.extend(page_labels)
            if len(page_labels) < 100:
                return labels
            page += 1

    def create_label(self, label):
        self.request("POST", f"/repos/{self.repository}/labels", label)

    def update_label(self, label):
        encoded_name = urllib.parse.quote(label["name"], safe="")
        self.request("PATCH", f"/repos/{self.repository}/labels/{encoded_name}", {
            "color": label["color"],
            "description": label["description"],
        })


def _stage_labels(issue_labels):
    return [
        label["name"]
        for label in issue_labels
        if label.get("name", "").startswith("stage:")
    ]


def _permission_for_approval(api, requested_stage, actor):
    if requested_stage not in APPROVAL_STAGES or is_bot_actor(actor):
        return None
    return api.collaborator_permission(actor)


def _rejection_comment(requested_stage, decision):
    if decision.reason == "approval-by-bot":
        return (
            f"`{requested_stage}` is a human approval gate. Bot accounts cannot "
            "approve it; the attempted label was removed."
        )
    if decision.reason == "approval-without-write-permission":
        return (
            f"`{requested_stage}` is a human approval gate and requires repository "
            "write, maintain, or admin permission. The attempted label was removed."
        )
    if decision.reason == "approval-permission-unavailable":
        return (
            f"`{requested_stage}` is a human approval gate, but the router could not "
            "verify repository permission through the collaborator API. The attempted "
            "label was removed; a maintainer must resolve the workflow token's API "
            "permission before this gate can advance."
        )
    if decision.reason == "multiple-current-stages":
        return (
            "Multiple lifecycle labels were already present, so this transition "
            "was rejected. Keep one `stage:*` label at a time; the attempted label "
            "was removed."
        )
    allowed = ", ".join(f"`{stage}`" for stage in decision.allowed_stages) or "none"
    return (
        f"`{requested_stage}` is not a valid next stage. Allowed next stage(s): "
        f"{allowed}. The attempted label was removed."
    )


def route_issue(api, event, labels):
    """Apply or reject the newly added lifecycle label using current issue state."""
    issue_number = event["issue"]["number"]
    requested_stage = event["label"]["name"]
    stage_names = tuple(label["name"] for label in labels)
    if not requested_stage.startswith("stage:"):
        return "ignored"
    if requested_stage not in stage_names:
        api.remove_issue_label(issue_number, requested_stage)
        api.comment(
            issue_number,
            f"`{requested_stage}` is not a configured lifecycle stage; the label was removed.",
        )
        return "rejected"

    current_issue = api.issue(issue_number)
    current_labels = current_issue.get("labels", [])
    if requested_stage not in {label.get("name") for label in current_labels}:
        return "stale-event"

    current_stages = [
        stage for stage in _stage_labels(current_labels) if stage != requested_stage
    ]
    actor = event.get("sender", {}).get("login", "")
    permission = _permission_for_approval(api, requested_stage, actor)
    decision = decide_transition(
        current_stages, requested_stage, actor, permission, stage_names
    )
    if not decision.valid:
        api.remove_issue_label(issue_number, requested_stage)
        api.comment(issue_number, _rejection_comment(requested_stage, decision))
        return "rejected"

    for previous_stage in current_stages:
        api.remove_issue_label(issue_number, previous_stage)

    owner = next(label["owner"] for label in labels if label["name"] == requested_stage)
    api.comment(
        issue_number,
        f"Lifecycle updated to `{requested_stage}`. Next owner: **{owner}**.",
    )
    return "accepted"


def sync_labels(api, labels):
    """Create missing labels and update existing lifecycle label metadata."""
    existing = {label["name"] for label in api.labels()}
    for label in labels:
        if label["name"] in existing:
            api.update_label(label)
            print(f"Updated {label['name']}")
        else:
            api.create_label({
                "name": label["name"],
                "color": label["color"],
                "description": label["description"],
            })
            print(f"Created {label['name']}")


def _api_from_environment():
    token = os.environ.get("GITHUB_TOKEN")
    repository = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repository:
        raise RuntimeError("GITHUB_TOKEN and GITHUB_REPOSITORY are required.")
    return GitHubApi(token, repository, os.environ.get("GITHUB_API_URL"))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        labels = load_label_definitions()
        api = _api_from_environment()
        if argv == ["sync-labels"]:
            sync_labels(api, labels)
            return 0
        if argv == ["route"]:
            event_path = os.environ.get("GITHUB_EVENT_PATH")
            if not event_path:
                raise RuntimeError("GITHUB_EVENT_PATH is required for routing.")
            with open(event_path, encoding="utf-8") as event_file:
                event = json.load(event_file)
            if event.get("action") != "labeled":
                raise RuntimeError("The lifecycle router only accepts labeled issue events.")
            result = route_issue(api, event, labels)
            print(f"Lifecycle label routing result: {result}.")
            return 0
        raise RuntimeError("Usage: lifecycle_router.py route|sync-labels")
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        print(f"Lifecycle router failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
