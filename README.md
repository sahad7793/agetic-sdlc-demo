# Task Management API: Agentic SDLC Demo

This repository is a compact ASP.NET Core .NET 8 API and a reference implementation of a human-governed Agentic SDLC workflow. The application provides task CRUD operations, validation, and task-status business rules; the repository automation demonstrates how an approved issue becomes a reviewed, tested pull request.

## Run locally

Prerequisite: .NET SDK 8.0.406 or a compatible .NET 8 SDK (the preferred version is pinned in `global.json`).

```bash
dotnet run --project src/TaskManagement.Api
```

The API persists local development data to `task-management.db`. In the Development environment Swagger is available at `/swagger`.

## Run tests

```bash
dotnet test TaskManagementDemo.sln
```

## OpenAPI change report

Pull requests changing the API or its contract tooling run a read-only, **advisory**
OpenAPI comparison against the reviewed snapshot on the base branch. The job
summary separates breaking changes from other consumer-facing changes; it
also uploads the generated contract and report. To regenerate the committed
snapshot after a reviewed contract change:

```bash
dotnet restore src/TaskManagement.Api/TaskManagement.Api.csproj
dotnet build src/TaskManagement.Api/TaskManagement.Api.csproj -c Release --no-restore
python3 scripts/openapi_diff.py capture --output docs/openapi/task-management-v1.json
```

Review the snapshot diff before committing it. See
[OpenAPI contract change reporting](docs/agentic-sdlc.md#openapi-contract-change-reporting)
for the baseline and advisory policy.

## Performance smoke test

A non-destructive [k6](https://k6.io) load test runs the API locally in CI on
pull requests touching `src/TaskManagement.Api` or `tests/performance`, and
reports (advisory-only) against an explicit, human-reviewed baseline via
`scripts/perf_gate.py`. See [docs/agentic-sdlc.md#performance-and-load-testing-gate](docs/agentic-sdlc.md#performance-and-load-testing-gate)
for the design, baseline lifecycle, and the manual, opt-in staging smoke mode.

## Mutation testing pilot

The path-scoped [Mutation testing workflow](.github/workflows/mutation-testing.yml)
runs Stryker.NET against `TaskService.cs` and the focused `TaskServiceTests`.
It is advisory: the mutation score is reported in the job summary and the HTML
report is uploaded as a 14-day artifact; no score threshold or required check is
enforced. The job has a 15-minute timeout.

Run the same pilot locally with the pinned tool:

```bash
dotnet tool restore
cd tests/TaskManagement.Api.Tests
dotnet stryker
```

The tool manifest pins Stryker.NET 4.16.0 for the repository's .NET 8 runtime.
Keep the app and test projects on `net8.0`; coordinate any tool upgrade with its
runtime requirements. See [Mutation testing](docs/agentic-sdlc.md#mutation-testing)
for scope and CI policy.

## EF Core migration safety

Pull requests changing EF Core migrations or the data model run a read-only,
advisory migration check. It verifies the model snapshot, reports potentially
destructive operations in newly added migrations, and uploads an idempotent SQL
script for those migrations. The check uses a design-time context and makes no
database connections; it has no secrets or real-environment database access
and does not block on migration findings. Tooling errors remain visible as job
failures.
See [EF Core migration safety](docs/agentic-sdlc.md#ef-core-migration-safety)
for scope and limitations.

## Architecture

- Read the [manager-friendly project overview](docs/project-overview.md) for a plain-language explanation of the application and the Agentic SDLC demonstration.
- **Controllers** expose HTTP endpoints and map service results to HTTP responses.
- **Services** enforce business rules, including non-past due dates and the `Todo -> InProgress -> Done` status sequence.
- **Repositories** isolate EF Core data access. The app uses SQLite locally; integration tests replace it with EF Core InMemory.
- **DTOs** define the API contract and validation boundary so EF entities are never returned directly.

Read [docs/agentic-sdlc.md](docs/agentic-sdlc.md) for the complete issue-to-merge
workflow, the [Agent roster](docs/agentic-sdlc.md#agent-roster), and repository
settings the owner must enable.
Read the [agent security policy](docs/agent-security-policy.md) for prompt-injection,
least-privilege, and human-approval guardrails.
For design changes, use the [ADR guide](docs/adr/README.md) and
[human design review checklist](docs/design-review.md) in the pull request.

Review the [SDLC metrics dashboard](https://github.com/sahad7793/agetic-sdlc-demo/issues/28)
for delivery speed, human review effort, the advisory Stryker mutation score,
CI/deployment reliability, and agentic execution outcomes.
See the [metric definitions and limitations](docs/agentic-sdlc.md#sdlc-metrics-dashboard)
before comparing periods.

Use **Actions > Release notes > Run workflow** to generate a truthful, human-reviewable
draft of merged changes without publishing anything; see
[Release notes](docs/agentic-sdlc.md#release-notes) for the first-release readiness
checklist, exact human publication gate, provenance, permissions, and limitations.
