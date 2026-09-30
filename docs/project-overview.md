# Project Overview: Task Management API and Agentic SDLC Demo

## Executive summary

This repository has two related purposes:

1. It contains a small **Task Management API** built with ASP.NET Core on .NET 8. The API lets a caller create, view, update, complete, and delete tasks.
2. It demonstrates a **human-governed, AI-assisted software delivery process**. GitHub workflows and agent profiles show how a proposed change can move from an issue, through requirements and design review, implementation, automated checks, human review, and controlled deployment.

The API is the example application; the delivery process is the broader demonstration. The project is intended to show how AI can help a team deliver and operate software while people retain authority over scope, design approval, code review, merges, production deployment, and recovery.

## Why the project exists

The project provides a practical reference for teams exploring agent-assisted software development. It demonstrates how to:

- Keep an AI agent's work tied to an approved issue and clear acceptance criteria.
- Divide responsibilities across requirements, design, implementation, testing, review, and release preparation.
- Run repeatable quality and governance checks in GitHub Actions.
- Deploy a validated application artifact to staging and, after a human approval gate, production.
- Make delivery and operational activity visible and auditable rather than relying on informal steps.

It is a reference implementation and demonstration, not a complete commercial task-management product or a general-purpose autonomous software engineer.

## What the application does

The API exposes task operations under `/api/tasks`. A task has a title, optional description, status, priority, optional due date, and creation timestamp.

| Capability | What it does |
| --- | --- |
| Create and retrieve tasks | Create a task, list tasks, or retrieve one by its ID. |
| Filter tasks | List by status or due-date range, and retrieve overdue tasks. |
| Update and delete | Change task details or remove a task. |
| Progress status | Move a task through `Todo → InProgress → Done`. Other status transitions are rejected. |
| Apply basic rules | Validate request fields, reject due dates in the past, and keep persistence details out of the HTTP contract. |
| Check service health | Expose `/health` for deployment checks and availability monitoring. |

The service follows a layered design: **Controllers** handle HTTP, **Services** enforce business rules, **Repositories** handle database access, and **DTOs** define the request and response contracts. Entity Framework Core is used for persistence.

## How a change moves through the delivery process

The intended path is:

1. A person files an issue with scope and testable acceptance criteria.
2. A maintainer reviews the requirements and approves the work before implementation.
3. An agent or developer prepares a solution design; a person reviews and approves design-impacting decisions.
4. An agent or developer implements the approved change on an isolated branch and opens a pull request.
5. GitHub Actions runs build, tests, coverage reporting, and CodeQL analysis. Relevant changes can also receive advisory checks such as API contract, migration, performance, mutation, and security reports.
6. A person reviews the code and results, approves the pull request, and merges it. Agents do not approve or merge their own work.
7. After CI succeeds on `main`, the deployment workflow builds an immutable container image, deploys it to staging, and checks `/health`. Production uses the same image and is gated by the GitHub production-environment approval.

Other repository automation supports this path with items such as lifecycle labels, issue-to-pull-request traceability, release-note drafts, rollback, incident issue creation, dependency and cost-governance reports, and SDLC metrics. These capabilities are designed to provide evidence and structure; advisory reports do not replace human judgment.

## Deployment and operations

The application can run locally using SQLite. The Azure deployment definition uses Azure Container Apps and Azure SQL, with managed identities for application access to Azure resources and Microsoft Entra-only SQL authentication. Application telemetry is wired to Azure Monitor/Application Insights, with health, availability, error-rate, restart, and latency monitoring defined in infrastructure code.

The repository's deployment notes record a successful staging deployment and provisioned production infrastructure, with production application deployment subject to its approval gate. This describes the recorded project state, not a live check of Azure resources; confirm current environment status with the owner before relying on it.

## Scope and current limitations

- The application is intentionally small and API-only; this repository does not include a user-facing web or mobile interface.
- The task API does not implement user accounts, tenant separation, or an application-level authentication/authorization model.
- Task data and rules are illustrative and are not a substitute for requirements analysis for a production task-management product.
- The agentic workflows are governance and delivery examples. Some checks are advisory or manually triggered, and GitHub/Azure settings such as branch protection, environment approvals, identities, and secrets/variables must be configured by repository or cloud administrators.
- A green build, a healthy endpoint, or an advisory agent report is useful evidence, but none alone proves that the application meets every business or security requirement.

## A simple way to explain it

> “This is a .NET task API used as a realistic example application, combined with a GitHub-based demonstration of how AI agents can help with requirements, design, coding, and testing. The workflows add repeatable checks and deployment evidence, while people keep control of approvals, merges, production releases, and incident decisions.”

## Where to look next

- [Repository README](../README.md) — local setup, tests, and technical entry points.
- [Agentic SDLC workflow](agentic-sdlc.md) — workflow stages, agent roles, deployment, and operating guidance.
- [Agent security policy](agent-security-policy.md) — boundaries for untrusted content, permissions, and human approvals.
- [Azure deployment plan](../.azure/deployment-plan.md) — implementation and validation notes for the Azure deployment.
