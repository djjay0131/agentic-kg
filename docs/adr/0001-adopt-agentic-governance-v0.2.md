# ADR-0001: Adopt agentic-governance v0.2 and declare interim design authority

Status: Accepted
Date: 2026-07-13

## Context

agentic-kg's siblings in the research-ai family, `agentic-kgis` and
`agentic-kgcs`, have already adopted the canonical
[agentic-governance](https://github.com/djjay0131/agentic-governance) v0.2
operating system. agentic-kg — the parent, deployed system — has not.
Governance in this repo has so far been implicit: Constellize's
design-first workflow (`CLAUDE.md`), a memory bank
(`llm/memory_bank/`), and a feature-spec catalog (`llm/features/`), but no
ADR trail, no governance-level classification, no explicit design-authority
document, and no PR/contribution template pointing to a governance model.
Unlike its siblings, agentic-kg has no single consolidated design spec —
its architecture is recorded across `llm/memory_bank/systemPatterns.md`
and informal references scattered through `construction/sprints/*.md`.

## Decision

Adopt agentic-governance v0.2 in this repo via `docs/governance-delta.md`,
mirroring the sibling repos' delta structure and localizing it to
agentic-kg's facts (public-repo branch protection availability, `master`
default branch, docs-only establish scope). Declare
`llm/memory_bank/systemPatterns.md` the **interim** design-authority
document — the rank-2 artifact in the design authority hierarchy — pending
a consolidated standalone FDS, since this repo has no equivalent to the
siblings' `docs/superpowers/specs/` document today.

## Rationale

Establishing over existing content (rather than reconciling a conflicting
prior governance model) is straightforward here: there is no competing
governance system to reconcile, only an absence to fill. Naming
`systemPatterns.md` interim design authority is the smallest true
statement available — it is already the most current, most complete
architecture record, actively kept in sync by the Constellize memory
workflow, and it is honest that it was never written to serve as one.
Writing a full FDS now would delay adoption of the ADR trail and PR
governance this repo needs immediately (it is mid-flight on a fragile
`Deploy Master` recovery, per `activeContext.md`) for a document that is a
separate, larger effort.

## Alternatives Considered

### Alternative 1: Write a consolidated FDS before adopting governance

Would give agentic-kg parity with its siblings' design-authority documents
immediately. Rejected for now: it blocks adoption of ADR discipline and PR
governance on a substantial writing effort, and the repo's current
priority is the deploy recovery (`deploy-pipeline-fix`). Recorded as a
follow-up instead (see Consequences).

### Alternative 2: Treat `llm/features/BACKLOG.md` as design authority

Rejected: `BACKLOG.md` is a feature-status catalog, not an architecture
document — it lacks the system-level design content (entity model, write
paths, confidence routing) that `systemPatterns.md` already carries.

## Consequences

### Positive

- agentic-kg gains the same governance model as its siblings: classified
  work, an ADR trail, a PR template with governance-level declaration, and
  a `CONTRIBUTING.md` pointer.
- Naming an interim design authority now (rather than waiting for an FDS)
  unblocks governed review immediately.

### Negative / Tradeoffs

- `systemPatterns.md` was not written as a design-authority document; it
  is implementation-pattern-shaped rather than decision-shaped. Some
  judgment calls in review will be less crisp than they would be against a
  purpose-built FDS until the follow-up lands.
- The ADR trail starts at 0001 with a gap behind it: prior architectural
  decisions (three-layer architecture, problems-as-first-class-entities,
  confidence-based matching, GCP Cloud Run deployment, Cloud Build
  CI/CD) exist only as informal references in `systemPatterns.md` and
  `construction/sprints/*.md`, not as numbered ADRs.

### Risks

- If the retroactive ADR cataloging follow-up (below) is deferred
  indefinitely, the gap behind ADR-0001 becomes a permanent blind spot
  rather than a temporary one.

## Impacted Areas

- [ ] Product
- [ ] Domain model
- [ ] Data architecture
- [ ] AI architecture
- [ ] Domain-specific systems (see governance delta)
- [ ] Integrations
- [ ] UX
- [ ] Security/privacy
- [ ] Implementation
- [x] Documentation

## Related Documents

- `docs/governance-delta.md`
- `llm/memory_bank/systemPatterns.md` (interim design authority)
- agentic-governance `docs/governance-delta-template.md`,
  `docs/l0-fast-track.md`, `docs/governance-levels.md`

## Related Issues / PRs

- Tracking issue and establish PR for this adoption (see
  `docs/governance-delta.md` Last-updated entry and the PR that introduces
  this ADR).

## Follow-Ups (recorded here so they are not orphaned)

- **Consolidated FDS.** Write a standalone design-authority document
  (mirroring `agentic-kgis`'s `docs/superpowers/specs/` shape) and
  supersede this ADR's "interim" declaration once it exists.
- **Retroactive ADR catalog.** Back-fill the pre-governance architectural
  decisions currently scattered across `construction/sprints/*.md` and
  `systemPatterns.md` — three-layer architecture, problems-as-first-class-
  entities, confidence-based matching, GCP Cloud Run deployment, Cloud
  Build CI/CD — as numbered ADRs (ADR-0002 onward), so the ADR trail has no
  gap behind ADR-0001.

## Supersedes

None.

## Superseded By

None.
