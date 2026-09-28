# Human design and threat-model review

Use this brief checklist for PRs that change API contracts, data storage,
identity, workflow permissions, or deployment design. Record relevant risks
and mitigations in the PR or a linked [ADR](adr/README.md); mark unrelated
items not applicable. This is reviewer guidance, not an automated gate.

- **Boundaries and data flow:** Which actors and components can send or
  receive data? Does the change preserve controller/DTO, service-rule, and
  repository/EF access boundaries? Are new external or Azure dependencies
  justified?
- **Trust and abuse:** Where does untrusted input enter, and what validates
  it? Could a caller access another user's data, elevate privileges, replay
  an operation, or leak secrets or personal data through responses/logs?
- **Permissions and operations:** Are GitHub and Azure permissions scoped
  to the required action? Are human review, environment approvals, and
  staging/production boundaries preserved? Is a rollback compatible with
  any schema or data migration?
- **Failure and evidence:** What happens on invalid input, partial failure,
  timeout, or unavailable dependencies? Which focused tests, CI signals,
  health checks, and run artifacts demonstrate the intended behavior?

The PR author proposes the design and documents open risks; a human reviewer
accepts or requests changes before merge. An ADR or checklist cannot substitute
for existing protection and approval rules.
