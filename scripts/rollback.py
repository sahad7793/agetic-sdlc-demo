#!/usr/bin/env python3
"""Image-only Container Apps rollback using retained, environment-specific Deploy evidence."""

from datetime import datetime
import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


JOBS = {"staging": "Build and deploy staging", "production": "Deploy production"}
STEPS = {
    "staging": "Deploy validated image to staging",
    "production": "Deploy the staging-validated image",
}
BUILD_STEP = "Build and publish immutable image"
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def command(*args, timeout=60):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    require(result.returncode == 0, f"{args[0]} failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout


def api(endpoint):
    return json.loads(command("gh", "api", endpoint))


def pages(endpoint, key):
    result = []
    page = 1
    while True:
        payload = api(f"{endpoint}?per_page=100&page={page}")
        rows = payload[key]
        require(isinstance(rows, list), f"Invalid GitHub page: {endpoint}")
        result.extend(rows)
        if len(rows) < 100:
            require(len(result) == payload["total_count"],
                    f"Incomplete or changing GitHub history: {endpoint}; retry.")
            require(len({row["id"] for row in result}) == len(result),
                    f"Duplicate GitHub history: {endpoint}; retry.")
            return result
        page += 1


def step_named(job, name):
    steps = [step for step in job["steps"] if step["name"] == name]
    require(len(steps) <= 1, f"Ambiguous step {name} in job {job['id']}")
    return steps[0] if steps else None


def history(repository, environment):
    root = f"repos/{repository}/actions"
    records = {}
    for run in pages(f"{root}/workflows/deploy.yml/runs", "workflow_runs"):
        # Only the trusted Deploy workflow's normal delivery events are evidence.
        if run["event"] != "workflow_run" or run["head_branch"] != "main":
            continue
        for attempt in range(1, run["run_attempt"] + 1):
            for job in pages(f"{root}/runs/{run['id']}/attempts/{attempt}/jobs", "jobs"):
                if job["name"] != JOBS[environment] or job["id"] in records:
                    continue
                step = step_named(job, STEPS[environment])
                if not step or step["conclusion"] == "skipped":
                    continue
                if step["status"] != "completed":
                    require(not step.get("started_at"),
                            "Another Deploy step is in progress; wait or cancel it before rollback.")
                    continue
                require(step.get("started_at") and step.get("completed_at"),
                        f"Missing deployment timing for job {job['id']}")
                records[job["id"]] = {
                    "job": job, "step": step, "run": run["id"], "attempt": attempt,
                    "start": timestamp(step["started_at"]), "end": timestamp(step["completed_at"]),
                    "success": job["conclusion"] == "success" and step["conclusion"] == "success",
                    "url": f"https://github.com/{repository}/actions/runs/{run['id']}/job/{job['id']}",
                }
    return sorted(records.values(), key=lambda record: record["start"], reverse=True)


def header_environment(log, step, command_prefix):
    """Read only the runner's command header during this exact step, not command output."""
    start, end = timestamp(step["started_at"]), timestamp(step["completed_at"])
    groups, group = [], None
    for raw in log.splitlines():
        match = re.match(r"^(\d{4}-\d\d-\d\dT\S+Z) (.*)$", ANSI.sub("", raw))
        if not match:
            continue
        when, line = timestamp(match[1]), match[2]
        if not start <= when <= end:
            continue
        if line.startswith("##[group]Run "):
            group = [line.removeprefix("##[group]Run ")]
        elif line == "##[endgroup]" and group is not None:
            if group[0].startswith(command_prefix):
                groups.append(group)
            group = None
        elif group is not None:
            group.append(line)
    require(len(groups) == 1, "Missing or ambiguous deployment runner header; use an explicit digest.")
    group = groups[0]
    require("env:" in group and any(line.startswith("shell: ") for line in group),
            "Missing runner environment header.")
    env = {}
    for line in group[group.index("env:") + 1:]:
        match = re.fullmatch(r"  ([A-Z_]+): (.*)", line)
        require(match is not None and match[1] not in env, "Malformed runner environment evidence.")
        env[match[1]] = match[2]
    return env, "\n".join(group[:group.index("env:")])


def pinned(image, registry):
    require(re.fullmatch(re.escape(registry) + r"/taskmanagement-api@sha256:[0-9a-f]{64}", image),
            "Expected a sha256 digest in the configured ACR taskmanagement-api repository.")
    return image


def image_evidence(record, repository, registry):
    if "image" not in record:
        log = command("gh", "api", "--allow-escape-sequences",
                      f"repos/{repository}/actions/jobs/{record['job']['id']}/logs")
        env, script = header_environment(log, record["step"], "az containerapp registry set")
        require('az containerapp update' in script and '--image "$IMAGE"' in script,
                "Unrecognized deployment command; cannot prove the deployed image.")
        require(env.get("ACR_LOGIN_SERVER") == registry, "Deployment registry does not match.")
        record["image"] = pinned(env.get("IMAGE", ""), registry)
        record["app"] = env.get("CONTAINER_APP_NAME")
        record["log"] = log
    return record["image"]


def previous_image(records, current, repository, registry, app):
    anchor = None
    for index, record in enumerate(records):
        image = image_evidence(record, repository, registry)
        require(record["app"] == app, "Deployment evidence refers to a different Container App.")
        if anchor is None:
            if image == current:
                anchor = record
            continue
        require(record["end"] < anchor["start"],
                "Deployment attempts overlap the current image's deployment; use an explicit digest.")
        if record["success"] and image != current:
            # Overlapping older jobs might actually have completed after this candidate.
            require(all(other["end"] < record["start"] for other in records[index + 1:]),
                    "Prior deployment ordering is ambiguous; use an explicit digest.")
            return image, record, anchor
    require(anchor is not None, "Current image is absent from retained Deploy evidence; use an explicit digest.")
    raise ValueError("No previous successful deployment of a different image exists in retained history.")


def explicit_image(value, repository, registry):
    prefix = f"{registry}/taskmanagement-api"
    if value.startswith("sha256:"):
        value = f"{prefix}@{value}"
    if "@" in value:
        return pinned(value, registry), None
    tag = value.removeprefix(f"{prefix}:")
    require(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag),
            "Use a digest, a tag, or a full reference in the configured ACR repository.")
    matches = []
    for record in history(repository, "staging"):
        build = step_named(record["job"], BUILD_STEP)
        if not build or build["conclusion"] != "success":
            continue
        image = image_evidence(record, repository, registry)
        env, script = header_environment(record["log"], build, 'az acr login')
        require('docker push "$ACR_LOGIN_SERVER/taskmanagement-api:$IMAGE_TAG"' in script,
                "Unrecognized tag publication evidence; use an explicit digest.")
        require(env.get("ACR_LOGIN_SERVER") == registry, "Build registry does not match.")
        if env.get("IMAGE_TAG") == tag:
            matches.append((image, record))
    require(matches, "Tag has no retained Deploy evidence; supply its verified digest instead.")
    require(len({image for image, _ in matches}) == 1,
            "Tag mapped to different digests across Deploy attempts; supply an explicit digest.")
    return matches[0]


def container_image(template):
    containers = template["containers"]
    require(len(containers) == 1 and containers[0]["name"] == "taskmanagement-api",
            "Rollback supports only the single taskmanagement-api container.")
    return containers[0]["image"]


def azure(*args, timeout=60):
    return command("az", "containerapp", *args, "--name", os.environ["CONTAINER_APP_NAME"],
                   "--resource-group", os.environ["AZURE_RESOURCE_GROUP"],
                   "--output", "json", timeout=timeout)


def live_state(deadline=None):
    def timeout():
        remaining = deadline - time.monotonic() if deadline is not None else 60
        require(remaining > 0, "Health verification deadline expired; no second rollback was attempted.")
        return min(60, remaining)

    app = json.loads(azure("show", timeout=timeout()))["properties"]
    require(app["configuration"]["activeRevisionsMode"] == "Single",
            "Rollback requires Single revision mode; no traffic configuration will be changed.")
    ingress = app["configuration"]["ingress"]
    fqdn = ingress["fqdn"]
    require(re.fullmatch(r"[a-zA-Z0-9.-]+", fqdn), "Invalid Container App ingress hostname.")
    ready = app.get("latestReadyRevisionName")
    revision = json.loads(azure("revision", "show", "--revision", ready,
                               timeout=timeout()))["properties"] if ready else None
    return {
        "desired": container_image(app["template"]),
        "latest": app.get("latestRevisionName"),
        "ready": ready,
        "serving": container_image(revision["template"]) if revision else None,
        "active": revision.get("active", False) if revision else False,
        "healthy": revision.get("healthState") == "Healthy" if revision else False,
        "provisioning": app.get("provisioningState"),
        "fqdn": fqdn,
        "traffic": ingress.get("traffic", []),
    }


def check_health(target, fqdn):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        state = live_state(deadline)
        require(state["desired"] == target and state["fqdn"] == fqdn,
                "Container App changed during verification; refusing to report success.")
        if (state["serving"] == target and state["latest"] == state["ready"]
                and state["active"] and state["healthy"] and state["provisioning"] == "Succeeded"):
            remaining = max(1, min(5, int(deadline - time.monotonic())))
            response = subprocess.run(
                ["curl", "--fail", "--silent", "--show-error", "--connect-timeout", "3",
                 "--max-time", str(remaining), "--output", os.devnull, f"https://{fqdn}/health"],
                capture_output=True, text=True, timeout=remaining + 2, check=False)
            if response.returncode == 0:
                return
            print(f"Health probe failed (curl {response.returncode}): {response.stderr}", file=sys.stderr)
        time.sleep(max(0, min(10, deadline - time.monotonic())))
    raise ValueError("Rollback image did not become ready and healthy within approximately two minutes. "
                     "No second rollback was attempted; inspect Container App revisions and logs.")


def save_audit(path, audit):
    path.write_text(json.dumps(audit, indent=2) + "\n")


def perform(path):
    audit = {"outcome": "No image update attempted; resolution incomplete",
             "environment": os.environ["TARGET_ENVIRONMENT"],
             "mode": os.environ["TARGET_MODE"], "requested": os.environ.get("TARGET_IMAGE", ""),
             "reason": os.environ["ROLLBACK_REASON"], "actor": os.environ["GITHUB_ACTOR"],
             "triggering_actor": os.environ.get("GITHUB_TRIGGERING_ACTOR", os.environ["GITHUB_ACTOR"])}
    save_audit(path, audit)
    try:
        environment = audit["environment"]
        require(environment in JOBS, "Choose staging or production.")
        require(audit["reason"].strip() and len(audit["reason"]) <= 2000,
                "A nonblank reason of at most 2000 characters is required.")
        repository, registry = os.environ["GITHUB_REPOSITORY"], os.environ["ACR_LOGIN_SERVER"]
        require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository), "Invalid repository.")
        require(re.fullmatch(r"[a-zA-Z0-9.-]+", registry), "Invalid registry hostname.")
        before = live_state()
        audit.update({"from": before["desired"], "serving_before": before["serving"],
                      "revision_before": before["ready"]})
        save_audit(path, audit)
        require(before["provisioning"] in {"Succeeded", "Failed", "Canceled"},
                "An Azure update is still in progress; wait before rollback.")
        if audit["mode"] == "previous successful deployment":
            require(not audit["requested"].strip(), "Leave image empty in previous-deployment mode.")
            target, source, anchor = previous_image(
                history(repository, environment), before["desired"], repository, registry,
                os.environ["CONTAINER_APP_NAME"])
            audit["anchor"] = anchor["url"]
        else:
            require(audit["mode"] == "explicit image", "Invalid rollback target mode.")
            target, source = explicit_image(audit["requested"].strip(), repository, registry)
        audit["to"] = target
        if source:
            audit.update({"source": source["url"], "source_attempt": source["attempt"]})
        save_audit(path, audit)
        require(target != before["desired"], "Target equals the current desired image; no rollback performed.")
        require(live_state() == before, "Container App changed during resolution; retry with fresh evidence.")
        audit["outcome"] = "Image update started; success not yet verified"
        save_audit(path, audit)
        azure("update", "--container-name", "taskmanagement-api", "--image", target, timeout=600)
        audit["outcome"] = "Image updated; health verification pending"
        save_audit(path, audit)
        check_health(target, before["fqdn"])
        audit["outcome"] = "Succeeded: target image is ready and /health passed"
        save_audit(path, audit)
    except (ValueError, KeyError, subprocess.SubprocessError, OSError) as error:
        audit["error"] = str(error)
        audit["outcome"] = "Failed: " + audit["outcome"]
        save_audit(path, audit)
        raise


def summary(path):
    audit = json.loads(path.read_text()) if path.exists() else {
        "outcome": "Rollback did not complete; inspect checkout/login/job logs.",
        "environment": os.environ["TARGET_ENVIRONMENT"], "mode": os.environ["TARGET_MODE"],
        "requested": os.environ.get("TARGET_IMAGE", ""), "reason": os.environ["ROLLBACK_REASON"],
        "actor": os.environ["GITHUB_ACTOR"],
        "triggering_actor": os.environ.get("GITHUB_TRIGGERING_ACTOR", os.environ["GITHUB_ACTOR"]),
    }
    lines = ["## Rollback audit", ""]
    for key in ("outcome", "environment", "mode", "requested", "from", "serving_before",
                "revision_before", "to", "source", "source_attempt", "anchor", "actor",
                "triggering_actor", "reason", "error"):
        value = str(audit.get(key) or "Not resolved / not applicable")
        lines.append(f"<p><strong>{key}</strong></p><pre>{html.escape(value)}</pre>")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as output:
        output.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    try:
        require(len(sys.argv) == 3 and sys.argv[1] in {"run", "summary"},
                "Usage: rollback.py run|summary AUDIT_JSON")
        (perform if sys.argv[1] == "run" else summary)(Path(sys.argv[2]))
    except (ValueError, KeyError, subprocess.SubprocessError, OSError) as error:
        print(f"Rollback error: {error}", file=sys.stderr)
        sys.exit(1)
