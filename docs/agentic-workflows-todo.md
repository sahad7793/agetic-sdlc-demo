# Deferred `gh-aw` workflows

This repository intentionally does not include executable GitHub Agentic Workflow (`gh-aw`) files yet. `gh-aw` workflow front matter and permissions are actively evolving, and committing guessed syntax would create a non-working example.

After the repository owner installs and verifies the extension (`gh extension install githubnext/gh-aw` or the current upstream installation method), add workflows using the exact version-compatible authoring format for:

1. **Issue triage and refinement** — inspect a newly opened feature or bug issue; post a comment proposing clarified acceptance criteria and suggested labels. It must not close, label, assign, or otherwise mutate the issue without human review.
2. **PR risk review** — inspect pull request changes and post a concise risk-area summary. It must not approve, merge, or bypass required human review.

Before enabling either workflow, review generated permissions, event triggers, model access, and the extension's dry-run or validation command. Keep their output advisory and human-gated.
