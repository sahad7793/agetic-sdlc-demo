# Agentic SDLC workflow

This repository uses agents to accelerate implementation and review, while people retain approval and merge authority.

## Intended delivery flow

1. File a feature or bug using the structured issue forms. Include measurable acceptance criteria and the affected area.
2. A maintainer reviews, clarifies, and approves the issue before implementation begins.
3. Assign the approved issue to GitHub Copilot coding agent, or select **Open in Copilot** from the issue. The agent (or a human) works in an isolated branch and opens a pull request that links the issue.
4. CI runs restore, build, tests with coverage collection, and CodeQL analysis. Dependabot opens weekly update PRs for NuGet packages and GitHub Actions.
5. A human reviewer uses the PR template, code review, test results, and any advisory agent review to check architecture, validation, business rules, and regression coverage.
6. After required CI checks are green and a human approval is present, a human merges the PR. Agents never approve or merge pull requests by themselves.

## Delivery and deployment

`main` deployments are handled by the **Deploy** workflow only after the **CI** workflow has completed successfully. It uses GitHub Actions OIDC to build the API once in Azure Container Registry (ACR), captures the immutable image digest, deploys that digest to `staging`, and verifies the `/health` endpoint. The `production` job then deploys the **same** digest; it is blocked by the GitHub Environment required-reviewer rule.

| Environment | Azure location | Resource group | Deployment behavior |
| --- | --- | --- | --- |
| `staging` | Central US | `rg-taskmanagement-staging-centralus` | Automatic after CI |
| `production` | West US 2 | `rg-taskmanagement-production-westus2` | Requires approval from `sahad7793` |

The environments have independent Container Apps environments/apps, user-assigned managed identities, Entra-only Azure SQL logical servers, and databases. They share ACR in East US 2 so that production can only receive the staging-validated image digest. Each Container App identity has `AcrPull` on the registry and is granted SQL data-plane access (`db_datareader`, `db_datawriter`, and `db_ddladmin` for EF migrations) by `scripts/grant-sql-access.sh`. SQL password authentication is disabled; the API uses `Authentication=Active Directory Managed Identity` with the user-assigned identity.

### Operator configuration

The following GitHub Environment variables are required for both `staging` and `production`: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_LOCATION`, `AZURE_RESOURCE_GROUP`, `ACR_NAME`, `ACR_LOGIN_SERVER`, `CONTAINER_APP_NAME`, and `MANAGED_IDENTITY_ID`. The Entra application registrations have federated credentials constrained to `repo:sahad7793/agetic-sdlc-demo:environment:staging` and `repo:sahad7793/agetic-sdlc-demo:environment:production`; no client secret is used.

The production approval gate is configured in **Settings > Environments > production**. Its required reviewer must remain enabled; do not deploy production directly with Azure CLI because that bypasses the approval audit trail.

## Getting started for the repository owner

### 1. Enable security features

Open **Settings > Code security and analysis** for this repository and enable Dependabot alerts and secret scanning. Consider enabling push protection where your plan supports it. These controls are repository/account decisions and are intentionally not changed by this scaffold.

### 2. Protect `main`

This repository uses a repository ruleset (**Settings > Rules > Rulesets**) named "Main Branch Protection" targeting `refs/heads/main`, enforcing:

- Require a pull request before merging, with at least one approving review.
- Dismiss stale approvals when new commits are pushed.
- Require the CI workflow's **Build, test, and analyze** status check to pass, using the up-to-date/strict policy.
- Block force pushes and branch deletion.

**Bypass policy:** repository admins may bypass this ruleset (`bypass_actors: RepositoryRole "admin"`, mode `always`). This lets the solo repository owner merge their own changes (e.g. dependency policy fixes) without waiting on a second approver, since GitHub does not allow self-approval of your own pull request. Implementation agents and other collaborators are not granted bypass, so their pull requests always require a human's approving review and a green CI run before merge.

### 3. Enable GitHub Copilot coding agent

Enable GitHub Copilot coding agent for the organization and this repository according to your Copilot plan's policies. Confirm it can create branches and pull requests but cannot bypass protection rules.

### 4. Hand off an approved issue

Open the approved issue, assign it to **Copilot**, or use **Open in Copilot**. Include the intended scope and acceptance criteria. Review the resulting plan and pull request like any other contributor change; the agent's work does not replace human review.

### 5. Workflow orchestration

This repository runs two [GitHub Agentic Workflows](https://github.github.com/gh-aw/) (`gh-aw`), configured under `.github/workflows/`: an issue-triage workflow that comments on newly opened issues, and a weekly report workflow that opens a summary issue. Both are advisory only — they cannot self-approve, self-merge, or mutate issues without review. See [agentic-workflows.md](agentic-workflows.md) for what each workflow does and the `COPILOT_GITHUB_TOKEN` repository secret the owner must add before they can run.
