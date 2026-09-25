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

## Release notes

Use **Actions > Release notes > Run workflow** when you want a truthful,
human-reviewable draft of what changed, without publishing anything. This is a
**manual, workflow_dispatch-only** entry point; nothing here runs automatically
on push, tag, or schedule.

Inputs:

- `tag_name` (required) — the prospective tag/version these notes describe
  (e.g. `v0.1.0`). **This workflow never creates this tag.**
- `previous_tag_name` (optional) — an existing tag/ref to start from. Leave
  blank to cover the full history up to `target_commitish`.
- `target_commitish` (default `main`) — the branch or commit the prospective
  tag would point at.
- `create_draft_release` (default `false`) — when `true`, also saves a
  **draft** GitHub Release with the generated notes.

### Provenance and no-fabrication guarantee

The notes body comes entirely from GitHub's own
`POST /repos/{owner}/{repo}/releases/generate-notes` REST API — the same
engine behind the "Generate release notes" button in the GitHub UI. It
computes notes purely from **merged pull request metadata** (labels, titles,
authors) between `previous_tag_name` and `target_commitish`, grouped into
categories defined in [`.github/release.yml`](../.github/release.yml).
`scripts/release_notes.py` calls this API and writes the response verbatim to
a `*.notes.md` file; it does not add, remove, reorder, or summarize any
content itself. Per GitHub's documentation, this API call has **no side
effects** — it does not save or create anything on its own.

`.github/release.yml` maps existing repository labels (`enhancement`, `bug`,
`accessibility`, `documentation`, `dependencies`/`github_actions`/`.NET`,
`incident`) to changelog sections, and ends with a mandatory catch-all
category (`labels: ["*"]`) named "Other changes". **Merged PRs with no
labels, or labels that match nothing above, are never silently dropped** —
they always appear under "Other changes" instead. `changelog.exclude.labels`
removes purely administrative labels (`duplicate`, `invalid`, `wontfix`,
`question`) that would not describe a real change to users.

### Jobs and permissions

Two least-privilege jobs, mirroring the split used by `sdlc-metrics.yml`:

- **`generate`** (`permissions: contents: read`) — always runs. Calls the
  generate-notes API, writes the notes and an audit JSON to
  `$GITHUB_STEP_SUMMARY` and a workflow artifact tagged
  **"DRAFT — nothing published, no tag created"**. This job never touches
  `contents: write` and cannot create or modify anything in the repository.
- **`draft-release`** (`permissions: contents: write`) — only runs when
  `create_draft_release` is `true`. Downloads the artifact from `generate` and
  saves (or updates, if one already exists for that tag) a **draft** GitHub
  Release using the generated notes.

**A draft Release with a brand-new tag name does not create a git tag.**
GitHub only creates the tag at the moment a maintainer opens the draft in the
UI and explicitly clicks **Publish release**. The `draft` script step also
re-reads the saved release via `gh release view --json isDraft` and refuses
to report success unless the result is still a draft — so this workflow
cannot accidentally publish a release even if `gh` behavior changes upstream.

There is deliberately **no GitHub Environment approval gate** on
`draft-release` (unlike `rollback.yml`'s production environment): a draft is
private (visible only to users with push access), fully reversible, and
triggering `workflow_dispatch` itself already requires write access to this
repository.

### Limitations

- No git tags or GitHub Releases exist in this repository yet, so there is no
  automatically-detected "previous release" — `previous_tag_name` must be
  supplied explicitly (or left blank to summarize the full history).
- This workflow does not verify that anything it describes was actually
  deployed, is healthy, or is free of open incidents; it only reflects merged
  PR metadata.
- Whether an unauthenticated `contents: read` token is sufficient for the
  generate-notes API call has not been empirically verified against this
  repository (GitHub's docs do not state a minimum permission level). If it
  is insufficient, the `generate` job fails loudly with the API's error
  message rather than degrading silently.
- Saving a draft release only records a draft; it never edits an existing
  **published** release, never deletes releases, and never force-updates a
  tag.

## Incident response

Use **Actions > Incident Response > Run workflow** when you receive a
production/staging alert — from an Azure Monitor action-group email, the
Portal, a health check, or a customer report — and want a structured,
ownable GitHub record of it. This is a **human-initiated** entry point: an
operator who received a forwarded alert pastes its details into the dispatch
form. There is no webhook receiver, no `repository_dispatch`, and no new
Azure resource; adding one was explicitly out of scope for this change.

`scripts/incident_report.py` builds the issue deterministically — every field
in the resulting issue is either the operator's verbatim input, a static
per-severity SLA target, or a static link to existing docs/workflows. It does
**not** call an LLM, does **not** query Application Insights or any other
telemetry source, and does **not** invent a correlation ID, log query, or
timeline entry that wasn't supplied. If an optional field (correlation ID, log
query link, Azure Portal alert link) is left blank, the issue says so
explicitly rather than fabricating a plausible-looking value.

The workflow's job permissions are `contents: read` (to check out the script)
and `issues: write` only — no Azure OIDC, `id-token`, or new secret is used or
required, because filing an incident never touches Azure. It only ever
creates one GitHub issue, labeled `incident` and a per-severity label
(`sev1`–`sev4`, created on first use if missing), and assigns it to the
triggering operator as the initial primary responder.

**This workflow never automatically remediates anything.** It does not
dispatch Rollback, does not call any Azure API, and does not close or merge
anything. Its triage checklist explicitly tells the responder to use the
existing **Rollback** workflow themselves, with a `reason` referencing the
incident issue, if they decide a rollback is warranted — preserving the same
production approval gate described above.

### Dispatch inputs

| Input | Required | Purpose |
| --- | --- | --- |
| `environment` | Yes | `staging`, `production`, or `shared` — which environment the alert concerns. |
| `severity` | Yes | `Sev1 - Critical` through `Sev4 - Low`; drives the static SLA targets below. |
| `alert_source` | Yes | Where the alert came from (Azure Monitor alert, availability/health check, manual observation, customer report, other). |
| `alert_title` | Yes | Short title, becomes part of the issue title. |
| `alert_summary` | Yes | The forwarded alert text or a description of what was observed; included verbatim in a fenced code block. |
| `correlation_id` | No | Application Insights `operation_Id` / trace ID, if known. |
| `log_query_link` | No | A deep link to a Log Analytics / Application Insights query. |
| `azure_alert_link` | No | A link to the Azure Portal alert instance. |

### Response timing targets

These are static SLA policy targets computed from the issue's creation time,
not a measurement of anything — the issue body says so explicitly:

| Severity | Acknowledge by | Mitigate by |
| --- | --- | --- |
| Sev1 - Critical | +15 minutes | +60 minutes |
| Sev2 - High | +30 minutes | +240 minutes |
| Sev3 - Moderate | +120 minutes | +480 minutes |
| Sev4 - Low | +480 minutes | +2880 minutes |

### Operating procedure

1. When you receive/observe an alert, dispatch **Incident Response** with the
   details you have. You do not need every optional field — leave unknown
   ones blank; the issue will say so rather than guessing.
2. The created issue is your incident record. Follow its triage checklist:
   confirm scope, correlate with the observability workbook/App Insights
   using the links you provided, check recent Deploy history, and decide
   whether to roll back.
3. If you roll back, dispatch **Rollback** yourself (see above), referencing
   the incident issue number in its `reason` input. Incident Response never
   does this for you.
4. Update the incident issue with your timeline and resolution as you work.
   Close it with a short postmortem note for Sev1/Sev2 once resolved.

### Limits

This is a lightweight GitHub-native record, not a paging/on-call product: it
does not page anyone, does not integrate with PagerDuty/Opsgenie, and does not
read alert state from Azure. "Filed" is not the same as "acknowledged in
production" — the assignee and checklist are the auditable record of who is
responding and when, based on what the operator reports. Treat the SLA table
as policy, and the alert-context fields as exactly what was typed in — verify
independently before acting on them for anything safety-critical.

## Performance and load-testing gate

There was no performance signal anywhere in the pipeline before this change:
CI proved the API builds, passes unit/integration tests, and is scanned by
CodeQL, but nothing measured latency, throughput, or error rate under
concurrent load, and nothing would catch a request-handling regression before
merge. This closes that gap with a **local/CI-only** load test — it never
runs against a deployed staging or production environment automatically.

### What was added

- **`tests/performance/task-api-load.js`** — a [k6](https://k6.io) script with
  two bounded (`per-vu-iterations`, not open-ended-duration) scenarios:
  - `reads` — pure `GET /health`, `GET /api/tasks`, `GET /api/tasks/overdue`
    traffic. Safe by construction; touches no other task's data.
  - `lifecycle` — each iteration creates one task it owns (a uniquely titled
    `perf-test-<uuid>` task), reads it back, transitions its status
    `Todo → InProgress` (the only transition the business rules allow from a
    fresh task), then deletes it. Every mutation is scoped to data the
    iteration itself created, so the run is idempotent and self-cleaning —
    nothing is left behind, and nothing pre-existing is read, written, or
    deleted.
  - The script has **no `thresholds` block** — it only measures and exports
    `perf-results/summary.json`. Pass/fail is entirely `perf_gate.py`'s job.
- **`scripts/perf_gate.py`** — a deterministic (no LLM, no network calls)
  comparator with two subcommands:
  - `compare` (run automatically in CI) reads a k6 summary + `baseline.json`,
    renders a markdown report to the job summary and `perf-results/gate-report.md`,
    and decides pass/fail. In `--mode advisory` (the default, and the only
    mode the workflow uses automatically) it **always exits 0** — the job
    never fails because of a slow run, only reports it. `--mode strict` is
    available for a maintainer to opt into later, once there's a track record
    of real baselines.
  - `capture-baseline` is a **maintainer-run-only** helper, never invoked by
    any workflow, that turns one reviewed `summary.json` into a new,
    committed `baseline.json`.
- **`tests/performance/baseline.json`** — ships as an explicit **bootstrap
  placeholder** (`"status": "unset"`, empty `scenarios: {}`). It intentionally
  contains **no invented latency/error/throughput numbers**. `perf_gate.py`
  special-cases this: every row in the report reads "no baseline yet" and the
  gate cannot fail, in either mode, until a human promotes a real baseline
  (see below). The tolerance multipliers it will apply once a baseline exists
  are documented in the file itself (`p95`/`p99` latency multipliers, a hard
  error-rate cap, and a minimum-throughput multiplier) — deliberately
  generous headroom bands, not production SLOs.
- **`.github/workflows/performance.yml`**:
  - `local-load-test` (pull requests touching the API/perf files, plus manual
    `workflow_dispatch`) builds the API, starts it as a background process on
    `localhost` against a **temporary SQLite file** (`Database:UseAzureSql` is
    already `false` by default), polls `/health` with bounded retries, runs
    the k6 script, then runs `perf_gate.py compare --mode advisory` and
    uploads `perf-results/` (including the API's own log) as a build
    artifact. Nothing in this job touches Azure or requires any secret.
  - `staging-smoke-load` is **manual-only** (`workflow_dispatch`) and gated
    behind two independent inputs the caller must both set
    (`run_staging_smoke: true` *and* `confirm: yes`) — mirroring the
    double-confirmation pattern already used by the `Rollback` workflow. It
    signs in with the existing Azure OIDC federation, resolves the staging
    Container App's FQDN, and runs the k6 script with
    `PERF_TEST_READS_ONLY=true` (which drops the `lifecycle` scenario
    entirely at script-load time, so it is structurally impossible for this
    job to send a mutating request) at a small fixed size (2 VUs × 5
    iterations). This job is **never dispatched automatically** by anything
    in this repository.
- **`tests/perf_gate/test_perf_gate.py`** — unit/integration tests covering
  metric extraction, tolerance-multiplier math, the bootstrap/unset baseline
  path, advisory-vs-strict exit codes, and `capture-baseline`. Wired into CI
  alongside the other `tests/*` contract suites.

### Baseline lifecycle (why there's no number in `baseline.json` yet)

1. **Now:** `baseline.json` ships `"status": "unset"`. The gate runs on every
   relevant PR, always exits 0, and its report always says "no baseline yet".
   This is intentionally not useful for catching regressions on day one — its
   purpose right now is to prove the harness itself works end-to-end.
2. **After a few real CI runs:** a maintainer reviews several
   `perf-results/summary.json` artifacts from ordinary (non-regressed) PRs to
   confirm they look stable and representative of the shared GitHub-hosted
   runner, then picks one and runs, locally or by downloading the artifact:
   `python3 scripts/perf_gate.py capture-baseline --from summary.json`. This
   overwrites `tests/performance/baseline.json` with `"status": "set"` and the
   observed p50/p95/p99/error-rate/throughput numbers, preserving the
   existing tolerance multipliers. The maintainer reviews the diff and opens
   it as its own PR — `capture-baseline` is never run by CI.
3. **From then on:** `compare` has real numbers to check against. It keeps
   running in `--mode advisory` (report-only) until there's enough
   confidence in the baseline's stability to switch the workflow to
   `--mode strict`, which is the point at which a genuine regression can fail
   the job.

### Security, permissions, and data-safety notes

- `local-load-test` requests only `contents: read`; it needs no Azure
  credential, `id-token`, or repository secret, because it never leaves the
  GitHub-hosted runner.
- `staging-smoke-load` requests `contents: read` and `id-token: write` (for
  the existing OIDC federation, reused as-is — no new credential was added)
  and runs under the `staging` **environment**, so it is subject to whatever
  required reviewers/protection rules are configured for that environment,
  the same as `Rollback`'s and `Deploy`'s staging jobs.
- Both jobs are GET-only or self-contained-mutation-only by construction: the
  `lifecycle` scenario only ever creates, reads, transitions, and deletes
  tasks it created itself in that same iteration, and the staging job cannot
  reach that scenario at all (`PERF_TEST_READS_ONLY=true` removes it from the
  k6 `scenarios` map before the test run starts, it is not just skipped at
  request time).
- Nothing in this gate reads or writes Application Insights, Key Vault, or
  any other Azure resource beyond resolving the Container App's FQDN for the
  manual staging smoke job.

### Limitations

- **k6's runtime cost/duration is bounded, not zero.** `local-load-test` has a
  15-minute job timeout and both k6 scenarios use bounded iteration counts
  (5 VUs × 10 iterations each by default), not open-ended duration — this is
  a smoke-sized load test suitable for a PR check, not a capacity/stress test.
- **The bootstrap baseline means the gate cannot catch anything yet.** Until
  a maintainer runs `capture-baseline` and reviews the result, every PR's
  report will say "no baseline yet" — this is expected, not a bug.
- **Advisory mode never fails the build**, by design, even after a baseline
  exists — `--mode strict` is a deliberate, separate opt-in.
- **A shared GitHub-hosted runner is noisy.** Baselines captured here reflect
  that runner's variable CPU/IO characteristics, not a dedicated or
  production-representative environment; treat tolerance multipliers as
  generous headroom for that noise, not a precise SLO.
- **The staging smoke job is not run by this change, or by any automation.**
  It exists as an opt-in, double-confirmed, read-only capability for a human
  to use deliberately; nothing in this repository schedules or triggers it.

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

## Cost governance

This closes the cost-governance maturity gap with a cost-allocation tag standard
enforced in IaC, a manual operator runbook for budgets/alerts, and a manual,
read-only, non-monetary governance report. No Azure resource, budget, alert, or
tag on an already-provisioned resource is created, changed, or deleted by any
script or workflow in this section; every mutating action below is explicitly
operator-run. This repository is **public**, so no dollar amount, spend
figure, or budget threshold is ever collected, logged, or published by
anything described here — see "Manual governance report" for how that boundary
is enforced structurally, not just by convention.

### Cost-allocation tag standard

Every environment and shared resource carries these tags, aligned with the
[Cloud Adoption Framework tagging guidance](https://learn.microsoft.com/azure/cloud-adoption-framework/ready/azure-best-practices/resource-tagging):

| Tag | Meaning | Source |
| --- | --- | --- |
| `application` | Fixed to `taskmanagement`. | `scripts/provision-infrastructure.sh` |
| `environment` | `staging` or `production`. | `infra/environment.bicep` |
| `component` | Set on shared resources (e.g. `registry`). | `scripts/provision-infrastructure.sh` |
| `costCenter` | The organization's cost-center/GL code accountable for this resource's spend. | Required Bicep parameter — **no default** |
| `owner` | The team or individual accountable for this resource. | Required Bicep parameter — **no default** |

`infra/shared.bicep` and `infra/environment.bicep` require `costCenter` and
`owner` as explicit parameters and merge them into every resource's tags with
`union()`, so they can't be silently omitted on a future deployment. They
intentionally have no default value: inventing a placeholder cost-center or
owner here would create false confidence in a value nobody chose. Operators
must supply their organization's real values via
`scripts/provision-infrastructure.sh`'s `cost_center_tag`/`owner_tag`
variables (clearly marked `CHANGEME-*`) before running it.

**This only affects future provisioning.** The six existing
`rg-taskmanagement-*` resource groups (three actively used, three preserved
region-fallback groups from `.azure/deployment-plan.md`'s Deployment Recovery
history) were provisioned before this standard existed and currently carry
only `application`/`environment`. Retrofitting them is a manual step — see
below — not something this change applies automatically, because doing so
would be a live Azure mutation outside this repository's automation.

### Operator runbook: budgets, alerts, and tag retrofit

All commands below are run manually by an operator with the required role
(table below); none are automated by a script or workflow in this repository.

**1. Retrofit tags onto existing resources.** Run per resource group, after
substituting the organization's real values for the placeholders:

```bash
COST_CENTER='<your-real-cost-center-code>'
OWNER='<your-real-owner-or-team>'
for rg in rg-taskmanagement-shared rg-taskmanagement-staging-centralus rg-taskmanagement-production-westus2 \
          rg-taskmanagement-staging rg-taskmanagement-production rg-taskmanagement-production-centralus; do
  az tag update --resource-id "$(az group show --name "$rg" --query id -o tsv)" \
    --operation merge --tags costCenter="$COST_CENTER" owner="$OWNER"
done
```

Extend to child resources (Container Apps, SQL servers, ACR, etc.) with
`az resource tag` if per-resource (not just per-resource-group) tagging is
required for your cost reports; Azure Cost Management can also
[inherit resource-group tags onto child resources](https://learn.microsoft.com/azure/cost-management-billing/costs/enable-tag-inheritance)
without retagging every resource individually.

**2. Create a budget.** No threshold is proposed here — set one from your own
historical spend and business judgement, at the resource-group scope (so
staging and production alert independently) or subscription scope, using
either the Portal (**Cost Management + Billing > Budgets**) or the
[Bicep quickstart](https://learn.microsoft.com/azure/cost-management-billing/costs/quick-create-budget-bicep). Do not create a budget scoped so broadly that
an unrelated subscription workload triggers a false alert for this
application.

**3. Wire alert delivery.** Reuse the per-environment Action Groups already
created by `infra/observability.bicep` (`task-api-stage-*-ag`,
`task-api-prod-west-*-ag`) if budget alerts should reach the same on-call
recipients as availability/error alerts, or create a separate finance-facing
Action Group if spend alerts should go to different people. Either is a valid
operator choice; this repository does not prescribe one.

**4. Review the orphaned region-fallback resource groups.**
`rg-taskmanagement-staging`, `rg-taskmanagement-production` (both `eastus2`),
and `rg-taskmanagement-production-centralus` were preserved, not deleted,
after the region-capacity failures documented in `.azure/deployment-plan.md`'s
Deployment Recovery section. They are a cost-governance finding — review
their contents and decide whether to delete them — but this repository does
not delete them automatically; that decision and action belong to an
operator who can confirm nothing in them is still relied upon.

**Required roles:**

| Task | Minimum built-in role | Scope |
| --- | --- | --- |
| View cost data, budgets, alert configuration | `Cost Management Reader` | Subscription or resource group |
| Create/edit/delete budgets | `Cost Management Contributor` (or `Owner`/`Contributor`, which already include it) | Subscription or resource group |
| Retag existing resources | `Tag Contributor` (or `Owner`/`Contributor`) | Resource group or resource |
| Create/manage Action Groups | `Monitoring Contributor` (or `Owner`/`Contributor`) | Resource group |

### Manual governance report

`scripts/cost_governance_report.py` and `.github/workflows/cost-governance-report.yml`
report **tag compliance, resource inventory, and budget/alert existence only —
never a dollar amount, threshold, forecast, or Cost Management billing figure.**
This is structurally enforced, not just a convention: the report's Azure OIDC
credential is granted **`Reader` only**, which cannot call the Cost Management
billing APIs at all — the safest way to guarantee no spend figure can leak is
for the credential to lack permission to read one.

It follows `scripts/sdlc_metrics.py`'s conventions: deterministic (no LLM), a
`collect` subcommand that writes `report.md`/`report.json`/`evidence.json`,
fixture-driven tests with no live network calls
(`tests/cost_governance`, wired into `ci.yml`), and fails collection closed
rather than reporting a fabricated compliance percentage when a resource group
can't be enumerated.

**What it reports:**
- Tag compliance: the percentage of resources across the six
  `rg-taskmanagement-*` resource groups carrying all of `application`,
  `environment`, `costCenter`, and `owner`.
- Resource inventory: counts and types per resource group/environment.
- Budget and Action Group **existence** (present/absent, and count) per scope —
  amounts, current spend, and forecast fields are never read or written.

**Trigger and permissions:** `workflow_dispatch` only (no `schedule:` yet — see
"Operator setup" below); job permissions are `contents: read` and
`id-token: write` only. There is no `issues: write` permission and no publish
step in this iteration: output stays in the run's step summary and a 90-day
artifact (matching the SDLC metrics workflow's retention), not a public
dashboard issue, to keep a brand-new Azure-reading credential's blast radius
minimal for a first iteration. Extending this to the
[SDLC metrics dashboard](#sdlc-metrics-dashboard) (e.g. a "tag compliance %"
row) or a dedicated public issue is a natural next step once the credential has
operated safely for a period, but is not built now.

**Operator setup required before dispatch:** a new Entra app registration and
OIDC federated credential for a `cost-governance` GitHub Environment, granted
`Reader` **only** at subscription scope — deliberately not `Cost Management
Reader`, for the structural reason above. This follows the same pattern as the
existing `staging`/`production` OIDC setup in "Operator configuration": the
operator creates the app registration and federated credential and sets the
GitHub Environment variables (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
`AZURE_SUBSCRIPTION_ID`); this repository does not create the credential
itself. Until that exists, the workflow can be reviewed and its tests run, but
should not be dispatched.

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

### 6. Review governance and ownership routing

[`.github/CODEOWNERS`](../.github/CODEOWNERS) routes review requests for
ownership-sensitive paths: `infra/` (Bicep), `.github/workflows/` (CI/CD,
deploy, rollback), the deploy/rollback/access-provisioning scripts, and this
document plus [agentic-workflows.md](agentic-workflows.md). It deliberately
does **not** cover `*` — application code, tests, and most other docs have no
entry, so a code-owner requirement scoped to these higher-risk surfaces can
never block an unrelated PR.

**This file has no enforcement effect by itself.** GitHub only requires a
code owner's approval once you turn on **Require review from Code Owners**
for the "Main Branch Protection" ruleset (**Settings > Rules > Rulesets**).
This scaffold does not enable that setting; enable it yourself if you want
GitHub to require `@sahad7793`'s (or a future team's) approval on the listed
paths in addition to the existing one-approval requirement.

With a single collaborator, CODEOWNERS does not yet redistribute review to a
different person — all listed paths point to `@sahad7793`, the sole verified
collaborator today. Its value now is documenting which surfaces are
higher-risk and giving PR authors an explicit checklist prompt (see the pull
request template). As real collaborators or teams join, replace individual
entries in `.github/CODEOWNERS` with the relevant team (e.g.
`@sahad7793/platform`) instead of adding more usernames ad hoc, and only then
does turning on code-owner enforcement change who is requested for review.
