# Azure Alert Publication Runbook

This runbook explains how to trigger and monitor Azure alert-to-issue publication, including manual targeted publication and scheduled multi-alert publication.

## Quick Reference: Variable State

### Required Always
- **AZURE_ALERTS_ENABLED**: `'true'` — Enables the entire alert polling infrastructure. Without this, no polls run.

### For Manual Targeted Publication
- **AZURE_ALERTS_PUBLISH_ENABLED**: `'true'` — Enables manual issue creation. Required for manual targeted publication; it does not enable scheduled publication.

### For Scheduled Publication Only
- **AZURE_ALERTS_SCHEDULE_ENABLED**: `'true'` — Explicitly enables the schedule. It must be used with **AZURE_ALERTS_PUBLISH_ENABLED** set to `'true'`; either unset or any value other than `'true'` disables scheduled polling and publication.

| Scenario | AZURE_ALERTS_ENABLED | AZURE_ALERTS_PUBLISH_ENABLED | AZURE_ALERTS_SCHEDULE_ENABLED | Polls Run | Publishes | Notes |
|----------|--------|----------|----------|-----------|-----------|--------|
| **Disabled** | (any) | (any) | (any) | ❌ No | ❌ No | Entire system off |
| **Manual poll (dry-run)** | `'true'` | (any) | (any) | ✅ Yes | ❌ No | Inspect alerts without publishing |
| **Manual publish (targeted)** | `'true'` | `'true'` | (any) | ✅ Yes | ✅ Yes | **Requires 64-hex alert fingerprint** |
| **Scheduled (enabled)** | `'true'` | `'true'` | `'true'` | ✅ Yes | ✅ Yes | Publishes all fired alerts |
| **Scheduled (disabled)** | `'true'` | (any) | (unset/other) | ❌ No | ❌ No | Schedule is not authorized |

## Manual Targeted Publication

### Workflow Dispatch: When and How

**When to use**: Publish a **single, specific alert** identified by its exact 64-hex fingerprint. Use this when you want to gate publication to a known, validated alert.

**How to trigger**:

1. Go to **Actions** → **Azure Alert to Issue** → **Run workflow**
2. Select branch: `main` (or your working branch)
3. Fill in:
   - **mode**: `poll` (triggers the configured live alert poll)
   - **publish**: `true` (enables issue creation)
   - **alert_fingerprint**: Paste the exact **64-hex fingerprint** (required)
4. Click **Run workflow**

### Finding the Alert Fingerprint

Alert fingerprints are the SHA-256 hash of the Azure alert instance ID after trimming whitespace. They appear in:

1. **GitHub issue body** (existing alerts already published):
   - Look for the `azure-alert-fingerprint` marker; the 64-hex fingerprint is inside the marker.

2. **Azure Monitor** → the fired alert instance:
   - Copy the alert instance ID and calculate its SHA-256 hash after trimming leading and trailing whitespace.
   - The poller does not log alert payloads or fingerprints; read-only dry-run counts cannot be used to retrieve a fingerprint.

### Behavior: Manual Targeted Publish

- **Accepts fingerprint**: Polls all configured bounded scopes, matches exactly one eligible alert by its exact 64-hex fingerprint, and creates at most that issue if it is new.
- **Rejects ambiguous/missing fingerprints**: Fails with explicit error if:
  - Fingerprint is empty or malformed (not exactly 64 hex)
  - No alert matched the fingerprint
  - Multiple alerts matched the same fingerprint (collision; contact support)
- **No success-shaped skip**: If fingerprint validation fails, the workflow run shows RED (failed) with clear error message, never green
- **Idempotent**: If alert was already published (deduplication by fingerprint), the workflow runs successfully but creates no new issue

### Example Trigger Command (CLI)

```bash
gh workflow run azure-alert-to-issue.yml \
  --ref main \
  -f mode=poll \
  -f publish=true \
  -f alert_fingerprint="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
```

(Fingerprint must be exactly 64 lowercase hexadecimal characters.)

## Manual Dry-Run (Poll Only)

**When to use**: Check that the configured Azure poll runs without creating issues.

**How to trigger**:
1. Go to **Actions** → **Azure Alert to Issue** → **Run workflow**
2. Select branch: `main`
3. Fill in:
   - **mode**: `poll` (triggers alert poll only)
   - **publish**: `false` (no issue creation)
   - **alert_fingerprint**: (leave empty—ignored for dry-run)
4. Click **Run workflow**

**Behavior**: Polls Azure and reports aggregate counts without creating issues or logging alert payloads. A dry run does not reveal fingerprints.

## Scheduled Publication

### How Scheduled Runs Work

A **cron schedule** (`17 */2 * * *`) runs every 2 hours. It starts only when `AZURE_ALERTS_ENABLED`, `AZURE_ALERTS_SCHEDULE_ENABLED`, and `AZURE_ALERTS_PUBLISH_ENABLED` are all `'true'`:

```
if: AZURE_ALERTS_ENABLED == 'true' &&
    AZURE_ALERTS_SCHEDULE_ENABLED == 'true' &&
    AZURE_ALERTS_PUBLISH_ENABLED == 'true'
```

When fired, publishes **all new alerts** (deduplication by fingerprint prevents re-publishing).

### Controlling Scheduled Publication

**To disable scheduled publication**:
```
AZURE_ALERTS_ENABLED: true
AZURE_ALERTS_SCHEDULE_ENABLED: false (or unset)
```

**To enable scheduled publish**:
```
AZURE_ALERTS_ENABLED: true
AZURE_ALERTS_SCHEDULE_ENABLED: true
AZURE_ALERTS_PUBLISH_ENABLED: true
```

## Guardrails and Fail-Closed Behavior

### What Fails Closed

1. **Missing AZURE_ALERTS_ENABLED**: Entire system disabled (no polls)
2. **Manual publish without fingerprint**: Workflow run fails red with error `"Manual issue publication requires an alert fingerprint."`
3. **Fingerprint mismatch**:
   - No alert matched: the run fails with an explicit not-found error before any GitHub API call.
   - Multiple alerts matched: the run fails with an explicit ambiguity error before any GitHub API call.
4. **Missing credential**: If Azure login fails, workflow fails red (no fallback or silent dry-run)

### What Does NOT Create Issues

- **Dry-runs**: `publish: false` never creates issues
- **Fixture testing**: Pushes to this workflow file run against test fixtures (no Azure access, no issue creation)
- **Resolved alerts**: Only `'Fired'` severity alerts are published; resolved/cleared alerts are skipped
- **Duplicate alerts**: Same fingerprint only creates issue once; subsequent polls skip it

### No Success-Shaped Skips

Skipped alerts (duplicates, resolved) do not cause the workflow to fail or warn—they're silent. But **validation errors** (missing fingerprint, no match, multiple matches, bad config) always produce explicit error messages and red (failed) workflow status. You'll never see a green "all good, but we didn't do anything" run for publication requests.

## Workflow Inputs and Environment Variables

### Workflow Dispatch Inputs (Manual Trigger)

- **mode**: `fixture` or `poll` (required; controls offline fixture or live polling)
- **publish**: `true` or `false` (required; `true` enables issue creation only for mode=poll)
- **alert_fingerprint**: exactly 64 hexadecimal characters (optional; required if `publish=true`)

### Repository Variables (Settings → Variables and Secrets)

- **AZURE_ALERTS_ENABLED**: `'true'` (enables polling infrastructure)
- **AZURE_ALERTS_PUBLISH_ENABLED**: `'true'` (enables manual targeted publication)
- **AZURE_ALERTS_SCHEDULE_ENABLED**: `'true'` (explicitly enables scheduled polling/publication only in combination with `AZURE_ALERTS_PUBLISH_ENABLED`; unset disables it)

Set these at the repository level in GitHub Settings. They persist across runs.

### Environment: azure-alerts

The workflow uses a protected environment `azure-alerts` with OIDC-based Azure login (no secrets stored). Only intended for prod Azure alerts; manual runs against other subscriptions require separate environment setup.

## Troubleshooting

### "publication is not explicitly enabled"
- Manual publish: Set `AZURE_ALERTS_PUBLISH_ENABLED: 'true'` in repo variables
- Scheduled publish: Set both `AZURE_ALERTS_SCHEDULE_ENABLED: 'true'` and `AZURE_ALERTS_PUBLISH_ENABLED: 'true'`

### "Manual issue publication requires an alert fingerprint"
- You triggered manual publish (`mode=poll, publish=true`) but left `alert_fingerprint` empty or provided invalid hex
- Use the fingerprint from an existing GitHub issue body or calculate it from the alert instance ID; dry-run logs intentionally do not reveal it
- Retry with valid 64-hex fingerprint

### "Selected alert fingerprint was not found in the eligible alerts"
- The fingerprint doesn't identify exactly one current eligible Azure alert
- Confirm that the alert instance ID and its hash were copied/calculated exactly
- Verify the fingerprint was copied exactly (case-insensitive, but must be valid hex)

### "Selected alert fingerprint matched multiple eligible alerts"
- The same alert instance appeared more than once across configured scopes; review scope configuration to avoid duplicates.
- Workaround: Not available; you'll need manual intervention in Azure

### Scheduled run never fires
- Check `AZURE_ALERTS_ENABLED: 'true'` is set
- Check both `AZURE_ALERTS_SCHEDULE_ENABLED: 'true'` and `AZURE_ALERTS_PUBLISH_ENABLED: 'true'` are set
- Check Azure environment (azure-alerts) is configured with valid OIDC credentials
- Check cron schedule (currently `17 */2 * * *` = every 2 hours UTC)

## Related Documentation

- **Workflow file**: `.github/workflows/azure-alert-to-issue.yml`
- **Alert polling script**: `scripts/azure_alert_to_issue.py`
- **Tests**: `tests/azure_alert_to_issue/`
- **Fixture (test alert)**: `tests/azure_alert_to_issue/fixtures/fired_alert.json`
