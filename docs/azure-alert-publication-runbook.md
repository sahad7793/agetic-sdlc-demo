# Azure Alert Publication Runbook

This runbook explains how to trigger and monitor Azure alert-to-issue publication, including manual targeted publication and scheduled multi-alert publication.

## Quick Reference: Variable State

### Required Always
- **AZURE_ALERTS_ENABLED**: `'true'` — Enables the entire alert polling infrastructure. Without this, no polls run.

### For Publication (Manual and Scheduled)
- **AZURE_ALERTS_PUBLISH_ENABLED**: `'true'` — Enables issue creation. Required for both manual targeted publication AND legacy scheduled publication (backward compatibility). Manual publish: **required**. Scheduled publish: required unless `AZURE_ALERTS_SCHEDULE_ENABLED` is set.

### For Scheduled Publication Only (New)
- **AZURE_ALERTS_SCHEDULE_ENABLED**: `'true'` — Enables scheduled multi-alert publication without requiring `AZURE_ALERTS_PUBLISH_ENABLED`. Optional. If set to `'true'`, scheduled runs publish alerts even if `AZURE_ALERTS_PUBLISH_ENABLED` is absent or `'false'`.

| Scenario | AZURE_ALERTS_ENABLED | AZURE_ALERTS_PUBLISH_ENABLED | AZURE_ALERTS_SCHEDULE_ENABLED | Polls Run | Publishes | Notes |
|----------|--------|----------|----------|-----------|-----------|--------|
| **Disabled** | (any) | (any) | (any) | ❌ No | ❌ No | Entire system off |
| **Manual poll (dry-run)** | `'true'` | (any) | (any) | ✅ Yes | ❌ No | Inspect alerts without publishing |
| **Manual publish (targeted)** | `'true'` | `'true'` | (any) | ✅ Yes | ✅ Yes | **Requires 64-hex alert fingerprint** |
| **Scheduled (legacy)** | `'true'` | `'true'` | (any) | ✅ Yes | ✅ Yes | Backward compatible; publishes all fired alerts |
| **Scheduled (new)** | `'true'` | (any) | `'true'` | ✅ Yes | ✅ Yes | New flag; independent of AZURE_ALERTS_PUBLISH_ENABLED |
| **Scheduled (disabled)** | `'true'` | `'false'` | (absent/false) | ✅ Yes | ❌ No | Polls run but don't publish |

## Manual Targeted Publication

### Workflow Dispatch: When and How

**When to use**: Publish a **single, specific alert** identified by its exact 64-hex fingerprint. Use this when you want to gate publication to a known, validated alert.

**How to trigger**:

1. Go to **Actions** → **Azure Alert to Issue** → **Run workflow**
2. Select branch: `main` (or your working branch)
3. Fill in:
   - **mode**: `publish` (triggers alert poll + issue creation)
   - **publish**: `true` (enables issue creation)
   - **alert_fingerprint**: Paste the exact **64-hex fingerprint** (required)
4. Click **Run workflow**

### Finding the Alert Fingerprint

Alert fingerprints are **deterministic hashes** of alert rule name and time-series attributes. They appear in:

1. **GitHub issue body** (existing alerts already published):
   - Look for `fingerprint: <64-hex>` in the issue description under "Alert Details"

2. **Workflow logs** (from recent scheduled or manual dry-run):
   - Go to **Actions** → workflow run
   - Check **poll-dry-run** or **poll-publish** logs
   - Search for `fingerprint:` (each fired alert is logged with its fingerprint during poll)

3. **Azure Monitor** → your alert rule → Fired Alerts tab:
   - No fingerprint shown directly in Azure UI
   - Run a manual dry-run (`mode: poll, publish: false`) to see all fingerprints

### Behavior: Manual Targeted Publish

- **Accepts fingerprint**: Polls Azure, matches alert by exact 64-hex fingerprint, creates one issue if alert is new
- **Rejects ambiguous/missing fingerprints**: Fails with explicit error if:
  - Fingerprint is empty or malformed (not 64 hex)
  - No alert matched the fingerprint
  - Multiple alerts matched the same fingerprint (collision; contact support)
- **No success-shaped skip**: If fingerprint validation fails, the workflow run shows RED (failed) with clear error message, never green
- **Idempotent**: If alert was already published (deduplication by fingerprint), the workflow runs successfully but creates no new issue

### Example Trigger Command (CLI)

```bash
gh workflow run azure-alert-to-issue.yml \
  --ref main \
  -f mode=publish \
  -f publish=true \
  -f alert_fingerprint="a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0u1v2w3x4y5z6a7b8c9d0e1f2"
```

(Fingerprint must be exactly 64 lowercase hexadecimal characters.)

## Manual Dry-Run (Poll Only)

**When to use**: Inspect all fired alerts without creating issues. Validate fingerprints or test configuration.

**How to trigger**:
1. Go to **Actions** → **Azure Alert to Issue** → **Run workflow**
2. Select branch: `main`
3. Fill in:
   - **mode**: `poll` (triggers alert poll only)
   - **publish**: `false` (no issue creation)
   - **alert_fingerprint**: (leave empty—ignored for dry-run)
4. Click **Run workflow**

**Behavior**: Polls Azure, logs all fired alerts with their fingerprints, creates no issues. Check logs to find fingerprints for targeted publish.

## Scheduled Publication

### How Scheduled Runs Work

A **cron schedule** (`0 */2 * * *`) polls Azure every 2 hours. Fires publish only if **at least one** of the publication flags is `'true'`:

```
if: AZURE_ALERTS_ENABLED == 'true' && 
    (AZURE_ALERTS_PUBLISH_ENABLED == 'true' OR AZURE_ALERTS_SCHEDULE_ENABLED == 'true')
```

When fired, publishes **all new alerts** (deduplication by fingerprint prevents re-publishing).

### Controlling Scheduled Publication

**To disable scheduled publish** (keep polling but don't create issues):
```
AZURE_ALERTS_ENABLED: true
AZURE_ALERTS_PUBLISH_ENABLED: false (or unset)
AZURE_ALERTS_SCHEDULE_ENABLED: false (or unset)
```

**To enable scheduled publish** (backward compatible, use legacy flag):
```
AZURE_ALERTS_ENABLED: true
AZURE_ALERTS_PUBLISH_ENABLED: true
```

**To enable scheduled publish** (new separate flag):
```
AZURE_ALERTS_ENABLED: true
AZURE_ALERTS_SCHEDULE_ENABLED: true
```

## Guardrails and Fail-Closed Behavior

### What Fails Closed

1. **Missing AZURE_ALERTS_ENABLED**: Entire system disabled (no polls)
2. **Manual publish without fingerprint**: Workflow run fails red with error `"Manual alert publishing requires a target alert fingerprint (64-hex)."`
3. **Fingerprint mismatch**:
   - No alert matched: Error `"No alert matched fingerprint <X>"`, workflow run fails red
   - Multiple alerts matched: Error `"Multiple alerts matched fingerprint <X>"`, workflow run fails red
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

- **mode**: `poll` or `publish` (required; controls alert polling)
- **publish**: `true` or `false` (required; `true` enables issue creation only for mode=publish)
- **alert_fingerprint**: 64-hex string (optional; required if `publish=true`)

### Repository Variables (Settings → Variables and Secrets)

- **AZURE_ALERTS_ENABLED**: `'true'` (enables polling infrastructure)
- **AZURE_ALERTS_PUBLISH_ENABLED**: `'true'` (enables manual publish and/or legacy scheduled publish)
- **AZURE_ALERTS_SCHEDULE_ENABLED**: `'true'` (enables new scheduled publish path)

Set these at the repository level in GitHub Settings. They persist across runs.

### Environment: azure-alerts

The workflow uses a protected environment `azure-alerts` with OIDC-based Azure login (no secrets stored). Only intended for prod Azure alerts; manual runs against other subscriptions require separate environment setup.

## Troubleshooting

### "publication is not explicitly enabled"
- Manual publish: Set `AZURE_ALERTS_PUBLISH_ENABLED: 'true'` in repo variables
- Scheduled publish: Set `AZURE_ALERTS_PUBLISH_ENABLED: 'true'` OR `AZURE_ALERTS_SCHEDULE_ENABLED: 'true'`

### "Manual alert publishing requires a target alert fingerprint (64-hex)"
- You triggered manual publish (`mode=publish, publish=true`) but left `alert_fingerprint` empty or provided invalid hex
- Get the fingerprint from a dry-run, or find it in an existing GitHub issue body
- Retry with valid 64-hex fingerprint

### "No alert matched fingerprint X"
- The fingerprint doesn't exist in current Azure alerts
- Run a dry-run to see all current fingerprints
- Verify the fingerprint was copied exactly (case-insensitive, but must be valid hex)

### "Multiple alerts matched fingerprint X"
- Rare collision (same rule + attributes); contact support
- Workaround: Not available; you'll need manual intervention in Azure

### Scheduled run never fires
- Check `AZURE_ALERTS_ENABLED: 'true'` is set
- Check `AZURE_ALERTS_PUBLISH_ENABLED: 'true'` OR `AZURE_ALERTS_SCHEDULE_ENABLED: 'true'` is set
- Check Azure environment (azure-alerts) is configured with valid OIDC credentials
- Check cron schedule (currently `0 */2 * * *` = every 2 hours UTC)

## Related Documentation

- **Workflow file**: `.github/workflows/azure-alert-to-issue.yml`
- **Alert polling script**: `scripts/azure_alert_to_issue.py`
- **Tests**: `tests/azure_alert_to_issue/`
- **Fixture (test alert)**: `tests/azure_alert_to_issue/fixtures/fired_alert.json`
