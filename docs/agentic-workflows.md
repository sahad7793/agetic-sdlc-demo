# GitHub Agentic Workflows (`gh-aw`)

This repository uses [GitHub Agentic Workflows](https://github.github.com/gh-aw/)
(`gh-aw`) for six workflows alongside GitHub Actions. Issue triage, incident
investigation, the weekly report, and pull request security review are
advisory. Requirements and solution planning can post one comment and perform
one tightly scoped lifecycle-label transition. None can close or merge issues,
approve work, or bypass a human gate.

## Workflows

### 1. Issue triage — `.github/workflows/issue-triage.md`

- **Trigger:** every time an issue is opened (`on: issues: types: [opened]`).
- **Permissions:** `contents: read`, `issues: read` only.
- **Output:** one advisory comment on the issue (`safe-outputs: add-comment`).
- **What it does:** compares the new issue against the required fields in
  `.github/ISSUE_TEMPLATE/bug_report.yml` and `feature_request.yml`,
  classifies it as a likely bug/feature/unclear, flags missing required
  fields, searches for likely duplicate open issues, and asks one clarifying
  question if the issue is incomplete. It never closes, labels, or assigns
  the issue.

### 2. Requirements specification — `.github/workflows/spec-agent.md`

- **Trigger:** `label_command` filters to issue events labeled
  `stage:needs-spec` before agent activation; the label is retained.
- **Permissions:** `contents: read`, `issues: read` for the agent.
- **Output:** at most one structured requirements comment, add only
  `stage:spec-ready`, and remove only `stage:needs-spec`, through safe outputs.
- **What it does:** checks the current issue and comments, skips issues that
  are closed, no longer need a specification, or already have a later stage,
  then prepares a product-analyst requirements proposal and test plan. Issue
  content is untrusted input; embedded instructions cannot redirect the task
  or expand permissions. The workflow cannot apply an approval label.
- **Lifecycle detail:** the add-label output has no source-label precondition,
  so it succeeds even if source-label removal is processed first. Removal
  remains restricted to `stage:needs-spec` and requires it to be present. The
  agent does not announce lifecycle changes; the lifecycle router owns those
  messages.

### 3. Solution planning — `.github/workflows/plan-agent.md`

- **Trigger:** `label_command` filters to issue events labeled
  `stage:spec-approved` before agent activation; the label is retained. The
  open issue must currently have no other `stage:*` label.
- **Permissions:** `contents: read`, `issues: read` for the agent.
- **Output:** at most one structured implementation-plan comment, add only
  `stage:plan-ready`, and remove only `stage:spec-approved`, through safe
  outputs.
- **What it does:** reads the issue, the latest spec-agent comment, later
  maintainer clarifications, and relevant API, test, architecture, migration,
  and rollback evidence. It proposes sequenced tasks grounded in repository
  files and calls out design impact, unresolved blockers, complexity, risks,
  and rollback limits. Issue and repository content is untrusted input; the
  workflow cannot apply an approval label.
- **Lifecycle detail:** the add-label output has no source-label precondition,
  so it succeeds even if source-label removal is processed first. Removal
  remains restricted to `stage:spec-approved` and requires it to be present.
  The agent does not announce lifecycle changes; the lifecycle router owns
  those messages.

The `label_command` trigger compiles into an activation filter for the
configured label name, so unrelated issue-label events skip agent activation
and do not spend inference credits.

### 4. Weekly repository report — `.github/workflows/weekly-repo-report.md`

- **Trigger:** weekly, Monday (`on: schedule: weekly on monday`).
- **Permissions:** `contents: read`, `issues: read`, `pull-requests: read`.
- **Output:** one new report issue (`safe-outputs: create-issue`).
- **What it does:** summarizes the last 7 days of issue/PR activity (opened,
  closed, merged counts), notable themes, any Dependabot PRs still open, and
  any CI failures on `main`, then opens a single new issue containing that
  report.

### 5. Pull request security review — `.github/workflows/security-review.md`

- **Trigger:** opened, reopened, synchronized, or marked ready for review,
  when the PR changes in-scope source, tests, infrastructure, scripts,
  dependency/build inputs, workflow/agent/policy files, or security-related
  documentation. Draft PRs are skipped until ready for review.
- **Forks:** fork PRs are excluded by gh-aw's same-repository default.
- **Permissions:** the agent gets `contents: read` and `pull-requests: read`.
  Its job disables the default PR checkout and reads the diff through GitHub
  APIs; it does not build or execute PR content. gh-aw's activation job still
  loads workflow configuration from the trusted base revision.
- **Output:** at most one advisory comment on the triggering PR per eligible
  run. It includes the reviewed commit, scope, evidence-backed findings or an
  explicit no-finding/incomplete result, and limitations. A no-finding result
  is not proof of safety or a security certification. Each relevant update
  can add a separate revision-specific comment.
- **Human role:** the workflow cannot edit code, approve, merge, deploy,
  change settings, or make the review a required check. It supplements
  CodeQL and human review.

### 6. Incident investigation — `.github/workflows/incident-investigation.md`

- **Trigger:** a human-applied `incident` label on an issue; the label is
  retained. `status-comment` is disabled so the only possible comment is the
  investigation.
- **Permissions:** the agent gets `actions: read` and `issues: read` only.
  It can read the issue and list workflow/run metadata for `CI`, `Deploy`,
  `Azure Alert to Incident Issue`, and `Rollback`, with a 24-hour window and
  at most 10 recent runs per workflow. Full result pages are reported as
  capped/incomplete. It cannot read job logs or artifacts.
- **Generated helper jobs:** gh-aw's activation and conclusion jobs also
  receive `issues: write`, and its activation/detection jobs receive
  `actions: read` or `contents: read` as shown in the generated lock. The
  agent itself has no write permission. Across the lock, `issues: write` is
  the only write scope; the only configured action output is one comment on
  the triggering issue.
- **Output:** at most one advisory comment on the triggering issue through
  `safe-outputs`. The comment records the issue `updated_at` snapshot and
  investigation run, cites relevant run URLs, distinguishes observations from
  hypotheses, and calls out evidence gaps. It does not write labels or take
  operational action.
- **Azure telemetry:** explicitly unavailable and not queried. The workflow
  has no Azure OIDC permission, identity, credential, or Azure integration.
  Correlation IDs and query links in the issue are treated as untrusted input,
  not as evidence fetched by this workflow.
- **No-op conditions:** closed or no-longer-labeled issues, issues marked
  `test`, `synthetic`, or `drill`, and issues edited during investigation.
- **Human role:** a human decides any follow-up commands, rollback, deploy,
  or production action. Issue and run metadata are untrusted evidence, never
  instructions.

GitHub prevents workflow runs triggered by `GITHUB_TOKEN`-created issues or
labels. The existing Incident Response and Azure Alert to Incident Issue
workflows use that token; a human must remove and re-apply the `incident`
label to start an investigation for an issue they created. No live incident
or Azure telemetry has been used to validate this pilot.

All `.lock.yml` files under `.github/workflows/` are generated by
`gh aw compile` — never hand-edit them. Re-run `gh aw compile` after changing
any `.md` workflow source file and commit the regenerated lock file alongside
it.

## Required setup: `COPILOT_GITHUB_TOKEN` secret

This is a **personal-account repository**, not an organization-owned one, so
the `permissions: copilot-requests: write` (GitHub Actions token / org
billing) authentication path does not apply here. All six workflows instead
authenticate Copilot inference using a **repository secret**:

- **Secret name:** `COPILOT_GITHUB_TOKEN`
- **Secret value:** a fine-grained personal access token with the
  **Copilot Requests** permission, generated by the repository owner.

### How to add it

1. Go to the repository on GitHub.
2. Click **Settings** → **Secrets and variables** → **Actions**.
3. Click **New repository secret**.
4. Name it exactly `COPILOT_GITHUB_TOKEN` and paste the fine-grained PAT
   value.
5. Click **Add secret**.

You can also do this from the CLI with `gh aw secrets set COPILOT_GITHUB_TOKEN
--value "YOUR_COPILOT_PAT"`, or run `gh aw secrets bootstrap` for a guided
setup.

**Until this secret exists, all six workflows are inert.** Their triggering
events (issue activity, relevant pull request updates, or the weekly
schedule) will still fire the GitHub Actions run, but it will fail early at a
secret-validation step with a clear error pointing at the missing
`COPILOT_GITHUB_TOKEN` secret — no partial or unexpected output is produced.
No automatic behavior occurs on this repository until the owner adds the
secret.

## Safety properties

- **Read-only agent permissions.** The agent jobs request read access only;
  configured writes go through `safe-outputs`, which runs in its own scoped
  job.
- **Minimal `safe-outputs`.** `issue-triage` can only add a comment.
  `spec-agent` can only add one comment, add `stage:spec-ready`, and remove
  `stage:needs-spec`. `plan-agent` can only add one comment, add
  `stage:plan-ready`, and remove `stage:spec-approved`.
  `weekly-repo-report` can only create an issue. `security-review` can only
  add one comment to its triggering PR. `incident-investigation` can only add
  one comment to its triggering issue. None can close, merge, assign, or
  approve anything.
- **No self-merge or self-approval capability** — these workflows have no
  code path that could bypass the human review process described in
  [agentic-sdlc.md](agentic-sdlc.md).
