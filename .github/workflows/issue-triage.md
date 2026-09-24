---
on:
  issues:
    types: [opened]

permissions:
  contents: read
  issues: read

engine: copilot

safe-outputs:
  add-comment:
    max: 1

---

# Issue Triage

Analyze the newly opened issue and post one helpful triage comment. Do not
close, label, assign, or otherwise mutate the issue — this workflow is
advisory only.

## Instructions

1. Read the new issue's title and body.
2. Compare the body against the fields defined in
   `.github/ISSUE_TEMPLATE/bug_report.yml` and
   `.github/ISSUE_TEMPLATE/feature_request.yml`. Determine which template
   (if either) the issue most likely corresponds to, and note whether any
   fields that template marks as required appear to be missing or left
   blank in the issue body.
3. Classify the issue as most likely a **bug**, a **feature request**, or
   **unclear**, based on its content and how well it matches either
   template's structure.
4. Search open issues in this repository for likely duplicates of this
   issue (similar title or description). List any strong duplicate
   candidates you find, with their issue numbers.
5. If the issue is missing information needed to act on it (e.g. no
   repro steps for a bug, no rationale for a feature), draft exactly one
   clarifying question to ask the reporter. Skip this if the issue is
   already clear and complete.
6. Post a single comment on the issue summarizing:
   - Likely type: bug / feature / unclear
   - Missing required fields (if any), referencing the template used
   - Possible duplicates (if any), linked by issue number
   - One clarifying question (if any)

Keep the comment concise and factual. Do not speculate about implementation
details, do not assign priority, and do not suggest labels or a fix.
