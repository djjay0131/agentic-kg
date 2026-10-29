---
title: Research agents on the canonical graph — runbook
nav_exclude: true
---

# Research agents on the canonical graph — runbook

Companion to ADR-0010,
`llm/governance/adr/0010-research-agents-canonical-graph.md`.
The research agents (ranking, continuation, evaluation, synthesis) read a
`GraphPort`. `AGENT_GRAPH_SOURCE` selects the adapter:

| value | adapter | reads |
|---|---|---|
| `legacy` (default) | `LegacyGraphPort` | legacy `:Problem` projection via repository/search/relations |
| `canonical` | `CanonicalGraphPort` | KGCS `Canon__Problem` identities at the latest epoch |

Terraform owns `agent_graph_source` and injects `AGENT_GRAPH_SOURCE` into the
API service. The deploy workflows only roll the image, so a deploy cannot flip
it by accident.

## Before flipping staging to `canonical`

1. The canonical namespace is populated and `kgis_kgcs_enabled = true`
   (see [kgis-kgcs-staging-runbook.html](kgis-kgcs-staging-runbook.html)).
2. The API/job image has the `migration` extra installed. A canonical run
   without it fails at `WorkflowRunner` construction; the API logs
   `WorkflowRunner initialization failed` and agent routes return 503.
3. `GET /api/canonical/summary` returns a non-zero `entities_by_type.Problem`
   (requires `CANONICAL_API_ENABLED=1`).
4. The review queue is reachable: `GET /api/review` returns 200.

## Flip

```hcl
# infra/envs/staging.tfvars
agent_graph_source = "canonical"
```

Apply through the normal Terraform workflow. Then roll the API image (or wait
for the next `deploy-master.yml` run) and confirm the service revision carries
`AGENT_GRAPH_SOURCE=canonical`:

```bash
gcloud run services describe agentic-kg-api-staging \
  --region=us-central1 --project=vt-gcp-00042 \
  --format='value(spec.template.spec.containers[0].env)'
```

Never set this with `gcloud run deploy`; ADR-0006 reserves the service spec
for Terraform.

## What changes in a canonical run

- **Reads** come from `Canon__Problem` identities, their assertions and their
  evidence ids. Ranking adds the documented graph-native score
  (`llm/governance/adr/0010-research-agents-canonical-graph.md` §Decision 4).
- **Writes**: none from the agents. SynthesisAgent queues
  `:Canon__ReviewItem` rows with `source=agent`, the workflow run id, agent
  name, model and prompt version. Every queued item is `pending`; nothing is
  canonical content until a human approves it.
- **Approval** through `POST /api/review/{id}` re-enters `commit_candidate`
  -> `run_curation` -> `PlanExecutor`. A rejected item writes nothing
  canonical. Status changes are queued `committable=false` (no KGCS candidate
  operation in this slice) and cannot be approved.
- **Provenance**: every agent output carries an `AgentProvenance` envelope and
  is held in the API process's `AgentOutputStore` (in-memory in this slice, the
  same lifetime as the runner's `MemorySaver` workflows). `GET
  /api/agents/outputs/{id}/provenance` returns the KGPS envelope (assertions +
  evidence ids + upstream output ids) and KGPS `explain` payloads when the
  canonical reader is reachable. Evidence content is not read here (KGIS
  ledger is a separate access path); unresolved links appear as KGPS gaps. A
  restart clears the store; the workflow state is cleared with it.

## Operating the queue

```bash
# What did the agents propose?
curl -s "$API/api/review?status=pending&type=entity" | jq
# The item says source=agent, agent_name=synthesis, run_id=<workflow run>
```

Approve only after reading the evidence text in the detail response. An
approval commits through KGCS; it is not a direct graph edit. If KGCS refuses
(e.g. a stale snapshot), the API returns 502 and the item stays pending.

## Rollback

Set `agent_graph_source = "legacy"` and apply. Pending agent review items
remain in the queue; they are harmless in a legacy run because only the
review API can act on them. Do not delete them: the queue is the audit trail
of what the agents proposed.

## Provenance endpoint availability

- Legacy output: the envelope abstains, `explanations` is empty.
- Canonical output with no reachable reader/KGPS: `canonical_available=false`
  and `canonical_error` names the reason. The envelope itself is still
  returned.
- Canonical output with a reachable reader: `explanations` has one KGPS
  `explain` payload per cited assertion.
