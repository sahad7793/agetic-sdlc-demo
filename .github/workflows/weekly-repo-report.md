---
on:
  schedule: weekly on monday

permissions:
  contents: read
  issues: read
  pull-requests: read

engine: copilot

safe-outputs:
  create-issue:
    max: 1

---

# Weekly Repository Report

Summarize the last 7 days of repository activity and open a single report
issue. This workflow is advisory only — it never closes, merges, or labels
anything; it only opens one new issue containing the report.

## Instructions

1. Look at issues and pull requests opened, closed, or merged in this
   repository over the last 7 days.
2. Compile counts: issues opened, issues closed, pull requests opened,
   pull requests merged, pull requests still open.
3. Note any notable themes across that activity (e.g. recurring bug areas,
   a burst of feature requests, a large refactor).
4. List any Dependabot pull requests that are still open, with how long
   they've been open.
5. Check the most recent CI runs on the default branch (`main`) and note
   whether any have failed, and if so, which workflow/job and roughly when.
6. Open one new issue titled with the current date (e.g.
   "Weekly repository report — YYYY-MM-DD") whose body contains the
   summary above as clearly labeled sections: Activity Summary, Themes,
   Open Dependabot PRs, CI Status on main.

Keep the report factual and concise. Do not recommend or perform any
changes — only report what happened.
