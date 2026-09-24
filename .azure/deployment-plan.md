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
