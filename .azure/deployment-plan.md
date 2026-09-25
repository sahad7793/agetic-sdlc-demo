# Azure Deployment Plan

**Status:** Validated

## Scope

Modernize the TaskManagementDemo .NET 8 API for staged Azure Container Apps delivery with Entra-only Azure SQL Database.

## Progress

- [x] Architecture and delivery approach approved
- [x] Resolve Azure tenant authentication and capacity validation
- [x] Generate application, container, infrastructure, and delivery artifacts
- [x] Validate implementation and infrastructure
- [ ] Deploy and verify staging health

## Validation Proof

- `az bicep build --file infra/shared.bicep` and `az bicep build --file infra/environment.bicep`: passed.
- SQL security scan for prohibited password-authentication properties: no matches.
- `dotnet build TaskManagementDemo.sln --configuration Release`: passed with zero warnings and errors.
- `dotnet test TaskManagementDemo.sln --configuration Release --no-build`: passed, 10/10 tests.
- `dotnet publish src/TaskManagement.Api/TaskManagement.Api.csproj --configuration Release --no-restore`: passed and produced the API assembly.
- Shell syntax validation (`bash -n`) for both provisioning scripts: passed.
- Local Docker build could not run because Docker Desktop is not running on the execution host. The container build remains independently covered by the Ubuntu GitHub Actions deployment runner.

## Role Assignment Verification

- The staging GitHub Actions principal receives `AcrPush` on the shared ACR through `infra/shared.bicep`.
- Staging and production GitHub Actions principals receive `Contributor` only on their respective application resource groups through the bootstrap script.
- Each user-assigned Container App identity receives `AcrPull` scoped to the shared ACR through the bootstrap script.
- Each user-assigned Container App identity receives Azure SQL data-plane roles (`db_datareader`, `db_datawriter`, and `db_ddladmin` for migrations) through the idempotent SQL grant script.

## Deployment Recovery

- East US 2 rejected Azure SQL logical-server creation with `RegionDoesNotAllowProvisioning`.
- Central US is the capacity-checked fallback for staging Container Apps and Azure SQL. The existing shared registry remains in East US 2.
- Central US has since reported `AKSCapacityHeavyUsage` when creating production's Container Apps environment. Production therefore uses the capacity-checked West US 2 fallback; staging remains in Central US.
- Failed East US 2 and Central US production resources are preserved; new environment-specific resource groups avoid destructive cleanup.
- Staging has been deployed successfully with `tmapi8a58968e.azurecr.io/taskmanagement-api@sha256:3a44cb0db004be1a73f86e1f8ff7ed09ea6dbc39149d8ca24cce68736d2ff5b4`; `/health` returned `Healthy`.
- Production infrastructure is provisioned in West US 2. Its application image remains intentionally gated behind the protected GitHub `production` Environment approval and will be deployed by the workflow after CI succeeds on `main`.

## Observability (added)

**Scope:** closed the runtime-observability gap — action groups, metric/log alerts, an availability test, and a cross-environment workbook — with additive-only Bicep deployments against the already-running staging and production Container Apps. No re-application of `environment.bicep`/`shared.bicep` was performed against live resources, because that would reset `containerImage` to its placeholder default (the running image is set out-of-band by the Deploy workflow via `az containerapp update`) and cause an outage.

**In-scope app change:** `APPLICATIONINSIGHTS_CONNECTION_STRING` was already set as a Container App env var by `environment.bicep`, but no telemetry SDK ever read it, so no request/dependency telemetry existed for the new alerts/workbook to observe. Added the `Azure.Monitor.OpenTelemetry.AspNetCore` NuGet package and a guarded `builder.Services.AddOpenTelemetry().UseAzureMonitor();` call in `Program.cs` (only wired when the connection string is present, so local/test runs are unaffected). This is the minimal wiring change described as in-scope in the task.

**New Bicep (extends existing IaC, no parallel path):**
- `infra/observability.bicep` — per-environment module: Action Group (email receiver), HTTP 5xx-rate metric alert, replica restart-count spike metric alert, p95 latency scheduled query rule (Application Insights `requests` log, since Container Apps platform metrics don't support percentile aggregations), a standard availability webtest against `/health`, and its associated availability metric alert. Wired into `infra/environment.bicep` via a new `observability` module call and a new `alertEmail` param.
- `infra/workbook.bicep` + `infra/workbook-content.json` — cross-environment Azure Monitor Workbook (request rate/failures, p95 latency, SQL dependency health per environment, replica/restart metrics). Wired into `infra/shared.bicep` via a new `observabilityWorkbook` module call.
- `scripts/provision-infrastructure.sh` — updated to pass the new params (`alertEmail`, container app names, `workbookLocation`) so a from-scratch bootstrap remains complete/idempotent.

**Deployment proof (additive-only, both environments + shared):**
- `az deployment group what-if` for `infra/observability.bicep` against both `rg-taskmanagement-staging-centralus` and `rg-taskmanagement-production-westus2`: 6 resource creates, all other resources `ignore` (no modifications to running Container Apps).
- `az deployment group create` for `infra/observability.bicep` against both resource groups: `provisioningState: Succeeded`.
- `az deployment group what-if` for `infra/workbook.bicep` against `rg-taskmanagement-shared`: 1 create, 1 ignore.
- `az deployment group create` for `infra/workbook.bicep` against `rg-taskmanagement-shared`: `provisioningState: Succeeded`.
- `az resource show` confirmed `provisioningState: Succeeded` for both `Microsoft.Insights/webtests` (staging and production). `Microsoft.Insights/metricAlerts` resources don't return a persistent `provisioningState` via GET (normal Azure Monitor behavior); their existence and `enabled: true` state was confirmed via `az monitor metrics alert show`, and the deployment-level `Succeeded` result is authoritative.
- `/health` re-confirmed reachable (`200`) on both staging and production after all deployments.
- Post-merge, generated real traffic against redeployed staging and confirmed via `az monitor app-insights query` that the `requests` table contains real telemetry rows — proving the OpenTelemetry wiring emits data end-to-end, not just compiles.
- Confirmed the deployed workbook's `serializedData` (only retrievable with the ARM query param `canFetchContent=true`) contains all four real resource IDs substituted, with no leftover `{0}`-`{3}` placeholders.

**End-to-end forced alert-fire test (staging only, 2026-09-25):**

To prove an alert doesn't just deploy but actually fires and auto-resolves, staging's single active Container App revision (`task-api-stage-8a58968e--0000005`) was briefly deactivated via `az containerapp revision deactivate` (no image/config change — `minReplicas`/`maxReplicas` were left at `1`/`3` throughout, since Container Apps requires `maxReplicas >= 1`). This made the ingress return real `404` responses for ~5.5 minutes.
- `availabilityResults` in Application Insights showed 4 of 5 test locations (Central US, North Central US, West US, West Europe) failing with `'404 - Not Found' does not match the expected status '200 - OK'`, well over the alert's `failedLocationCount >= 2` threshold.
- Queried `Microsoft.AlertsManagement/alerts` (api-version `2019-05-05-preview`) filtered to `targetResourceGroup=rg-taskmanagement-staging-centralus`: `task-api-stage-8a58968e-health-test-alert` showed `monitorCondition: Fired` (Sev1) at `2026-09-25T05:45:48Z`.
- The revision was reactivated via `az containerapp revision activate`; `/health` returned `200` again within ~2 minutes, and all 5 availability-test locations passed on the next cycles.
- Re-querying the Alerts Management API ~11 minutes later showed the same alert transitioned to `monitorCondition: Resolved` at `2026-09-25T05:56:50Z` — a full real `Fired -> Resolved` lifecycle, with no lasting changes to staging's configuration.
