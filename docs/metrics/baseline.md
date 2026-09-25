### SDLC metrics
Repository: `sahad7793/agetic-sdlc-demo`. Schema: 1.
Collection started: 2026-09-25T07:03:46Z; finished: 2026-09-25T07:04:43Z.
Collector commit: `283113b5cfd89e2a435d4c4c9be9556562af55cc`.

> [!WARNING]
> Descriptive evidence, not proof that agents caused improvement. Small samples, incomplete issue links, author attribution, and retained history limit interpretation.

Current: **[2026-09-18T00:00:00Z, 2026-09-25T00:00:00Z)**.
Previous: **[2026-09-11T00:00:00Z, 2026-09-18T00:00:00Z)**.
Repository created: 2026-09-23T13:52:31Z. Observed hours: current 34.125, previous 0.
A period predating repository creation is partial/unavailable as a comparison, not a zero-activity baseline.

| Metric | Current | Previous |
| --- | --- | --- |
| Main PRs merged | 19 | N/A (repository did not exist) |
| Median PR creation to merge | 5.203 h (n=19) | N/A (repository did not exist) |
| PR cycle: dependabot | 14.543 h (n=13) | N/A (repository did not exist) |
| PR cycle: copilot_authored | 0.211 h (n=1) | N/A (repository did not exist) |
| PR cycle: other_or_unknown | 0.055 h (n=5) | N/A (repository did not exist) |
| Median linked issue creation to first main merge | 0.215 h (n=1) | N/A (repository did not exist) |
| Issue-link coverage (merged PRs) | 1/19 | N/A (repository did not exist) |
| Excluded external issue references | 3 | N/A (repository did not exist) |
| Excluded negative issue / PR durations | 0 / 0 | N/A (repository did not exist) |
| ci reliability | 89.04% (65/73 decisive; 73 total); action_required=3, failure=5, success=65 | N/A (repository did not exist) |
| ci rerun attempts | 3 | N/A (repository did not exist) |
| issue_triage reliability | 100.0% (1/1 decisive; 1 total); success=1 | N/A (repository did not exist) |
| issue_triage rerun attempts | 0 | N/A (repository did not exist) |
| weekly_report reliability | 100.0% (1/1 decisive; 1 total); success=1 | N/A (repository did not exist) |
| weekly_report rerun attempts | 0 | N/A (repository did not exist) |
| CI: pull_request | 85.19% (46/54 decisive; 54 total); action_required=3, failure=5, success=46 | N/A (repository did not exist) |
| CI: main_push | 100.0% (19/19 decisive; 19 total); success=19 | N/A (repository did not exist) |
| CI: other | N/A (0/0 decisive; 0 total); no attempts | N/A (repository did not exist) |
| staging delivery reliability | 50.0% (1/2 decisive; 2 total); failure=1, success=1 | N/A (repository did not exist) |
| staging successful deliveries / per day | 1 / N/A | N/A (repository did not exist) |
| production delivery reliability | N/A (0/0 decisive; 1 total); skipped=1 | N/A (repository did not exist) |
| production successful deliveries / per day | 0 / N/A | N/A (repository did not exist) |
| Dependabot PRs opened / merged to main | 14 / 13 | N/A (repository did not exist) |
| codeql alerts created / fixed / dismissed | 0 / 0 / 0 | N/A (repository did not exist) |
| dependabot alerts created / fixed / dismissed | 0 / 0 / 0 | N/A (repository did not exist) |

### Coverage and interpretation

- **Issue-to-deployment:** Unavailable: automatic environment SHA and workflow_run head_sha do not independently identify the image built from the triggering CI SHA. No issue-to-deployment link is inferred.
- Reliability = success / (success + failure + timed_out + startup_failure + action_required). Cancellation, skipped, neutral, stale, pending and unknown are shown separately, not failures.
- Reliability cohorts use attempt/job start time; outcomes reflect collection-time observations, not reconstructed period-end state. Repeated collection can revise older cohorts.
- Delivery frequency uses successful job completion time; reused job IDs count once. Per-day rates are withheld for partial periods. Production success means the pipeline completed, not verified runtime health.
- Same-repository explicit closing links only; each issue uses its earliest linked main merge. Unlinked work is not assigned a guessed lead time.
- CI includes build, tests and CodeQL together; execution success is not a defect or vulnerability count. Agentic execution success is not advice quality or proof of a published report.
- GitHub APIs are not transactional; observations span the collection interval. Deleted/expired runs and missing historical state cannot be recovered. Alert event timestamps show available latest transitions, not a complete event log.

### Current inventory (not historical period-end state)

- Open Dependabot PRs: 0.
- Unfinished delivery jobs: production: waiting=3. Waiting for approval is not failure.
- codeql alert inventory: 0 alerts returned.
- dependabot alert inventory: 0 alerts returned.

### Initial partial baseline

Available repository history: **[2026-09-23T13:52:31Z, 2026-09-25T07:03:46Z)** (41.188 hours); not a pre-agentic control period.
- Main merged PRs: 23; median cycle: 1.892 h (n=23).
- Linked issue median: 0.215 h (n=1); linked PR coverage: 1/23.
- CI: 90.12% (73/81 decisive; 81 total); action_required=3, failure=5, success=73.
- staging: 5 successful delivery completions; 83.33% (5/6 decisive; 6 total); failure=1, success=5.
- production: 2 successful delivery completions; 100.0% (2/2 decisive; 6 total); pending=3, skipped=1, success=2.
Full baseline breakdown is in the accompanying JSON; no per-day extrapolation is applied.

### Sources

Evidence: 24 PRs, 90 workflow attempts, 12 unique delivery jobs.
[Metric definitions and runbook](https://github.com/sahad7793/agetic-sdlc-demo/blob/main/docs/agentic-sdlc.md#sdlc-metrics-dashboard) | [Workflow runs](https://github.com/sahad7793/agetic-sdlc-demo/actions/workflows/sdlc-metrics.yml)
