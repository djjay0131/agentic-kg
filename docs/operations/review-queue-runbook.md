---
title: Reviewing the nightly queue
parent: Operations
nav_order: 8
---

# Reviewing the nightly queue

The nightly pipeline defers candidates the deterministic admission policy will
not auto-apply. P2 grades those candidates with a bounded KGCS adviser and puts
them in a human review queue. This runbook is the operator procedure.

Contract: `docs/design/nightly-pipeline-contract.md` §Review queue (P2).
Decision record: `llm/governance/adr/0008-nightly-ingestion-pipeline.md`.

## What the queue contains

One item per deferred candidate per run:

- **Entity candidates** — `Problem`, `ResearchConcept`, `Method`, `Model`,
  `Topic`. These are the main event: approving one mints its canonical
  identity.
- **Relation / attribute candidates whose endpoints are already identity ids.**
  Approving one attaches the assertion.
- **Not queued:** artifacts (no KGCS operation type) and assertions whose
  endpoints are still aliases (they need entity resolution, which is not
  wired). They remain visible in the run's `deferral_reasons`.

Each item carries the quoted evidence text the adviser saw, the adviser's
verdict (`admit` / `reject` / `merge-into:<id>`), confidence and rationale.

## Open the queue

UI: **Review** in the sidebar, or a run's `review_queue_size` on the Runs page
(that link pre-filters by `run_id`).

API:

```bash
curl "$API_URL/api/review?status=pending&limit=25"
curl "$API_URL/api/review?run_id=nightly-20261009T063000Z&type=Problem"
curl "$API_URL/api/review/<item_id>"
```

## Decide an item

```bash
curl -X POST "$API_URL/api/review/<item_id>" \
  -H 'Content-Type: application/json' \
  -H 'X-User-ID: your.name' \
  -d '{"decision":"approve","note":"evidence is specific; problem is distinct"}'
```

- **Approve** commits the candidate through KGCS (`run_curation` with the
  candidate marked human-approved). The plan is still KGCS's; the review queue
  is not a second write path. The response includes `committed_epoch` and
  `committed_identity_ids`.
- **Reject** records the decision and writes nothing to the canonical graph.
- Both create an immutable `:Canon__ReviewDecision` node. An item that already
  has a decision cannot be decided again (409).
- A non-committable item (merge recommendation, or an item an older stage
  queued with alias endpoints) returns 409 with the blocker. Rejecting it still
  works.

Approval is **not** automatic. Nothing in the nightly job approves an item.

## Budget

The stage reuses `NIGHTLY_MAX_LLM_USD` (`0` disables the cap). The provider
client does not report token usage through KGCS's port, so the cap is enforced
against a declared per-call estimate
(`EST_USD_PER_ADVISER_CALL`, currently USD 0.001). A stopped stage:

- writes the items it graded;
- leaves the rest ungraded;
- sets the run's `budget.stopped_by_budget` and appends an `adviser` failure;
- exits 1, so the workflow surfaces the partial.

Raise `NIGHTLY_MAX_LLM_USD` or re-run the stage; it is idempotent and grades
only the missing candidates.

## Run the stage manually

```bash
# Cloud Run Job (staging)
gcloud run jobs execute agentic-kg-adviser-staging \
  --region us-central1 --wait \
  --update-env-vars NIGHTLY_RUN_ID=nightly-20261009T063000Z

# Or locally, against a run's ledger directory
python -m agentic_kg.pipeline.curate \
  --run-id nightly-20261009T063000Z \
  --ledger-dir /path/to/ledger \
  --namespace staging
```

Without `--run-id`/`NIGHTLY_RUN_ID`, the stage reads
`<NIGHTLY_RUNS_DIR>/nightly/.latest_run_id`, the marker the nightly job writes.
The stage prints one JSON summary line (`review_items_written`,
`verdict_counts`, `honest_nulls`, ...).

Exit codes: `0` wrote the queue (or had nothing to do), `1` budget stop, `2`
a failure (including no resolvable run id).

## When the queue is empty

- `nightly_ingest_mode = "legacy"` writes no KGIS ledger, so there are no
  candidates to grade. `review_queue_size` is `0` and the stage reports an
  honest null. This is expected until the nightly opt-in changes; P2 does not
  change the default.
- A repeated stage run skips candidates already queued for the run
  (`honest_nulls.idempotent_skips`).
- A run whose candidates all routed `AUTO` has nothing deferred.

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `review_queue_size` is null | the adviser stage did not run for the run | check the workflow step and `nightly_curate_enabled` |
| stage exits 2 with "no run id" | no `NIGHTLY_RUN_ID` and no marker | pass `--run-id`; check the runs volume mount |
| stage reports 0 candidates | legacy ingest, or run id mismatch | confirm `nightly_ingest_mode` and the run id |
| approve returns 409 | item not pending, or not committable | read `commit_blocker`; reject if appropriate |
| approve returns 502 | KGCS/Neo4j failure | check Cloud Run logs and Neo4j connectivity; retry is safe only if the item is still pending |

## Invariants

- The adviser never writes canonical state; it only returns an assessment.
- Only `PlanExecutor` writes canonical state.
- Review items live in the canonical namespace under `Canon__ReviewItem`, never
  in the legacy label space.
- Decisions are append-only; the item's `status` is its current value, not a
  replacement for the decision history.
