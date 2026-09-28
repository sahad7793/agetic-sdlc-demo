#!/usr/bin/env python3
"""Validate that a pull request links an issue or documents an exception."""

import json
import os
import re
import sys
import urllib.error
import urllib.request

if __package__:
    from .issue_references import extract_issue_references, HTML_COMMENT
else:
    from issue_references import extract_issue_references, HTML_COMMENT


NO_ISSUE_LINE = re.compile(r"^\s*No-Issue\s*:\s*(?P<reason>\S(?:.*\S)?)\s*$", re.IGNORECASE)
DEPENDABOT = "dependabot[bot]"
PLAN_APPROVED_LABEL = "stage:plan-approved"
API_VERSION = "2022-11-28"


class IssueMetadataError(Exception):
    """Raised when GitHub issue metadata cannot be read or understood."""


def get_issue_metadata(repository, issue_number, token, api_url):
    """Read issue state and labels without changing GitHub data."""
    url = f"{api_url.rstrip('/')}/repos/{repository}/issues/{issue_number}"
    request = urllib.request.Request(url, method="GET")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", API_VERSION)
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise IssueMetadataError(f"GitHub returned HTTP {error.code}.") from error
    except urllib.error.URLError as error:
        raise IssueMetadataError("Could not reach the GitHub API.") from error
    except TimeoutError as error:
        raise IssueMetadataError("The GitHub API request timed out.") from error
    except OSError as error:
        raise IssueMetadataError("Could not read issue metadata from GitHub.") from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise IssueMetadataError("GitHub returned invalid issue metadata.") from error

    if not isinstance(payload, dict):
        raise IssueMetadataError("GitHub returned an invalid issue response.")
    state = payload.get("state")
    labels = payload.get("labels")
    if state not in ("open", "closed") or not isinstance(labels, list):
        raise IssueMetadataError("GitHub issue metadata is missing its state or labels.")
    if any(not isinstance(label, dict) or not isinstance(label.get("name"), str)
           for label in labels):
        raise IssueMetadataError("GitHub returned an invalid issue label.")
    return state, {label["name"] for label in labels}


def report_plan_approval_status(body, repository, token, api_url, output=None,
                                summary_path=None, fetch_issue=get_issue_metadata):
    """Report plan-approval status as advisory-only annotations and a job summary."""
    if output is None:
        output = sys.stdout
    current_repository = (repository or "").casefold()
    references = extract_issue_references(body, default_repository=repository)
    results = []

    for linked_repository, issue_number in references:
        issue_ref = f"{linked_repository or 'unknown repository'}#{issue_number}"
        if not linked_repository:
            result = (issue_ref, "Unable to verify", None,
                      "The repository for this issue reference is unavailable.")
        elif linked_repository == current_repository and not token:
            result = (issue_ref, "Unable to verify", None,
                      "The read-only GitHub token is unavailable.")
        else:
            try:
                state, labels = fetch_issue(
                    linked_repository,
                    issue_number,
                    token if linked_repository == current_repository else None,
                    api_url,
                )
            except IssueMetadataError as error:
                result = (issue_ref, "Unable to verify", None, str(error))
            else:
                approved = PLAN_APPROVED_LABEL in labels
                status = "Approved" if approved else "Not approved"
                result = (issue_ref, f"{status} ({state})", approved, None)
        results.append(result)

    if not results:
        return results

    for issue_ref, status, approved, error in results:
        if approved:
            print(
                f"::notice title=Plan approval status::{issue_ref} has "
                f"{PLAN_APPROVED_LABEL} ({status}).",
                file=output,
            )
        elif error:
            print(
                f"::warning title=Plan approval status::{issue_ref} could not be "
                f"verified: {error} Review the issue and confirm the human "
                f"{PLAN_APPROVED_LABEL} gate.",
                file=output,
            )
        else:
            print(
                f"::warning title=Plan approval status::{issue_ref} is missing "
                f"{PLAN_APPROVED_LABEL} ({status}). Ask a maintainer to review "
                "the plan and apply the label after approval; this warning does "
                "not block the pull request.",
                file=output,
            )

    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as summary:
                summary.write("## Linked issue plan approval\n\n")
                summary.write("| Issue | Plan approval status |\n")
                summary.write("| --- | --- |\n")
                for issue_ref, status, _, _ in results:
                    summary.write(f"| `{issue_ref}` | {status} |\n")
                summary.write("\n")
        except OSError as error:
            print(
                f"::warning title=Plan approval summary::Could not write the "
                f"linked issue summary: {error}. See workflow annotations.",
                file=output,
            )
    return results


def validate_pull_request_body(body, author):
    """Return an error message unless the author/body contains an allowed reference."""
    if author.casefold() == DEPENDABOT:
        return None

    visible_body = HTML_COMMENT.sub("", body or "")
    if extract_issue_references(visible_body):
        return None

    for line in visible_body.splitlines():
        match = NO_ISSUE_LINE.fullmatch(line)
        if match and match.group("reason").casefold() != "<reason>":
            return None

    return (
        "Pull request body must reference an issue (for example, 'Closes #123' "
        "or a GitHub issue URL), or include a 'No-Issue: <brief reason>' line."
    )


def main():
    body = os.environ.get("PR_BODY") or ""
    author = os.environ.get("PR_AUTHOR") or ""
    error = validate_pull_request_body(body, author)
    if error:
        print(error, file=sys.stderr)
        return 1
    print("Pull request issue-link requirement satisfied.")
    report_plan_approval_status(
        body,
        os.environ.get("GITHUB_REPOSITORY"),
        os.environ.get("GITHUB_TOKEN"),
        os.environ.get("GITHUB_API_URL", "https://api.github.com"),
        summary_path=os.environ.get("GITHUB_STEP_SUMMARY"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
