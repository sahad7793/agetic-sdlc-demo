# Architecture decisions

Use an architecture decision record (ADR) for a proposed change to service
boundaries, data storage, public API contracts, identity/trust boundaries, or
deployment topology. Routine fixes do not need an ADR. Copy
[`template.md`](template.md) to `NNNN-short-title.md`, choose the next
available number, and link the draft in the pull request.

The author describes the trade-offs and alternatives before implementation;
a human reviewer checks the decision and its impact using the
[design review checklist](../design-review.md). Set the status to `Proposed`
until human review, then record `Accepted` or `Rejected` and the decision
date. If the decision changes later, add a new ADR that links to the old one
and mark the old record `Superseded by ADR-NNNN` rather than rewriting its
history. An ADR is documentation, not permission to bypass existing CI,
review, or deployment approvals.
