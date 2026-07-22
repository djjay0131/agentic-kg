---
title: CI smoke test
parent: Design
nav_order: 12
---

# CI smoke test — end-to-end ingestion

{: .label .label-green }
VERIFIED

**Backlog:** ci-smoke-test-ingestion · **Depends on:**
entity-pipeline-orchestration + E-1..E-8 (all VERIFIED) · **Spec:**
[`ci-smoke-test-ingestion.md`](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/ci-smoke-test-ingestion.md)

## Why

Entity-pipeline-orchestration made full V2 extraction default-on in
`ingest_papers`: every commit on `master` now runs V2 entity extraction,
cross-entity normalization, and the V1/V2 integration loop in production. Unit
tests pin contracts *at each seam* with mocked LLM clients — so a refactor can
silently break the wiring *across* seams and every unit test still passes. A
broken failure-isolation path, a skipped `taxonomy_hash` write, an upstream
OpenAI prompt shift returning empty concept lists — none of those show up until
someone hits them on a Tuesday morning. There was no automated check that the
wired-up pipeline actually writes edges to the graph.

## What shipped

One GitHub Actions workflow,
[`smoke-ingest.yml`](https://github.com/djjay0131/agentic-kg/blob/master/.github/workflows/smoke-ingest.yml),
that stands up an **ephemeral Neo4j service container** inside the runner,
ingests 3 real papers end-to-end with extraction default-on, and asserts the
resulting graph has the expected shape. It fires on three triggers: `pull_request`
against `master` (path-filtered to `packages/core/**`, `pyproject.toml`, the
workflow file, and the assertion script), a daily `cron` at `17 6 * * *` UTC, and
`workflow_dispatch` with `query`/`limit` inputs for developer self-service. The
`ingest_result.json` is uploaded as an artifact on every run, pass or fail.

## Design decisions

**Assert on real graph edges, not mocks.** The whole point is to catch what unit
tests can't. The assertion runs a single Cypher query against the just-populated
Neo4j and checks six batch-level conditions — this is a genuine integration
check against a live database, not a re-assertion of a mocked contract. If the
orchestrator writes zero `BELONGS_TO` edges, the smoke test goes red where every
unit test stayed green.

**Ephemeral Neo4j, zero GCP secrets.** A `neo4j:5.26-community` service container
(APOC enabled, healthcheck-gated) gives a clean slate every run. Nothing touches
staging Neo4j, the Cloud Run Job, or any GCP project — `NEO4J_URI` always points
at `localhost`. The only secret referenced is `OPENAI_API_KEY`. Production deploy
verification is a deliberately separate (deferred) workflow.

**Real data, accepted nondeterminism.** The test ingests live arXiv/Semantic
Scholar results rather than pinned fixtures. Red is treated as a real signal —
either a wiring regression or genuine upstream drift — so a single automatic
retry (30s sleep) absorbs transient OpenAI flake, and the daily cron surfaces API
drift before a developer hits it. The check is informational, not a required
merge gate.

**Batch-level, `≥ 1` per entity type.** Assertions are `Model OR Method` (a
3-paper batch may not produce both) rather than exhaustive per-paper checks —
strict enough to prove wiring, loose enough to survive real-corpus variance.

## How it works

- **Workflow:**
  [`smoke-ingest.yml`](https://github.com/djjay0131/agentic-kg/blob/master/.github/workflows/smoke-ingest.yml)
  — checkout → install `packages/core` → `initialize_schema(force=True)` →
  ingest (bash single-retry loop, max 2 attempts) → assert → upload artifact.
  A `concurrency` group cancels stale runs on rapid pushes, saving LLM calls.
- **Assertion:**
  [`scripts/smoke_assert.py`](https://github.com/djjay0131/agentic-kg/blob/master/scripts/smoke_assert.py)
  first checks `IngestionResult.status == "completed"` (early-exit before touching
  Neo4j), then runs one chained-`OPTIONAL MATCH` Cypher query for six counts and
  reports a `PASS:`/`FAIL:` table plus raw counts to stdout. It lives under
  `scripts/` (not `tests/`) because it runs against a live DB, not under pytest.
- **The six edges/counts asserted:** `Paper ≥ 1`; `BELONGS_TO` topic edges `≥ 1`;
  `ResearchConcept ≥ 1`; `Model + Method ≥ 1`; `CITES ≥ 1`; Papers with non-null
  `taxonomy_hash ≥ 1` (proves V2 integration ran to its audit-metadata write).
- **Local mirror:** `make smoke-local` reproduces the workflow step-for-step
  against a local Docker Neo4j, so a green local run predicts a green CI run.

This smoke test is the runtime guard on the pipeline described in
[Entity pipeline orchestration]({{ site.baseurl }}/design/entity-pipeline-orchestration).
For the node types and edges it asserts on, see
[Entity Relationships]({{ site.baseurl }}/reference/entity-relationships).

## Verification

- **AC coverage:** all 15 acceptance criteria (triggers, path filter, ephemeral
  Neo4j + schema init, default-on ingest, single-retry, six assertions, status
  pre-check, artifact upload, concurrency, `make smoke-local`).
- **Tests:** `smoke_assert.py` is split into `_load_result` / `_run_graph_checks`
  / `_evaluate_checks` so its status pre-check, count logic, and error paths are
  unit-testable with an injected mock session (no live Neo4j needed).
- **Status:** VERIFIED — workflow live on `master` with PR + daily-cron +
  dispatch triggers.

## Related

- Guards: [Entity pipeline orchestration]({{ site.baseurl }}/design/entity-pipeline-orchestration)
- Asserts on: [E-1 · Topic entities]({{ site.baseurl }}/design/e1-topic-entities), [E-5 · Citation graph]({{ site.baseurl }}/design/e5-citation-graph)
- Reference: [Entity Relationships]({{ site.baseurl }}/reference/entity-relationships)
- Deferred: production deploy-verification smoke (Cloud Run Job vs staging Neo4j)
