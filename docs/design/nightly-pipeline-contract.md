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
  trigger: "schedule" | "manual"
  namespace: str         # "staging"
  queries: [ {query_id, query, topic, limit, papers_seen, papers_new, status, error|null} ]
  totals: {papers_seen, papers_new, committed_operations, deferred_candidates, honest_nulls}
  deferral_reasons: {reason: count}
  budget: {max_papers, max_llm_usd, est_llm_usd, stopped_by_budget: bool}
  review_queue_size: int|null      # null until P2 review queue exists
  proposed_queries: []             # empty until P3
  failures: [ {step, message, log_url|null} ]
  git_sha: str, image: str
API (T30): GET /api/runs?limit=&cursor= -> {"runs":[PipelineRun summary...], "next_cursor": str|null}
           GET /api/runs/{run_id} -> full PipelineRun ; 404 if unknown. Read from Neo4j; no GCS access needed by the API.
```

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
