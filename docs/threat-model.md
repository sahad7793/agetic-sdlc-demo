# Current system threat model

This STRIDE review describes the repository state represented by the code and
infrastructure-as-code, not controls that may exist only in an Azure or GitHub
tenant. It is a design aid, not a penetration test or a substitute for human
review. Reassess it when API, data, identity, infrastructure, workflow, or
dependency boundaries change. See the
[design review checklist](design-review.md) and reusable
[threat-model template](templates/threat-model-template.md).

## System and trust boundaries

- External clients reach the ASP.NET Core API through Azure Container Apps
  external ingress. The app exposes `GET /api/tasks`,
  `GET /api/tasks/overdue`, `GET /api/tasks/{id}`, `POST /api/tasks`,
  `PUT /api/tasks/{id}`, `PATCH /api/tasks/{id}/status`,
  `DELETE /api/tasks/{id}`, and `GET /health`. Ingress disallows insecure
  transport; the delivery workflow sets the target port to 8080.
- Controllers bind and validate DTOs, services implement task rules, and the
  EF Core repository reads and writes task records. Azure SQL is selected in
  the deployed configuration; SQLite is used when Azure SQL is disabled.
  Azure SQL authentication is configured for the Container App's
  user-assigned managed identity. The deploy workflow configures the same
  identity for ACR pulls. The API applies EF migrations at startup.
- GitHub Actions runs CI for pull requests and `main`. The deploy workflow
  runs after successful CI for `main`, obtains Azure access with OIDC, pushes
  an image to ACR, records its digest, deploys it to staging, then deploys the
  same digest to production. Repository documentation says production
  requires environment approval; the environment protection and federated
  credential settings are not defined in this repository.
- The planning agent activates on a human-applied `stage:spec-approved`
  label. It reads an issue, trusted maintainer clarifications, and repository
  files. Its GitHub job has read-only permissions; comments and the two
  narrowly scoped stage-label changes are routed through `safe-outputs`.
  Issue text, comments, and repository content are untrusted input.

## Evidence gaps

- The API has no authentication or authorization middleware, `[Authorize]`
  policies, or user/tenant ownership checks in the routes or task service.
  Therefore the API does not identify callers or partition tasks by caller.
- The checked-in Bicep configures a user-assigned identity for the Container
  App and uses it for Azure SQL connection authentication, but does not show
  the SQL database role grants or an ACR pull role assignment. The shared
  template grants the staging deployment principal `AcrPush`; verify the
  runtime identity's actual SQL and ACR grants in the Azure configuration.
- Azure SQL enables public network access and includes the `0.0.0.0` Azure
  services firewall rule; no private endpoint or narrower SQL network policy
  is defined in the inspected environment template.
- OIDC federation details, Azure role scopes, GitHub branch protection, and
  production environment reviewers are tenant/repository settings and are
  not fully verifiable from these files. Repository documentation describes
  production approval; confirm that the live setting matches it.

## STRIDE threats

### Spoofing

**Asset:** Task records and the identity of API callers.

**Threat:** Any network caller can invoke task endpoints and is indistinguishable
from every other caller. A caller can act as an arbitrary task owner because
the current model has no owner or tenant field.

**Existing mitigation:** Container Apps ingress is external but disallows
insecure transport in [`infra/environment.bicep`](../infra/environment.bicep).
The create request has required/length/enum validation
([`CreateTaskRequest.cs`](../src/TaskManagement.Api/DTOs/CreateTaskRequest.cs));
these checks do not authenticate the caller.

**Residual risk:** HTTPS protects transport, not caller identity or access to
task data. There is no per-user or per-tenant boundary.

**Recommended follow-up:** Before adding private or user-specific task data,
introduce an explicit authentication scheme and service-level authorization
that binds each requested task to a verified subject; add endpoint integration
tests for unauthorized and cross-owner access.

### Tampering

**Asset:** Task records and the deployed application image.

**Threat:** Any caller can create, update, change status, or delete tasks
through the public API. Separately, an actor able to alter trusted `main`
workflow or deployment inputs could influence the image that passes through
the deployment pipeline.

**Existing mitigation:** The service rejects past due dates and invalid status
transitions ([`TaskService.cs`](../src/TaskManagement.Api/Services/TaskService.cs));
EF Core uses parameterized LINQ queries
([`EfTaskRepository.cs`](../src/TaskManagement.Api/Repositories/EfTaskRepository.cs)).
Deployment follows successful CI, checks out the workflow-run SHA, pushes an
image, then deploys its digest to both environments
([`deploy.yml`](../.github/workflows/deploy.yml)). ACR has its admin user
disabled, and the shared template assigns `AcrPush` to the staging deployment
principal ([`shared.bicep`](../infra/shared.bicep)).

**Residual risk:** Business validation does not prevent unauthorized writes.
The digest prevents staging-to-production tag drift, but does not prove source
or image provenance; ACR trust and quarantine policies are disabled in
[`shared.bicep`](../infra/shared.bicep), its public network access remains
enabled, and the repository does not define branch protection or
image-signature verification.

**Recommended follow-up:** Add caller authorization before exposing
user-specific records. Verify branch protections and narrowly scoped Azure
federated credentials; consider image signing/scanning and deployment-time
verification if the release threat model requires provenance guarantees.

### Repudiation

**Asset:** Evidence of task changes and deployment actions.

**Threat:** A task mutation cannot be tied to a verified person or tenant, and
the application does not persist an append-only record of who changed a task
and when.

**Existing mitigation:** The service emits informational logs for task
creation, updates, status changes, and deletion, with task IDs and selected
values ([`TaskService.cs`](../src/TaskManagement.Api/Services/TaskService.cs)).
The deployed app can send request and SQL dependency telemetry to Application
Insights ([`Program.cs`](../src/TaskManagement.Api/Program.cs),
[`environment.bicep`](../infra/environment.bicep)). Deploy and rollback
workflows use GitHub Actions run history; rollback writes an audit summary
([`rollback.yml`](../.github/workflows/rollback.yml)).

**Residual risk:** Application logs do not establish caller identity or a
durable task-change history. Telemetry and workflow run history are not
equivalent to an application-level audit record.

**Recommended follow-up:** If accountability or regulated auditability is
required, record authenticated actor, operation, task identifier, timestamp,
and outcome in a protected audit store; define retention and access controls.

### Information disclosure

**Asset:** Task titles, descriptions, dates, status, and priority; telemetry
and monitoring credentials; Azure SQL data.

**Threat:** Public read endpoints return all tasks or an individual task
without checking caller identity. The Container Apps environment also receives
the Log Analytics workspace shared key as a Bicep-provided configuration
value.

**Existing mitigation:** Ingress requires HTTPS
([`environment.bicep`](../infra/environment.bicep)). Request/response
contracts use DTOs rather than returning EF entities
([`TaskResponse.cs`](../src/TaskManagement.Api/DTOs/TaskResponse.cs),
[`TasksController.cs`](../src/TaskManagement.Api/Controllers/TasksController.cs)).
The service logs identifiers and selected metadata, not task titles or
descriptions ([`TaskService.cs`](../src/TaskManagement.Api/Services/TaskService.cs)).
Azure SQL requires Entra-only authentication and TLS 1.2
([`environment.bicep`](../infra/environment.bicep)).

**Residual risk:** HTTPS does not restrict which caller can read task data.
The workspace shared key is configured from `listKeys()` in
[`environment.bicep`](../infra/environment.bicep); its effective visibility
and redaction depend on Azure control-plane permissions and are not established
by this repository. Azure SQL public network access is enabled and the
`0.0.0.0` firewall rule permits Azure services; network reachability and
database authorization must be considered separately.

**Recommended follow-up:** Restrict reads with the same explicit
authentication/ownership policy as writes. Review who can read or modify
Container Apps environment configuration and rotate the workspace key if
exposure is suspected; use an identity-based logging path if supported by the
chosen Azure configuration. Prefer private connectivity for SQL and narrow
network access; verify SQL grants for the app identity.

### Denial of service

**Asset:** API availability, Azure SQL capacity, and deployment health.

**Threat:** The external API has no visible rate limit, request quota, or
pagination cap. `GET /api/tasks` materializes the complete filtered result
([`EfTaskRepository.cs`](../src/TaskManagement.Api/Repositories/EfTaskRepository.cs)),
so repeated or large reads/writes can consume app and database capacity.

**Existing mitigation:** The Container App scales from one to at most three
replicas and configures an HTTP concurrency scaling rule
([`environment.bicep`](../infra/environment.bicep)). SQL enables transient
failure retries ([`Program.cs`](../src/TaskManagement.Api/Program.cs)); the
infrastructure defines 5xx, restart, latency, and availability alerts
([`observability.bicep`](../infra/observability.bicep)).

**Residual risk:** Scaling, retries, and alerts do not prevent abuse or bound
database work. No endpoint rate limiting or result-size limit is configured
in the API.

**Recommended follow-up:** Add bounded pagination and server-side maximum
page sizes; configure rate limits and request-size/time limits appropriate
for the service, and load-test the limits against SQL and Container Apps
capacity.

### Elevation of privilege

**Asset:** Azure SQL, ACR, and GitHub issue lifecycle controls.

**Threat:** A compromised API process can request tokens for its attached
user-assigned identity. Since migrations run at app startup
([`Program.cs`](../src/TaskManagement.Api/Program.cs)), that identity may need
schema-changing access; exact SQL grants are not present in Bicep. The
planning agent can also be targeted by prompt injection in issue text or
comments to elicit an incorrect plan or stage transition.

**Existing mitigation:** The app uses managed-identity SQL authentication
instead of a checked-in password and Azure SQL is configured for
Entra-only authentication ([`environment.bicep`](../infra/environment.bicep)).
The planning agent explicitly treats issue/repository content as untrusted,
has only `contents: read` and `issues: read`, and can emit only one triggering
issue comment plus the configured `stage:plan-ready` and
`stage:spec-approved` label operations
([`plan-agent.md`](../.github/workflows/plan-agent.md)). Applying
`stage:plan-approved` remains a human gate
([`lifecycle-labels.json`](../.github/lifecycle-labels.json)).

**Residual risk:** The app identity's actual database and registry grants
must be verified outside this repository. Prompt-injection defenses are
instructional rather than a guarantee: injected content could still produce
a misleading advisory comment or an incorrect `stage:plan-ready` transition,
although the configured outputs cannot approve a plan, merge, or deploy.

**Recommended follow-up:** Verify the app identity has only required
data-plane permissions and a narrowly scoped ACR pull permission; separate
migration privileges from runtime permissions where operationally feasible.
Keep human review of plan content and human-only approval labels, and report
suspected injection without following it.
