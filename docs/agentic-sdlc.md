# Agentic SDLC workflow

This repository uses agents to accelerate implementation and review, while people retain approval and merge authority.

## Intended delivery flow

1. File a feature or bug using the structured issue forms. Include measurable acceptance criteria and the affected area.
2. A maintainer reviews, clarifies, and approves the issue before implementation begins.
3. Assign the approved issue to GitHub Copilot coding agent, or select **Open in Copilot** from the issue. The agent (or a human) works in an isolated branch and opens a pull request that links the issue.
4. CI runs restore, build, tests with coverage collection, and CodeQL analysis. Dependabot opens weekly update PRs for NuGet packages and GitHub Actions.
5. A human reviewer uses the PR template, code review, test results, and any advisory agent review to check architecture, validation, business rules, and regression coverage.
6. After required CI checks are green and a human approval is present, a human merges the PR. Agents never approve or merge pull requests by themselves.

## Getting started for the repository owner

### 1. Enable security features

Open **Settings > Code security and analysis** for this repository and enable Dependabot alerts and secret scanning. Consider enabling push protection where your plan supports it. These controls are repository/account decisions and are intentionally not changed by this scaffold.

### 2. Protect `main`

Open **Settings > Branches**, choose **Add branch protection rule**, and target `main`. Enable:

- Require a pull request before merging.
- Require at least one approving review.
- Dismiss stale approvals when new commits are pushed (recommended).
- Require status checks to pass before merging, then select the CI workflow's **Build, test, and analyze** check after it has run at least once.
- Require branches to be up to date before merging (recommended).

Do not grant bypass permissions to implementation agents. Repository owners should confirm this configuration manually because it controls production governance.

### 3. Enable GitHub Copilot coding agent

Enable GitHub Copilot coding agent for the organization and this repository according to your Copilot plan's policies. Confirm it can create branches and pull requests but cannot bypass protection rules.

### 4. Hand off an approved issue

Open the approved issue, assign it to **Copilot**, or use **Open in Copilot**. Include the intended scope and acceptance criteria. Review the resulting plan and pull request like any other contributor change; the agent's work does not replace human review.

## Future advisory workflows

See [agentic-workflows-todo.md](agentic-workflows-todo.md) for safe, intentionally deferred `gh-aw` workflow goals. They are advisory only and must never self-approve, self-merge, or mutate issues without review.
