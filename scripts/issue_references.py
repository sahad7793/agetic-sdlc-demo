"""Shared parsing for issue references in pull request bodies."""

import re


ISSUE_REFERENCE = re.compile(
    r"\b(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?|refs?|references?)\s*:?\s+"
    r"#(?P<number>[1-9]\d*)\b",
    re.IGNORECASE,
)
ISSUE_URL = re.compile(
    r"https?://github\.com/(?P<repository>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/"
    r"(?P<number>[1-9]\d*)(?:[/?#][^\s]*)?(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
HTML_COMMENT = re.compile(r"<!--.*?(?:-->|$)", re.DOTALL)


def extract_issue_references(body, default_repository=None):
    """Return unique (repository, issue number) references visible to the PR checker."""
    visible_body = HTML_COMMENT.sub("", body or "")
    references = {
        (default_repository.lower() if default_repository else None, int(match["number"]))
        for match in ISSUE_REFERENCE.finditer(visible_body)
    }
    references.update(
        (match["repository"].lower(), int(match["number"]))
        for match in ISSUE_URL.finditer(visible_body)
    )
    return sorted(references, key=lambda reference: (reference[0] or "", reference[1]))
