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

The following GitHub Environment variables are required for both `staging` and `production`: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_LOCATION`, `AZURE_RESOURCE_GROUP`, `ACR_NAME`, `ACR_LOGIN_SERVER`, `CONTAINER_APP_NAME`, and `MANAGED_IDENTITY_ID`. The Entra application registrations authenticate via OIDC federated credentials; no client secret is used.

> **⚠️ Federated credential subject format:** this account/organization uses GitHub Enterprise Managed Users, so GitHub renders OIDC subject claims with numeric IDs embedded — e.g. `repo:sahad7793@139941502/agetic-sdlc-demo@1383544386:environment:staging`, **not** the plain `repo:sahad7793/agetic-sdlc-demo:environment:staging` format documented in most GitHub OIDC guides. If the app registration or repository is ever recreated, re-check the *actual* subject GitHub presents (visible in the `azure/login` failure message: `AADSTS700213: No matching federated identity record found for presented assertion subject '<actual subject>'`) before configuring the federated credential, rather than assuming the theoretical format.

The production approval gate is configured in **Settings > Environments > production**. Its required reviewer must remain enabled; do not deploy production directly with Azure CLI because that bypasses the approval audit trail.

## Observability

Application Insights and Log Analytics were provisioned per environment from the start, but until this change nothing consumed the telemetry proactively — no alerts, no action groups, no dashboard. This section closes that gap with additive Bicep resources; no application code, deployment workflow logic, or existing SQL/identity setup was changed except one required wiring fix (below).

### What was added

- **Action groups** (`infra/observability.bicep`, one per environment) — email-based notification targets (`primary-oncall`) that all alert rules below fire into.
- **HTTP 5xx-rate alert** — fires when the Container App returns more than 5 HTTP 5xx responses within a 5-minute window (`Requests` metric, `statusCodeCategory=5xx`).
- **p95 latency alert** — fires when p95 request duration exceeds 1500 ms over a 15-minute window. Implemented as a log-based scheduled query rule (KQL over the Application Insights `requests` table) because the Container Apps platform `ResponseTime` metric only supports Average/Total/Maximum/Minimum aggregations, not percentiles.
- **Restart/replica spike alert** — fires when replicas restart more than 3 times within 15 minutes (`RestartCount` metric), an early indicator of crash-looping.
- **Availability alert** — a standard Application Insights availability web test hits `/health` every 5 minutes from 5 geographically distributed locations; the paired alert fires if 2 or more locations fail within a 5-minute window.
- **Cross-environment workbook** (`infra/workbook.bicep` + `infra/workbook-content.json`, deployed once into the shared resource group) — a single Azure Monitor Workbook summarizing request rate/failure rate, p95 latency, SQL dependency health, and replica/restart counts for staging and production side by side.

### Required wiring fix (in scope)

`APPLICATIONINSIGHTS_CONNECTION_STRING` was already set as a Container App environment variable by `infra/environment.bicep`, but no telemetry SDK ever read it, so the API emitted no request or dependency telemetry for the new alerts/workbook to observe. Added the `Azure.Monitor.OpenTelemetry.AspNetCore` NuGet package and a guarded `builder.Services.AddOpenTelemetry().UseAzureMonitor();` call in `Program.cs` (only wired when the connection string is configured, so local/test runs are unaffected). This auto-instruments ASP.NET Core requests and SqlClient dependencies.

### Where to review it

- **Alerts:** Azure Portal → resource group (`rg-taskmanagement-staging-centralus` or `rg-taskmanagement-production-westus2`) → **Alerts**, or **Monitor → Alerts** filtered to the resource group. Each alert rule name is prefixed with the Container App name (e.g. `task-api-stage-8a58968e-5xx-rate`).
- **Workbook/dashboard:** Azure Portal → `rg-taskmanagement-shared` → the `Microsoft.Insights/workbooks` resource, or **Monitor → Workbooks → Shared reports** in either environment's Application Insights resource.
- **Action groups:** Azure Portal → resource group → **Monitor → Alerts → Action groups**, named `<container-app-name>-ag`.

### Adding on-call contacts later

Edit the `emailReceivers` (or add `webhookReceivers` / SMS / voice / Teams receivers) in the action group resource inside `infra/observability.bicep`, then redeploy that module standalone against the target resource group, e.g.:

```bash
az deployment group create \
  --resource-group rg-taskmanagement-staging-centralus \
  --template-file infra/observability.bicep \
  --parameters location=centralus environmentName=staging \
    containerAppId=<id> containerAppName=<name> applicationInsightsId=<id> \
    healthCheckUrl=<url> alertEmail=<email>
```

Do not re-run `infra/environment.bicep` or `infra/shared.bicep` directly against a live environment — those templates default `containerImage` to a placeholder and would reset the running Container App. They exist to keep a from-scratch bootstrap (`scripts/provision-infrastructure.sh`) complete; deploy `observability.bicep`/`workbook.bicep` standalone for updates to already-running environments.

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
