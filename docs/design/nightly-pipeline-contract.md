---
title: Nightly pipeline contract
parent: Design
nav_order: 14
---

# Nightly Pipeline Report Contract

The shared interface between the nightly pipeline job (T29) and the Runs page
(T30). Both sides implement this contract exactly. Whichever PR lands first
adds this file; the other references it. This is a project/domain technical
specification (data plane, `docs/`), not governance.

The contract, verbatim:

```
PipelineRun (one per nightly execution), stored two ways:
  1. JSON report at gs://vt-gcp-00042-agentic-kg-runs-staging/nightly/<run_id>.json
  2. Neo4j node (:PipelineRun {run_id}) in the staging DB, label NOT under Canon__*.
Fields:
  run_id: str            # "nightly-YYYYMMDDTHHMMSSZ"
  started_at, finished_at: ISO-8601 UTC str (finished_at null while running)
  status: "running" | "succeeded" | "partial" | "failed"
  trigger: "schedule" | "manual" | "research_cycle"  # research_cycle = autonomous loop tick
  namespace: str         # "staging"
  queries: [ {query_id, query, topic, limit, papers_seen, papers_new, status, error|null} ]
  totals: {papers_seen, papers_new, committed_operations, deferred_candidates, honest_nulls}
  deferral_reasons: {reason: count}
  budget: {max_papers, max_llm_usd, est_llm_usd, stopped_by_budget: bool}
  review_queue_size: int|null      # pending ReviewItems for this run; null when the adviser stage did not run
  adviser_verdicts: {verdict: count}  # admit | reject | merge-into:<id>, P2
  proposed_queries: []             # empty until P3
  failures: [ {step, message, log_url|null} ]
  git_sha: str, image: str
API (T30): GET /api/runs?limit=&cursor= -> {"runs":[PipelineRun summary...], "next_cursor": str|null}
           GET /api/runs/{run_id} -> full PipelineRun ; 404 if unknown. Read from Neo4j; no GCS access needed by the API.
             The detail also carries adviser_human_agreement: {entity_type:
             {decided, agreed, agreement_rate|null}}, computed at read time
             across every decided ReviewItem in the namespace (to date, not
             scoped to this run).
```

The research-progression loop reuses this exact shape for each cycle
(`trigger="research_cycle"`, `run_id="cycle-YYYYMMDDTHHMMSSZ"`), writing its
JSON under `research-cycles/` instead of `nightly/`. It has no ingest queries,
so `queries` carries one row per started workflow (`query_id` = workflow run
id, `topic` = current gate/state) and `proposed_queries` carries the selected
problem ids. The Runs page and the log-based alert events therefore cover a
cycle without a second report format.

## Live acquisition (P3)

`papers_seen` and `papers_new` are populated from the acquisition step, not
derived from the KGIS/KGCS summary's paper list. When the nightly bridge runs
the KGIS backend with `MIGRATION_SOURCE=live`, `INGEST_QUERY` selects the papers
and `INGEST_LIMIT` caps the run; the per-query `honest_nulls` no longer carries
`query_scoping` (the query *does* select papers). For `MIGRATION_SOURCE=corpus`
(the default) the query is still recorded but does not select papers, and the
`query_scoping` null remains. The migration summary exposes the fields the bridge
reads: `source`, `query`, `limit`, `papers_seen`, `papers_new`,
`papers_skipped_duplicate`, `papers_skipped_no_text`, and `acquisition_errors`.

Implementation: `packages/core/src/agentic_kg/migration/live.py` (acquisition)
and `migration/run.py` (`execute_migration`).

## Review queue (P2)

The adviser stage runs after the nightly ingest in the same Cloud Workflows
execution (`python -m agentic_kg.pipeline.curate`, Cloud Run Job
`agentic-kg-adviser-<env>`, gated by `nightly_curate_enabled`). It reads the
run's KGIS ledger, re-runs the deterministic admission policy over the
candidates, and grades the deferred ones with the bounded KGCS adviser. The
adviser receives the evidence **text** (KGCS `EvidenceLookup` over the ledger's
evidence registry), not just ids.

A `ReviewItem` is one candidate, its adviser grade, and the human decision. It
is a `:Canon__ReviewItem` node under the canonical `ns` namespace (ADR-0004 /
ADR-0005) — canonical-path workflow state, never an Identity/Assertion.
Idempotency is on `(run_id, candidate_id)`; a retry never re-grades an item
that already exists.

Fields: `id`, `run_id`, `candidate_id`, `candidate_kind`, `entity_type`,
`candidate_summary`, `candidate_payload` (the full KGIS candidate JSON),
`evidence_refs` (id, relationship, quoted text), `adviser_verdict`
(`admit` | `reject` | `merge-into:<id>`), `confidence`, `rationale`,
`adviser_type` / `adviser_version` / `adviser_model_id` / `adviser_model_version`
/ `prompt_version`, `adviser_abstained`, `status` (`pending` | `approved` |
`rejected`), `committable` / `commit_blocker`, `decided_by`, `decided_at`,
`decision_note`, `committed_epoch`, `committed_identity_ids`.

Only candidates a human decision can actually execute are queued: entity
candidates (Problems, ResearchConcepts, Methods, Models, Topics, ...) and
relation/attribute candidates whose endpoints are already identity ids.
Artifacts have no operation type, and an assertion with an alias endpoint needs
entity resolution, so both stay in the run's `deferral_reasons` rather than
asking a human to approve something the system cannot write.

API:

```
GET  /api/review?status=&type=&run_id=&limit=&offset=
     -> {"items":[ReviewItem summary...], "total": int, "limit": int, "offset": int}
GET  /api/review/{id} -> full ReviewItem ; 404 if unknown
POST /api/review/{id} {"decision": "approve"|"reject", "note": str|null}
     Requires X-User-ID (the same mechanism as the legacy review routes).
     approve -> KGCS commit path; reject -> decision only. Both append an
     immutable :Canon__ReviewDecision node. 409 if not pending / not committable.
```

Approval calls `agentic_kg.migration.curation.commit.commit_candidate`, which
reconstructs the stored candidate and hands it to the ordinary `run_curation`
path with the candidate marked human-approved. KGCS still plans and executes
the operation; the review queue is never a second writer. A human approval
cannot widen eligibility: only an `EntityCandidate` the deterministic policy
routed `LLM_ASSESS` can be promoted.

Operator procedure: `docs/operations/review-queue-runbook.md`.
