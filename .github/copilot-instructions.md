# Copilot instructions

This repository is a small .NET 8 Task Management API and an Agentic SDLC demonstration. Preserve its layered structure:

- `Controllers` define HTTP behavior only.
- `Services` contain business rules and orchestrate application work.
- `Repositories` own EF Core access.
- `DTOs` are the request/response contracts; do not expose EF entities.

## Conventions

- Use nullable reference types and idiomatic C# naming: PascalCase public members, camelCase locals and parameters.
- Use asynchronous APIs all the way through I/O paths and accept/pass `CancellationToken` for endpoint-driven operations.
- Add business rules in services, not controllers or repositories, and return correct HTTP status codes through controllers.
- Add or update focused service tests for every new business rule. Add integration tests under `tests/TaskManagement.Api.Tests` when endpoint behavior changes.
- Keep existing tests unless a linked issue explicitly changes their expected behavior.
- Do not add NuGet dependencies without a clear justification in the pull request description.
- Keep the API, its tests, and documentation buildable with .NET 8 and preserve the current layered architecture.
