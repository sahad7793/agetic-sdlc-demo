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

## Architecture

- **Controllers** expose HTTP endpoints and map service results to HTTP responses.
- **Services** enforce business rules, including non-past due dates and the `Todo -> InProgress -> Done` status sequence.
- **Repositories** isolate EF Core data access. The app uses SQLite locally; integration tests replace it with EF Core InMemory.
- **DTOs** define the API contract and validation boundary so EF entities are never returned directly.

Read [docs/agentic-sdlc.md](docs/agentic-sdlc.md) for the complete issue-to-merge workflow and the repository settings the owner must enable.
