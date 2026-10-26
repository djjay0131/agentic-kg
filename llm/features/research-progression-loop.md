---
title: Research-progression loop (scheduled cycle with human gates)
status: IMPLEMENTED
---

# Feature: Research-progression loop

Implements the closed loop from the proposal §IV-E / §V-G5. One scheduled tick:

```
close finished workflows
  -> re-rank the graph
  -> select top-k problems
  -> start one workflow per problem (runs to its next gate)
  -> [GATE select_problem]        -> continuation proposals
  -> [GATE approve_proposal]      -> evaluation
  -> [GATE review_evaluation]     -> synthesis write-back
  -> next cycle re-ranks the updated graph
```

## Acceptance criteria

- **AC-1** A persistent `:Canon__ResearchCycle` node records cycle id,
  started/finished, budget, selected problems, per-workflow ids/state, gate
  decisions (who/when/note/edit) and outcomes. App-namespace per ADR-0004/0005.
- **AC-2** `python -m agentic_kg.pipeline.cycle` closes finished workflows,
  re-ranks, and starts at most `RESEARCH_CYCLE_TOP_K` (default 3) workflows,
  each running to its next gate. A new cycle never starts while another is
  `running`; a cycle waiting at a gate does not block the next tick.
- **AC-3** Workflow checkpoints are durable across process restarts: a workflow
  stopped at a gate can be resumed by a different process (Neo4j-backed store;
  in-memory store for offline tests).
- **AC-4** Autonomy is policy, not a prompt: `config/research-autonomy.yaml` is
  versioned, every gate ships `human`, the policy fails closed, and a test pins
  the shipped default `human`. ADR-0010 records the evidence required before a
  gate may change.
- **AC-5** A gate decision is audited on the cycle and resumes the workflow.
  Only an approval may reach synthesis; a rejection closes the workflow without
  a canonical write.
- **AC-6** Budgets are honest stops: per-cycle LLM USD cap, max experiments and
  max wall time set `stopped_by_budget` and a note; the kill switch
  (`RESEARCH_CYCLE_ENABLED`, Terraform `research_cycle_enabled`) defaults false.
- **AC-7** Each cycle writes the shared `PipelineRun` shape with
  `trigger="research_cycle"` so the Runs page and the existing structured
  log events (`pipeline_run_completed` / `pipeline_run_failed`) cover it.
- **AC-8** Infra: a Cloud Run Job, a nightly Workflow step after curate, and an
  optional weekly scheduler, all Terraform-gated (default off).
- **AC-9** Offline tests cover scheduling, gate resumption across a simulated
  restart, budget stop, the default-human policy pin, and no-write-on-reject.

## Out of scope / honest nulls

- Live model quality of ranking/continuation/evaluation is not measured here.
- The Cloud Run Job evaluation backend is ADR-0009 (separate, unmerged branch);
  this loop uses the existing workflow's evaluation path and does not depend on
  that backend being present.
- No gate is auto. No auto path is enabled in staging.
- The cycle's USD figure is an explicit per-workflow estimate, not a billing
  measurement (the agent client does not report token usage through this path).
