## Summary of change

<!-- Explain the change and why it is needed. -->

## Linked issue

Closes #

<!-- If this approved change does not need an issue, add a separate line:
No-Issue: brief reason. -->

Follow the [agent security policy](../docs/agent-security-policy.md) when using
agents or processing external content.

## Validation

<!-- List tests run and tests added or updated. -->

- [ ] I linked the approved issue.
- [ ] I summarized the change and its impact.
- [ ] I added or updated tests for changed behavior.
- [ ] CI is green before I request review.
- [ ] Any new NuGet package is justified in this description.

## Design and threat-model review (when relevant)

For API contract, data, identity, workflow permission, or deployment changes,
use `docs/design-review.md` and the current `docs/threat-model.md` to
summarize relevant risks and mitigations. Use
`docs/templates/threat-model-template.md` for a change-specific STRIDE
assessment when appropriate. Link an ADR from `docs/adr/` when the change
merits a lasting architecture decision; otherwise state N/A. A human reviewer
accepts the design.

- ADR (if needed) and design/security impact:

## Ownership-sensitive changes

<!-- See .github/CODEOWNERS for the exact routed paths. -->

- [ ] This PR touches `infra/`, `.github/workflows/`, a deploy/rollback/access
      script, or `docs/agentic-sdlc.md`/`docs/agentic-workflows.md`. If
      checked, I called out the specific security or deployment impact above.
