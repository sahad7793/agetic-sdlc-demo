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

## Rollback

Use **Actions > Rollback > Run workflow** on `main` after a bad release, failed
health check, or alert. This is an operator-triggered recovery workflow, not an
automatic response to alerts. It reuses an existing image: no build, Git revert,
infrastructure deployment, new secret, or identity change is involved.

Choose `staging` or `production`, select a target, and provide an incident reference
or explanation in `reason` (required, nonblank, at most 2000 characters):

| Target | Image input | Behavior |
| --- | --- | --- |
| `previous successful deployment` | Leave empty | Finds the most recent successful deployment of a **different image before the current image's deployment**, in the chosen environment's retained Deploy history. |
| `explicit image` | `sha256:<64 lowercase hex characters>` or the full configured ACR `taskmanagement-api@sha256:...` reference | Deploys exactly that digest. An operator must verify its suitability; this option does not require successful-deployment history. |
| `explicit image` | A tag, or the full configured ACR `taskmanagement-api:<tag>` reference | Resolves the tag using retained successful Build/publish evidence and its deployment digest, then deploys the digest, **not the mutable tag**. Unknown tags and tags rebuilt to different digests fail; supply a verified digest instead. |

For example, using the GitHub CLI:

```bash
gh workflow run rollback.yml --ref main \
  -f environment=staging \
  -f target='previous successful deployment' \
  -f reason='Incident 123: elevated HTTP 5xx after release'
```

For production, change `environment=production`. The job uses the **existing
`environment: production` required-reviewer gate** before login or any Azure
operation. Approve it in the Actions run; do not work around approval with a
direct Azure CLI update. Existing environment variables and `azure/login@v2`
provide OIDC authentication, including the existing numeric-ID subject
configuration described above. No additional ACR read permission is required for
tag resolution. Dispatches from other branches do not execute the rollback job.

### Selection and verification guarantees

`scripts/rollback.py` queries the exact `deploy.yml` workflow, all retained
attempts, and the named target-environment deployment jobs. It reads digest
references from the runner's deployment command environment header within that
step's timestamps. It does **not** infer images from a commit SHA, mutable current
tag, or an automatic GitHub deployment record. Staging success still counts when
the same run's production job is waiting, rejected, or failed. Reused jobs across
reruns count once.

The current desired Container App image anchors the search, even when that
deployment failed its health check. Successful older jobs must have completed
before the anchor deployment started. Same-image redeployments are skipped;
ambiguous/overlapping deployment ordering, missing or expired logs, and missing
predecessors fail clearly **without updating the app**. A previous rollback can
leave an older image current; the next automatic rollback searches before that
image's latest matching Deploy occurrence rather than picking a newer release again.
An image introduced outside Deploy requires an explicit digest if its provenance
cannot be found.

The workflow supports the existing single-container, Single-revision topology.
It records both the desired image and the ready revision's image because Azure
may keep the old revision serving after a failed update. It rechecks state just
before mutation, updates only the named container's image, then requires the
target digest to become the latest ready, active, healthy revision and polls
`/health` with bounded requests for approximately two minutes. A healthy old
revision is not accepted as successful rollback. Image provisioning has a separate
timeout; the whole job is bounded. API errors or failed verification fail the run,
and **never initiate a second automatic rollback**.

The run's **Summary > Rollback audit** records requested target, observed from/to
images, previously ready image/revision, source deployment job and attempt,
original actor, rerun actor, reason, and outcome/error. If resolution or login
fails, unresolved fields are explicitly marked rather than reported as success.
After a failure, inspect the summary and Container App revisions/logs before
retrying with corrected evidence or an explicit known-good digest.

### Operational limits

**This rolls back the container image only, not database schema, migrations,
data, configuration, secrets, or infrastructure.** An older image must remain
compatible with the current SQL schema; any startup migration behavior belongs to
that image and is not undone or suppressed by this workflow. Registry images must
still exist and remain pullable by the app's existing managed identity.

"Successful deployment" is pipeline evidence, not proof that a release is
incident-free. Historical production Deploy jobs do not run `/health`; Rollback
does run it for both environments. A passing health check does not verify every
business operation or guarantee that alerts have resolved. Logs deleted or expired
by GitHub retention cannot be recovered, and tags are resolved to recorded build
digests rather than their current registry meaning.

Deploy and Rollback share per-environment job concurrency groups and do not cancel
a running update. GitHub concurrency is **not FIFO** and a new pending job can
replace an older pending job. Review/cancel unwanted pending Deploy runs,
including production jobs awaiting approval, during incident handling: a later
normal deployment can replace the rolled-back image. This does not freeze
releases. Azure operations outside these workflows are not covered by the lock;
the pre-update drift check reduces, but cannot atomically eliminate, that race.
Runs already started from an older Deploy workflow version also lack the new
lock and should be allowed to finish or cancelled before rollback.

The separate `Rollback` workflow and `Rollback <environment> image` job are
recovery evidence for future metrics, **not regular deployments**. The SDLC
metrics collector intentionally queries only `deploy.yml` for delivery
reliability/frequency; rollback runs do not change those denominators. No new
dashboard fields are added by this change.

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

### Validated end-to-end

Beyond confirming the deployments succeeded, the availability alert was proven to actually fire and auto-resolve: staging's Container App revision was briefly deactivated (no image/config change), which made 4 of 5 availability-test locations fail real `/health` checks. The `task-api-stage-8a58968e-health-test-alert` transitioned to `Fired` in the Azure Alerts Management API within minutes, and back to `Resolved` once the revision was reactivated and `/health` started returning `200` again. Live request telemetry (via OpenTelemetry) and the workbook's resource-ID substitution were also verified against the deployed resources, not just the Bicep templates. See `.azure/deployment-plan.md` for full timestamps and query details.

The alert *rule* firing correctly does not by itself guarantee a human receives an email — that dispatch step was validated separately. Initial test notifications to the email receiver failed silently (`BadRequest: There are no valid receivers in the request`) because of the OTP-verification requirement described below; a webhook receiver was temporarily added to prove the action-group dispatch pipeline itself worked (confirmed via a real HTTP POST from Azure's `IcMBroadcaster` service), then removed once the underlying email OTP verification was completed and confirmed working on both staging and production via `az monitor action-group test-notifications create`.

### Where to review it

- **Alerts:** Azure Portal → resource group (`rg-taskmanagement-staging-centralus` or `rg-taskmanagement-production-westus2`) → **Alerts**, or **Monitor → Alerts** filtered to the resource group. Each alert rule name is prefixed with the Container App name (e.g. `task-api-stage-8a58968e-5xx-rate`).
- **Workbook/dashboard:** Azure Portal → `rg-taskmanagement-shared` → the `Microsoft.Insights/workbooks` resource, or **Monitor → Workbooks → Shared reports** in either environment's Application Insights resource.
- **Action groups:** Azure Portal → resource group → **Monitor → Alerts → Action groups**, named `<container-app-name>-ag`.

### Adding on-call contacts later

Edit the `emailReceivers` (or add `webhookReceivers` / SMS / voice / Teams receivers) in the action group resource inside `infra/observability.bicep`, then redeploy that module standalone against the target resource group, e.g.:

> **⚠️ New email receivers require manual OTP verification.** Azure Monitor now enforces one-time-passcode (OTP) verification for action-group email receivers. An email address added via Bicep/ARM does **not** automatically receive or accept notifications — Azure silently drops both real alert emails and test notifications (`az monitor action-group test-notifications create ... -a email ...` returns `BadRequest: There are no valid receivers in the request`) until a human opens the verification email Azure sends and clicks the link (or completes it in the Portal via **Action group → Email receiver → Verify**). This is easy to miss because the receiver's `status` field still shows `Enabled` — that flag is unrelated to OTP-verification state. Verification persists tenant-wide: once an address is verified for one action group, it is automatically verified for every other action group in the same tenant (confirmed in this repo — verifying `sahad@saasberrylabs.com` on staging's action group also unblocked production's identical receiver with no extra step). If you don't receive alert emails after adding a new address, first confirm delivery with `az monitor action-group test-notifications create --action-group <ag-name> -g <rg> --alert-type webtestalert -a email <receiver-name> <address> usecommonalertschema` and check the Portal for a pending verification prompt before assuming the Bicep wiring is wrong.

```bash
az deployment group create \
  --resource-group rg-taskmanagement-staging-centralus \
  --template-file infra/observability.bicep \
  --parameters location=centralus environmentName=staging \
    containerAppId=<id> containerAppName=<name> applicationInsightsId=<id> \
    healthCheckUrl=<url> alertEmail=<email>
```

Do not re-run `infra/environment.bicep` or `infra/shared.bicep` directly against a live environment — those templates default `containerImage` to a placeholder and would reset the running Container App. They exist to keep a from-scratch bootstrap (`scripts/provision-infrastructure.sh`) complete; deploy `observability.bicep`/`workbook.bicep` standalone for updates to already-running environments.

## SDLC metrics dashboard

The [SDLC metrics dashboard](https://github.com/sahad7793/agetic-sdlc-demo/issues/28)
is the single reporting issue. Its body shows the latest observation; dated comments
preserve the original snapshot for each reporting window. The **SDLC Metrics**
workflow (`.github/workflows/sdlc-metrics.yml`) runs Mondays at 08:30 UTC or on
manual dispatch from the default branch. GitHub may delay scheduled runs.

This deterministic Python/GitHub Actions report complements the narrative gh-aw
weekly repository report; it does not use an LLM to calculate numbers or require
a Copilot token, Azure credentials, external dashboard, Pages setup, or custom PAT.
It changes neither application code nor deployment gates.

### Reporting windows and baseline

The current window is the last **seven complete UTC days**, ending at midnight
on the collection date. The previous window is the immediately preceding seven
days. Both are half-open `[start, end)`: an event at the end belongs to the next
period. Manual runs use the same rule, not a rolling 168 hours ending at dispatch.
Reports record repository creation, collection start/end, collector commit, schema
version, observed hours, sample counts, and explicit source availability.

The repository was created on September 23, 2026. The initial
[baseline report](metrics/baseline.md) and [structured snapshot](metrics/baseline.json)
therefore cover only the available early history, not a pre-agentic control period.
The separate initial-baseline section includes activity up to collection start,
including the current partial day. Weekly comparisons still exclude that day.
Periods preceding repository creation are displayed as unavailable comparisons,
not evidence of zero productivity. Per-day deployment rates are withheld for partial
periods and the initial baseline. Empty duration samples and zero-denominator rates
are `N/A`, never a fabricated zero-hour duration or 100% reliability.

### Metric dictionary

| Metric | Population, numerator, denominator | Exclusions and limits |
| --- | --- | --- |
| PR cycle time | Main-target PRs merged within the window; median elapsed hours from PR creation to merge, with sample count. | Includes draft/review waiting. Excludes unmerged PRs, other target branches, and negative durations. Author cohorts show Dependabot, explicitly named Copilot agent accounts, and other/unknown; the latter does not mean human-only. |
| Issue-to-merge lead time | Explicit same-repository `closingIssuesReferences`; one sample per issue at its earliest linked main merge, assigned to that merge's window; median hours from issue creation. | No prose parsing, chronological guesses, or external issue references. A linked PR is not evidence that every requirement was delivered. Negative durations are excluded and counted. |
| Issue-link coverage | Main PRs merged in the window with at least one valid same-repository closing link / all main PRs merged in the window. | Denominator includes bots and unlinked work. Also show unique issue sample count; PR coverage and issue sample size are different quantities. |
| Issue-to-deployment lead time | **Unavailable until trustworthy deployed-source evidence is recorded.** | The deployment workflow checks out the triggering CI SHA. Automatic environment deployment SHA and outer `workflow_run.head_sha` do not independently identify the image source under concurrent pushes. No guessed issue-to-deployment timings are reported, and no production change is made to populate this metric. |
| CI reliability | Every attempt of `ci.yml` whose `run_started_at` is in the window: successful attempts / decisive attempts. Decisive means `success`, `failure`, `timed_out`, `startup_failure`, or `action_required`. | Reruns count independently, preserving failed attempts. Counts for cancellations, skipped, neutral, stale, pending, and unknown are separate and excluded from the ratio. `action_required` can reflect authorization rather than a code defect. PR-triggered CI, main pushes, and other triggers are also separated. |
| Staging/production delivery reliability | Unique deployment job IDs from **all** `deploy.yml` attempts, grouped by environment and job start time; same decisive-outcome denominator as CI. | Staging includes build/publish failures. Skipped production is not failure; approval-waiting jobs are pending. Reused jobs across reruns count once. Job names are explicitly mapped; an unmapped name fails collection instead of dropping data. |
| Deployment frequency | Count of unique successful delivery jobs completed in the window, separately for staging and production; divide by seven calendar days only for complete periods. | Repeated real delivery jobs count; reused jobs do not. This counts pipeline delivery operations, not distinct releases, image digests, or deployment-status updates. Production job success is not a runtime health guarantee. |
| Agentic execution outcomes | Exact `issue-triage.lock.yml` and `weekly-repo-report.lock.yml` attempt outcomes and rerun counts, with the same start-time cohorts and reliability denominator as CI. | Successful execution does not prove useful advice, a published output, accepted recommendations, or subsequent delivery. Generic bot comments/issues are not attributed without explicit workflow provenance. |
| Dependabot PR activity | PRs authored by the known Dependabot account opened in each window; main-target PRs merged in each window; open PR inventory at collection. | Includes security and version updates; do not infer security fixes from ordinary dependency PRs. Closed-unmerged PRs do not count as merged. |
| CodeQL / Dependabot alerts | If accessible: current counts by state and counts whose available `created_at`, `fixed_at`, or `dismissed_at` timestamps fall in the window. Code-scanning results are filtered to the CodeQL tool on `main`. | Inventory is current, not period-end backlog. Reopenings and overwritten transitions cannot be reconstructed from this API response. Dependabot auto-dismissals appear in inventory but are not counted as manual dismissal events. No raw vulnerability details are published. |

Reliability outcomes are observed **during collection**, even for prior-period
start-time cohorts. For example, an attempt started yesterday but completed today
can contribute yesterday's cohort with today's observed result. This is not a
historical end-of-day success rate; recollection may revise a cohort. Frequency
instead uses completion timestamps. Current unfinished deployment jobs, including
approval waiting, are explicitly a separate inventory.

### Data sources, access, and trustworthy failures

`scripts/sdlc_metrics.py` uses these supported APIs:

- [GraphQL pull requests](https://docs.github.com/en/graphql/reference/objects#pullrequest),
  including `closingIssuesReferences`, creation/merge timestamps, base branch,
  and author login; outer and nested connections are paginated.
- [REST workflow runs and attempts](https://docs.github.com/en/rest/actions/workflow-runs)
  resolved by exact workflow file path, and
  [attempt-specific jobs](https://docs.github.com/en/rest/actions/workflow-jobs).
  Delivery jobs are sufficient to measure the actual pipeline operations; automatic
  deployment status/SHA records are deliberately not used as image provenance.
- [Code-scanning alerts](https://docs.github.com/en/rest/code-scanning/code-scanning)
  and [Dependabot alerts](https://docs.github.com/en/rest/dependabot/alerts).

The collector uses `GITHUB_TOKEN` with `contents: read`, `actions: read`,
`issues: read`, `pull-requests: read`, and `security-events: read`. No token has
Azure access, OIDC permission, or repository contents-write permission.
The separate publisher job has only `contents: read` to check out its trusted
script and `issues: write` to update the pre-created dashboard. Publication is
restricted to default-branch schedule/manual runs, serialized with workflow
concurrency, and never uses `pull_request_target` or executes PR-supplied scripts
with write privileges.

Dependabot alert access is not guaranteed with `GITHUB_TOKEN`; GitHub does not
offer a `dependabot: read` Actions permission. Alert 403/404 responses are shown
as **unavailable**, not zero. The local baseline's authenticated user may see
data the scheduled token cannot. No additional secret is requested to work around
this. Code-scanning availability also depends on the enabled feature and token.

Required-source failures, malformed/incomplete pagination, rate limits after
bounded retries, and unknown deployment job mappings fail collection and preserve
the last published dashboard. Optional alerts are unavailable only for 403/404;
other errors fail rather than producing reassuring empty results. The collector
does not use the search API's capped results and fetches all retained runs/attempts
and PRs, including earlier records needed for first-merge attribution. As the
repository grows, a run may reach its explicit timeout; investigate rather than
silently truncating the population.

GitHub APIs are not transactional. Collection start/end disclose the observation
interval; the entire repository is not frozen at one instant. Deleted/expired
workflow history and historical alert states cannot be recovered. API pagination
counts are checked when provided, but retained records are not proof of all-time
completeness. CI combines build, tests, coverage collection, and CodeQL in one job;
CI failure is not automatically a vulnerability or test failure.

### History, publication, and operation

Each successful collection writes `report.md`, `report.json`, and an allowlisted
`evidence.json` containing IDs, timestamps, outcomes, author identifiers, and issue
links. It never archives PR/issue bodies, logs, tokens, or vulnerability payloads.
These are uploaded as `sdlc-metrics-<run_id>-<attempt>` artifacts for 90 days, subject
to repository retention policy and manual deletion, and Markdown is included in
the GitHub Step Summary.

The publisher is explicitly configured for dashboard issue 28. It verifies the
issue marker and owner, so a title collision cannot select an unrelated issue.
There is no automated issue creation and no weekly issue spam. Closed dashboards
are reused without reopening. If the issue is removed, restore it or intentionally
bootstrap a replacement and update the configured number and documentation;
publication fails rather than creating surprise replacements.

A stable period marker deduplicates archived comments. Reruns refresh the current
body but preserve the original comment; its original observation timestamp remains
visible. Older windows/observations cannot overwrite a newer current body.
Archived comments provide history after artifact expiry, but remain subject to
normal issue permissions, edits, and deletion. Routine output is not committed.

To collect a **read-only** preview with an already authenticated `gh` CLI:

```bash
python3 scripts/sdlc_metrics.py collect \
  --repository sahad7793/agetic-sdlc-demo --output /tmp/sdlc-metrics-preview
python3 -m unittest discover -s tests/sdlc_metrics
```

`--baseline` additionally calculates all available early history up to collection
start. The explicit `publish` subcommand writes to the dashboard and is not part
of preview or tests. Prefer **Actions > SDLC Metrics > Run workflow** on `main`
for publication after merge. Tests use fixtures/fake clients and need no network
or credential.

The committed baseline is a local read-only observation. The dashboard was
initially seeded from this snapshot using the explicit publisher and the owner's
existing GitHub credential; repeating publication preserved exactly one archived
comment for that window. This does not test the scheduled token's permissions.
The workflow becomes
eligible for scheduled/default-branch publication only after a human merges the
PR. Local API access and a green PR check do not prove that the first scheduled
`GITHUB_TOKEN` run can read optional alerts; inspect that run's availability notes.

### Interpreting change

Review delivery speed alongside reliability, link coverage, and sample size.
Growing link coverage changes the measured lead-time population; dependency PRs
and approval delays can dominate small cohorts. A comparison between two
agent-enabled periods cannot establish that agents caused improvement. There is
no controlled pre-agentic baseline or complete attribution of locally assisted
work. Do not interpret these metrics as individual productivity, escaped-defect
rate, DORA change-failure rate, MTTR, or proof of advice quality. Those require
additional evidence that this report deliberately does not invent.

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

**Merging your own PR as the solo owner.** GitHub always disables "Approve" for a pull request's own author (there is no repository setting that changes this), so a solo owner's PRs will show `mergeStateStatus: BLOCKED` on the review requirement even with CI green. Verify your CI is passing first, then use the bypass rather than treating it as an error:

- **UI:** open the PR, scroll to the merge box, and use the dropdown next to the (greyed-out) merge button to pick **"Merge without waiting for requirements to be met (bypass branch protections)"**.
- **CLI:** `gh pr merge <number> --admin --squash` (or `--merge`/`--rebase` to match the allowed merge methods).

Only use this for your own PRs. Do not use it to wave through a collaborator's or an agent's pull request — those must still get a real second-party approval; bypassing on their behalf defeats the review gate this ruleset exists to enforce.

### 3. Enable GitHub Copilot coding agent

Enable GitHub Copilot coding agent for the organization and this repository according to your Copilot plan's policies. Confirm it can create branches and pull requests but cannot bypass protection rules.

### 4. Hand off an approved issue

Open the approved issue, assign it to **Copilot**, or use **Open in Copilot**. Include the intended scope and acceptance criteria. Review the resulting plan and pull request like any other contributor change; the agent's work does not replace human review.

### 5. Workflow orchestration

This repository runs two [GitHub Agentic Workflows](https://github.github.com/gh-aw/) (`gh-aw`), configured under `.github/workflows/`: an issue-triage workflow that comments on newly opened issues, and a weekly report workflow that opens a summary issue. Both are advisory only — they cannot self-approve, self-merge, or mutate issues without review. See [agentic-workflows.md](agentic-workflows.md) for what each workflow does and the `COPILOT_GITHUB_TOKEN` repository secret the owner must add before they can run.
