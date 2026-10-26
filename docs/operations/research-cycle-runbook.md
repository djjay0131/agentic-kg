---
title: Research-cycle runbook
---

# Research-cycle runbook

The research-progression loop runs one **cycle** at a time: it closes finished
workflows, re-ranks the graph, starts up to `RESEARCH_CYCLE_TOP_K` workflows and
stops each at its next human gate. Infrastructure is in `infra/main.tf`
(`agentic-kg-research-cycle-<env>`), gated by `research_cycle_enabled`, which
**defaults false**. ADR-0010 governs autonomy.

## Turning it on

1. Review `config/research-autonomy.yaml`. It ships with every gate `human`; a
   test pins that default. Changing a gate requires ADR-0010's evidence and an
   owner decision.
2. In `infra/envs/<env>.tfvars` set `research_cycle_enabled = true` and apply
   (owner bootstrap may be needed; see ADR-0008 for the CI principal limits).
3. Optionally set `research_cycle_weekly_enabled = true` for an independent
   weekly tick (`research_cycle_weekly_schedule`, default Sunday 03:00 ET).
4. Run it manually before trusting the schedule (below).

## Manual run

```bash
gcloud run jobs execute agentic-kg-research-cycle-staging \
  --region=us-central1 --wait
```

The Job runs `python -m agentic_kg.pipeline.cycle`. It writes:

- a `:Canon__ResearchCycle` node (`ns`, `cycle_id`), and
- a `:PipelineRun` node plus a JSON report under
  `gs://<runs-bucket>/research-cycles/<cycle_id>.json`.

It logs one structured line: `pipeline_run_completed` or
`pipeline_run_failed` with `trigger: research_cycle`. When disabled it logs
`research_cycle_disabled` and exits 0 without touching the graph.

## Deciding a gate

Use **Research Loop** in the UI, or:

```bash
curl -X POST \
  -H 'X-User-ID: alice' -H 'Content-Type: application/json' \
  "$API/api/research/gates/<cycle_id>/<workflow_run_id>" \
  -d '{"gate":"approve_proposal","decision":"approve","note":"ok"}'
```

`GET /api/research/gates` lists pending gates; `GET /api/research/cycles`
lists cycles. The decision is recorded on the cycle before the workflow is
resumed, so a failed resume leaves an audit record. Approve/edit resumes the
workflow; reject closes it without a canonical write.

## Budgets and the kill switch

| Env | Terraform | Meaning |
|---|---|---|
| `RESEARCH_CYCLE_ENABLED` | `research_cycle_enabled` | Kill switch (default false) |
| `RESEARCH_CYCLE_TOP_K` | `research_cycle_top_k` | Workflows per cycle (default 3) |
| `RESEARCH_CYCLE_MAX_LLM_USD` | `research_cycle_max_llm_usd` | Est-USD cap; ≤0 disables |
| `RESEARCH_CYCLE_MAX_EXPERIMENTS` | `research_cycle_max_experiments` | Evaluations per cycle |
| `RESEARCH_CYCLE_MAX_WALL_SECONDS` | `research_cycle_timeout` | Wall-time cap; stale `running` cycles are closed |

A cap is an honest stop: the cycle sets `budget.stopped_by_budget = true`, a
`note` naming the cap, and exits 1. The USD figure is a documented per-workflow
estimate, not billing.

## One-at-a-time

A cycle is `running` only while it is executing. Once its workflows are at
gates it is `awaiting_gate`, which does **not** block the next tick. A stale
`running` cycle older than the wall-time cap is closed as failed before the
guard is applied, so a crashed Job cannot block the loop forever.

## Durable gates

Checkpoints live in `:Canon__AgentCheckpoint` nodes (`ns`, `thread_id`), written
by `DurableCheckpointSaver`. The API and the cycle Job both use it, so a gate
can be decided by a process that did not start the workflow. Do not delete those
nodes while a workflow is gated.
