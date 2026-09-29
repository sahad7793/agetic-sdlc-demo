# Threat model: <change or system>

- **Status:** Draft
- **Date:** YYYY-MM-DD
- **Related issue / PR:** #
- **Author / reviewer:** TBD
- **Scope:** <affected components and change>

## System and trust boundaries

Describe actors, components, data flows, and trust boundaries. Cite the
relevant code, configuration, and infrastructure files. Distinguish current
controls from proposed controls and note evidence that is unavailable.

## Design impact

Classify the change as design-impacting when it includes any of:

- A new endpoint or API/request/response contract change
- A data schema change or migration
- Authentication, authorization, or identity changes
- Infrastructure-as-code or deployment-topology changes
- Workflow trigger, permission, or safe-output changes
- A new dependency or external integration

For each criterion, state **Changed** or **Not changed** and cite evidence.
Also note relevant configuration changes.

## STRIDE analysis

For each relevant threat, include an evidence-based mitigation and an
actionable follow-up. State when a category has no relevant threat in scope.

| Category | Asset | Threat scenario | Existing mitigation (file citation) | Residual risk | Recommended follow-up |
| --- | --- | --- | --- | --- | --- |
| Spoofing | | | | | |
| Tampering | | | | | |
| Repudiation | | | | | |
| Information disclosure | | | | | |
| Denial of service | | | | | |
| Elevation of privilege | | | | | |

## Architecture decision

Recommend **ADR needed** or **ADR not needed**, with a reason. Use
[`docs/adr/README.md`](../adr/README.md) to decide whether the change creates
a lasting decision about service boundaries, storage, public API contracts,
identity/trust boundaries, or deployment topology. An ADR and this threat
model do not replace CI, human review, or approval gates.

## Verification and open questions

List focused tests, operational checks, unresolved evidence gaps, and any
compatibility, migration, rollback, or recovery concerns.
