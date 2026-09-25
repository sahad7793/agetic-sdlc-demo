#!/usr/bin/env python3
"""Produce truthful, human-controlled draft release notes.

This is deliberately deterministic (no LLM, no heuristics of our own): the release
notes body comes entirely from GitHub's own `releases/generate-notes` API, which
computes them from merged pull request metadata (labels, titles, authors) using the
categories in `.github/release.yml`. This script never invents content.

Nothing here creates a git tag or a published GitHub Release. `generate`/`summary`
are pure read/compute steps. `draft`/`draft-summary` (run only when an operator
explicitly opts in) save or update a **draft** Release object; GitHub does not
create the underlying tag until a human opens it and clicks "Publish release".
"""

from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys


REF_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def require_ref(value, field):
    """Reject anything but a nonblank, whitespace-free, non-flag-like ref token.

    tag_name/target_commitish are used both as a `gh api` JSON body value and as
    `gh release` command arguments, so a leading '-' (flag injection) and any
    whitespace/control characters are rejected here regardless of call site.
    """
    require(bool(REF_PATTERN.fullmatch(value)), f"{field} must be a nonblank ref-like token "
            "(letters, digits, '.', '_', '-', '/' only; no leading '-'; max 200 characters).")


def command(*args, timeout=60, input_text=None):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                             input=input_text, check=False)
    require(result.returncode == 0, f"{args[0]} failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout


def save_audit(path, audit):
    path.write_text(json.dumps(audit, indent=2) + "\n")


def notes_path_for(audit_path):
    return audit_path.with_suffix(".notes.md")


def read_generate_inputs():
    tag_name = os.environ["INPUT_TAG_NAME"].strip()
    previous_tag_name = os.environ.get("INPUT_PREVIOUS_TAG_NAME", "").strip()
    target_commitish = os.environ.get("INPUT_TARGET_COMMITISH", "").strip() or "main"
    require_ref(tag_name, "tag_name")
    if previous_tag_name:
        require_ref(previous_tag_name, "previous_tag_name")
    require_ref(target_commitish, "target_commitish")
    return {"tag_name": tag_name, "previous_tag_name": previous_tag_name,
            "target_commitish": target_commitish}


def generate(path):
    audit = {"outcome": "No release notes generated; resolution incomplete",
              "tag_name": os.environ.get("INPUT_TAG_NAME", ""),
              "previous_tag_name": os.environ.get("INPUT_PREVIOUS_TAG_NAME", ""),
              "target_commitish": os.environ.get("INPUT_TARGET_COMMITISH", "") or "main",
              "actor": os.environ.get("ACTOR", ""),
              "run_url": os.environ.get("RUN_URL", "")}
    save_audit(path, audit)
    try:
        inputs = read_generate_inputs()
        audit.update(inputs)
        repository = os.environ["GH_REPOSITORY"]
        require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository), "Invalid repository.")

        body = {"tag_name": inputs["tag_name"], "target_commitish": inputs["target_commitish"]}
        if inputs["previous_tag_name"]:
            body["previous_tag_name"] = inputs["previous_tag_name"]
        output = command("gh", "api", "--method", "POST",
                          "-H", "Accept: application/vnd.github+json",
                          f"repos/{repository}/releases/generate-notes",
                          "--input", "-", input_text=json.dumps(body))
        generated = json.loads(output)
        require(isinstance(generated.get("body"), str) and isinstance(generated.get("name"), str),
                "Malformed generate-notes response.")

        notes_file = notes_path_for(path)
        body_text = generated["body"]
        notes_file.write_text(body_text if body_text.endswith("\n") else body_text + "\n")

        audit.update({
            "outcome": "Succeeded: release notes generated (draft content only; no tag or "
                       "release created)",
            "generated_name": generated["name"],
            "notes_path": str(notes_file),
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        })
        save_audit(path, audit)
    except (ValueError, KeyError, subprocess.SubprocessError, OSError) as error:
        audit["error"] = str(error)
        audit["outcome"] = "Failed: " + audit["outcome"]
        save_audit(path, audit)
        raise


def summary(path):
    audit = json.loads(path.read_text()) if path.exists() else {
        "outcome": "Release notes generation did not complete; inspect job logs.",
        "tag_name": os.environ.get("INPUT_TAG_NAME", ""),
        "previous_tag_name": os.environ.get("INPUT_PREVIOUS_TAG_NAME", ""),
        "target_commitish": os.environ.get("INPUT_TARGET_COMMITISH", "") or "main",
        "actor": os.environ.get("ACTOR", ""),
    }
    lines = [
        "## Release notes (DRAFT \u2014 nothing published, no tag created)",
        "",
        "These notes are computed read-only from GitHub's own `releases/generate-notes` "
        "API and `.github/release.yml`. No git tag, GitHub Release, deployment, or Azure "
        "change was made by this step.",
        "",
    ]
    for key in ("outcome", "tag_name", "previous_tag_name", "target_commitish",
                "generated_name", "actor", "generated_at", "error"):
        value = str(audit.get(key) or "Not resolved / not applicable")
        lines.append(f"<p><strong>{key}</strong></p><pre>{html.escape(value)}</pre>")
    notes_file = notes_path_for(path)
    if notes_file.exists():
        lines += ["", "<details><summary>Generated release notes (verbatim)</summary>", "",
                   notes_file.read_text(), "</details>"]
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as output:
        output.write("\n".join(lines) + "\n")


def release_exists(tag_name):
    try:
        command("gh", "release", "view", tag_name, "--json", "tagName")
        return True
    except ValueError:
        return False


def draft(path):
    audit = json.loads(path.read_text())
    require(audit.get("outcome", "").startswith("Succeeded"),
            "Cannot draft a release without successfully generated notes.")
    try:
        tag_name = audit["tag_name"]
        target_commitish = audit["target_commitish"]
        notes_file = notes_path_for(path)
        require(notes_file.exists(), "Missing generated notes file.")
        title = audit.get("generated_name") or tag_name

        existing = release_exists(tag_name)
        args = ["gh", "release", "edit" if existing else "create", tag_name,
                "--draft", "--title", title, "--notes-file", str(notes_file)]
        if not existing:
            args += ["--target", target_commitish]
        command(*args)

        view = json.loads(command("gh", "release", "view", tag_name, "--json",
                                   "url,isDraft,tagName"))
        require(view["isDraft"], "Release was not saved as a draft; refusing to report success.")
        audit.update({
            "draft_outcome": "Succeeded: draft release saved (still requires a human "
                              "'Publish release' click to go live)",
            "draft_url": view["url"],
        })
        save_audit(path, audit)
    except (ValueError, KeyError, subprocess.SubprocessError, OSError) as error:
        audit["draft_error"] = str(error)
        audit["draft_outcome"] = "Failed: draft release not saved"
        save_audit(path, audit)
        raise


def draft_summary(path):
    audit = json.loads(path.read_text()) if path.exists() else {}
    lines = ["## Draft release", ""]
    if audit.get("draft_url"):
        lines += [
            f"A **draft** GitHub Release was saved: {audit['draft_url']}",
            "",
            "It is **not public** and no git tag exists yet. A maintainer must open it on "
            "GitHub and explicitly click **Publish release** to make it public and create "
            "the tag.",
        ]
    else:
        lines.append(str(audit.get("draft_outcome") or audit.get("draft_error") or
                          "Draft release creation was skipped (not requested) or did not "
                          "complete; see the job logs."))
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as output:
        output.write("\n".join(lines) + "\n")


SUBCOMMANDS = {"generate": generate, "summary": summary, "draft": draft,
               "draft-summary": draft_summary}


if __name__ == "__main__":
    try:
        require(len(sys.argv) == 3 and sys.argv[1] in SUBCOMMANDS,
                "Usage: release_notes.py generate|summary|draft|draft-summary AUDIT_JSON")
        SUBCOMMANDS[sys.argv[1]](Path(sys.argv[2]))
    except (ValueError, KeyError, subprocess.SubprocessError, OSError) as error:
        print(f"Release notes error: {error}", file=sys.stderr)
        sys.exit(1)
