#!/usr/bin/env python3
"""Validate that a pull request links an issue or documents an exception."""

import os
import re
import sys

if __package__:
    from .issue_references import extract_issue_references, HTML_COMMENT
else:
    from issue_references import extract_issue_references, HTML_COMMENT


NO_ISSUE_LINE = re.compile(r"^\s*No-Issue\s*:\s*(?P<reason>\S(?:.*\S)?)\s*$", re.IGNORECASE)
DEPENDABOT = "dependabot[bot]"


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
    return 0


if __name__ == "__main__":
    sys.exit(main())
