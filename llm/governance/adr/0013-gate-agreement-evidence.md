---
title: "ADR-0013: Gate agreement evidence and evidence-based autonomy criteria"
nav_exclude: true
---

# ADR-0013: Gate agreement evidence and evidence-based autonomy criteria

Status: Proposed
Date: 2026-11-27

## Context

ADR-0011 shipped the research-progression loop with every gate `human` and said
the evidence required before any gate could change. That evidence did not exist:
the P2 review queue records adviser/human agreement for extracted candidates,
but nothing recorded whether a human agreed with the nightly workflow at
`select_problem`, `approve_proposal` or `review_evaluation`. A policy that says
"auto when the evidence is good" with no measurement of that evidence is not
auditable.

Two gaps had to be closed together:

1. **The recommendation must exist before the decision.** If the system derives
   its recommendation after a human acts, the "agreement" number is
   contaminated. The workflow already produces everything needed before the
   interrupt: a ranked list, a continuation proposal with a confidence, and an
   evaluation result with a verdict and a provenance envelope.
2. **A policy edit alone must not grant autonomy.** ADR-0011 said the code does
   what the file says, which makes an accidental `auto` edit a write-authority
   change. The evidence bar must be enforced at runtime, failing closed.

## Decision

**Record a gate recommendation before the human decides, persist the human
decision beside it, and refuse any non-human gate until the recorded evidence
meets criteria a human wrote into the versioned policy.**

### 1. Recommendation and decision record

The workflow's own outputs produce an `AgentRecommendation` while the gate is
pending:

| Gate | Recommendation |
|---|---|
| `select_problem` | top-ranked problem id, score, and score margin over the second candidate |
| `approve_proposal` | approve/reject, rationale, confidence (proposal confidence below 0.5 recommends reject) |
| `review_evaluation` | write/do-not-write (approve/reject), verdict rationale, confidence, and whether the provenance envelope abstained |

The recommendation is stored on the `:Canon__ResearchCycle`'s workflow entry
(`pending_recommendation`) before any decision can be recorded. When a human
decides, the `GateDecision` records the workflow id, cycle id, gate, policy
version, the agent recommendation, the human decision, `decided_by`, the
decision timestamp, `agreed`, and the human's free-text reason when they
disagreed.

`agreed` compares actions: `edit` is an approving action. The raw decision and
the edit payload are still kept, so an edit that changes the recommended problem
is visible in the audit trail. An automatic decision has no human decision and
therefore `agreed = null`; it is **not** counted as evidence of human agreement.

Everything here is app-namespace workflow state (ADR-0004/0005). No
recommendation, agreement record or statistic is canonical `Identity`/`Assertion`
content.

### 2. The statistic

For each gate over a stated window:

- `decisions` — recorded gates with **both** an agent recommendation and a human
  decision. Automatic decisions are excluded: there is no human to agree with.
- `agreed` — those where the actions matched.
- `agreement_rate` — `agreed / decisions`, or an honest null when `decisions` is
  zero. A rate with no denominator is not evidence.
- `lower_bound` — the Wilson score interval's 95% lower bound. Small samples
  cannot claim certainty; 10/10 has a lower bound near 0.72, and 9/10 near 0.60.

`GET /api/research/agreement?gate=&since=` and the Research Loop page expose
these per gate plus the last N disagreements. `agentic-kg autonomy status` is
the read-only CLI view and additionally says whether the current evidence meets
the gate's promotion criteria.

### 3. Policy schema

`config/research-autonomy.yaml` gains, per gate:

```yaml
gate:
  mode: human | auto_if | auto_when_confident | auto
  confidence_threshold: 0.0..1.0   # required by the two confidence modes
  promotion:
    min_decisions: 0..N
    min_agreement_lower_bound: 0.0..1.0
```

`auto_when_confident` is the ADR's name for "the agent decides when its
confidence is at least the threshold, otherwise a human decides"; it resolves
identically to `auto_if`. The threshold is a policy choice over model
self-reports, not a calibrated probability (ADR-0011).

The shipped file keeps every gate `human`, and the existing test pins it. The
shipped promotion bars are 20 decisions / 0.90 lower bound for the first two
gates and 30 / 0.95 for the write gate.

### 4. Promotion rule

The system only **recommends** promotion. A human promotes a gate by editing the
versioned policy file in a PR, which is the review gate.

At runtime, a non-`human` gate is refused and falls back to a human unless:

1. the gate's `promotion` criteria are present, and
2. `decisions >= min_decisions`, and
3. the Wilson lower bound is at least `min_agreement_lower_bound`, and
4. for `review_evaluation`, the evaluation's provenance envelope is
   non-abstaining (a result not grounded in canonical assertions can never be
   auto-written).

Absent criteria fail closed. The only way to say "no evidence required" is an
explicit `min_decisions: 0` plus `min_agreement_lower_bound: 0.0` in a reviewed
file. This keeps test/demo policies possible without making missing criteria look
like sufficient evidence.

A stale or unavailable evidence read also fails closed: if the cycle cannot
compute the report, every promotion flag is false for that tick.

### 5. Shadow mode is not shipped

ADR-0011 listed a shadow period. This ADR does not implement one: a gate either
waits for a human (`human`), acts only when its mode *and* evidence allow
(`auto_if`/`auto_when_confident`/`auto`), or is refused. A shadow mode that
records "what auto would have done" while a human decides is a reasonable next
slice, but it is not required to measure agreement, because every human decision
already has the pre-decision recommendation beside it.

## Consequences

### Positive

- Autonomy is earned from measured human agreement, not asserted by an edit.
- The evidence is reproducible from the cycle records; no second store or
  projection can drift.
- The write gate has two independent guards: the evidence bar and a
  non-abstaining provenance envelope.
- The same `agreed` field serves the UI panel, the API and the CLI.

### Negative / tradeoffs

- Agreement counts only gates with a recommendation and a human decision; an
  older cycle with no recommendation contributes an honest null, not a guess.
- Model confidences are self-reports, so the promotion bar measures decision
  agreement, not calibrated certainty.
- A `select_problem` edit that keeps the workflow moving counts as agreement at
  the action level even if the human changed the recommended problem; the edit
  payload preserves the difference for review.

### Risks

- A policy file edited to `auto` with explicit zero criteria grants autonomy by
  construction. That is a deliberate human act in a reviewed file, not a system
  recommendation; the shipped file does not do it.
- Agreement on a small sample can look high while the lower bound stays low.
  That is why promotion uses the lower bound, not the point estimate.

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

- `config/research-autonomy.yaml` — shipped policy (all human, with promotion bars)
- `packages/core/src/agentic_kg/agents/autonomy.py`
- `packages/core/src/agentic_kg/agents/gate_agreement.py`
- `packages/core/src/agentic_kg/pipeline/cycle.py`
- ADR-0011 (the autonomy policy this ADR supplies evidence for)
- ADR-0010 (agent provenance on the canonical graph)
- ADR-0004 / ADR-0005 (app-namespace node convention)
- `docs/operations/research-cycle-runbook.md`
- `llm/features/research-progression-loop.md`

## Related Issues / PRs

- Implements evidence-based autonomy for proposal §IV-E / §V-G5.

## Supersedes

None.

## Superseded By

None.
