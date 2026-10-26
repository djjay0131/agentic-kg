---
title: "ADR-0010: Autonomy policy for the research loop"
nav_exclude: true
---

# ADR-0010: Autonomy policy for the research loop

Status: Proposed
Date: 2026-10-26

## Context

The research-progression cycle (`agentic_kg.pipeline.cycle`) closes the loop
from ranking to synthesis: it re-ranks the graph, starts up to
`RESEARCH_CYCLE_TOP_K` workflows, and each workflow stops at three gates before
it may write:

1. **select_problem** — which ranked problem the workflow investigates,
2. **approve_proposal** — whether the continuation proposal is executed, and
3. **review_evaluation** — whether the evaluated result may be written back to
   the canonical graph.

The system already has an adviser that grades extracted candidates, and the P2
review queue records adviser-vs-human agreement per entity type
(`GET /api/runs/{id}` exposes it as `adviser_human_agreement`). That number is
the only measured evidence this repo has about whether an automated decision
agrees with a human on graph-affecting work.

The question this ADR records is **not** "can the loop run unattended?". It is
"what must be true before any gate may run unattended?" This PR deliberately
ships the conservative answer: **all three gates are human**, and a test pins
that default (`tests/pipeline/test_cycle_policy.py`).

## Decision

**Ship a versioned autonomy policy, loaded from
`config/research-autonomy.yaml`, with every gate set to `human`.**

1. The policy file has a `version` and one rule per gate:
   `{mode: human | auto_if | auto, confidence_threshold?}`.
2. The file is versioned and the version is recorded on every
   `:Canon__ResearchCycle` node (`policy_version`), so "what was the system
   allowed to do?" is recoverable after the fact. An environment variable may
   point at a different file, but cannot change the rules inline.
3. The policy fails closed: a missing gate, an unknown gate/mode, an
   `auto_if` without a threshold, or a threshold outside `[0, 1]` raises. An
   absent confidence never satisfies `auto_if`.
4. `auto` is implemented but **not enabled**. Enabling any gate is an owner
   decision that requires a new version of this ADR and the evidence in
   §Evidence below.
5. A gate decision is recorded on the cycle (who, when, note, edit payload) and
   only an approval may resume a workflow into a write. A rejection resumes the
   workflow only so LangGraph closes it; its routing function never reaches
   synthesis.

## Options considered

### Option A — all human (chosen)

Every gate waits for a person. Cost: the loop's throughput is bounded by human
attention. Benefit: the first production version cannot write canonical content
without a named human, which is the same guarantee the P2 review queue already
gives extraction.

### Option B — `auto_if` on `select_problem` first

Selection is the least consequential gate: it chooses which open problem to
investigate, and rejecting a bad selection costs a re-rank. A plausible first
step is `auto_if` with a high threshold on the ranking score (for example 0.85),
while proposal approval and result validation stay human. This is **not**
enabled here because the ranking score is an LLM self-report, not a calibrated
probability, and no human-vs-ranking agreement has been measured.

### Option C — `auto_if`/`auto` on proposal approval or result validation

This is the highest-consequence change: approval runs an experiment and
validation permits a canonical write. It should not be considered until the
evidence below exists. This ADR does **not** propose it.

### Option D — per-gate env flags

Rejected. A cluster of environment flags is not versioned, is easy to change
without review, and leaves no artifact saying which rules a cycle ran under.
The policy file is reviewable in a PR and its version is recorded on the cycle.

## Evidence required before a gate may become auto

Any future ADR that flips a gate must present, for that gate:

1. **Agreement.** Adviser/human agreement for the decision class, computed from
   decided `:Canon__ReviewItem` nodes over a stated window, with `decided`
   counts. A rate with no denominator is not evidence. The P2 projection
   (`adviser_human_agreement`) is the source; the Research page/Runs detail
   already expose it.
2. **A pre-registered threshold and a shadow period.** The proposed
   `confidence_threshold` must be fixed before measuring, and the gate must run
   in shadow (policy says auto, the system records what it *would* have done,
   a human still decides) for a stated period. Shadow decisions must be
   compared to the human decision on the same gate.
3. **A false-approval bound.** The rate at which automation would have approved
   something the human rejected must be below a number the owner accepts in
   advance, per gate. A high overall agreement does not excuse a bad tail on
   approvals that write canonical content.
4. **A rollback path.** Lowering the gate back to `human` must be a one-line
   versioned change with no code change, and stale auto decisions must not be
   resumable after the policy version changes.

## Consequences

### Positive

- The loop can run on a schedule without silently gaining write authority.
- The policy is versioned, reviewable, auditable on every cycle, and fails
  closed.
- The same evidence surface (P2 agreement) that justifies the adviser is the
  one that must justify autonomy.

### Negative / tradeoffs

- Human attention bounds cycle throughput; a pending gate does not block the
  next scheduled cycle (only `running` does), so cycles can accumulate pending
  gates.
- The `auto` code path is exercised only by tests until an owner enables it.
- The ranking/continuation/evaluation confidences are model self-reports; the
  threshold in any future `auto_if` is therefore a policy choice, not a
  calibrated probability.

### Risks

- If an owner edits the file to `auto` without the evidence above, the system
  will do exactly what the file says. The ADR is the review gate; the code is
  not a second opinion.
- A changed policy version does not retroactively invalidate pending gates.
  A future change should refuse to resume a gate recorded under a different
  policy version when the gate's mode changed to auto; that is follow-up work,
  not shipped here.

## Impacted Areas

- [ ] Product
- [ ] Domain model
- [x] Data architecture
- [x] AI architecture
- [x] Domain-specific systems
- [ ] Integrations
- [x] UX
- [x] Security/privacy
- [x] Implementation
- [x] Documentation

## Related Documents

- `config/research-autonomy.yaml` — the shipped policy (all human)
- `packages/core/src/agentic_kg/agents/autonomy.py`
- `packages/core/src/agentic_kg/pipeline/cycle.py`
- `llm/features/research-progression-loop.md`
- `docs/operations/research-cycle-runbook.md`
- ADR-0008 (nightly ingestion; the cycle runs after curate)
- ADR-0004 / ADR-0005 (the app-namespace node convention)

## Related Issues / PRs

- Implements the closed research-progression loop (proposal §IV-E, §V-G5).

## Supersedes

None.

## Superseded By

None.
