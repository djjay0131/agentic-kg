# Feature: CI Smoke Test — Ingestion

**Status:** SPECIFIED
**Date:** 2026-06-25
**Author:** Feature Architect (AI-assisted)
**Backlog ID:** (follow-up to entity-pipeline-orchestration loop closure)
**Depends On:** entity-pipeline-orchestration (VERIFIED), all of E-1..E-8 V2 + E-7 (VERIFIED)
**Decoupled From:** real-data eval calibration (E-7 AC-21 + E-8 V2 AC-17 deferred steps), production deploy verification (separate Cloud Run Job smoke; out of scope here).

## Problem

`entity-pipeline-orchestration` just landed as default-on in `ingest_papers`. Every commit on `master` now invokes the full V2 entity extraction + cross-entity normalization + V1/V2 integration loop in production. There is **no automated regression check** that the wired-up loop actually writes Topic edges, ResearchConcept nodes, Model nodes, Method nodes, CITES edges, and audit metadata to the graph after extraction.

Concrete failure modes that exist without this feature:

- A refactor in `extraction/pipeline.py` silently breaks `extract_all_entities`'s `_run` failure isolation; every paper records `extraction_failed_extractors="model,method"` but the unit tests still pass because the mocked LLM clients never get called in those tests.
- A bug in `kg_integration_v2.py::integrate_paper_entities` skips writing `Paper.taxonomy_hash` for a code branch; AC-21's skip check then silently disables itself, costing real money on every re-ingest.
- An upstream OpenAI prompt-template behavior change (rare but observed historically) causes `ConceptExtractor` to return empty lists for every paper; unit tests pass because they pin the response schema with mocks.
- `cross_entity_normalizer.py`'s `_build_paper_excerpt` truncation interacts badly with a new section type from the segmenter; the routing LLM gets garbage; every paper records `picked=None`.

Unit tests pin contracts at the seam; they don't catch broken wiring across seams. A smoke test that ingests 3 real papers end-to-end + asserts on graph shape catches all four classes.

## Goals

- **One GitHub Actions workflow** (`.github/workflows/smoke-ingest.yml`) that ingests 3 papers against a fresh testcontainers Neo4j inside the runner, with extraction default-on, and asserts the resulting graph has the expected shape.
- **Triggers** (Q3 decision): `pull_request` on `master` (path-filtered to `packages/core/**`, `pyproject.toml`, the workflow file itself, and `scripts/smoke_assert.py`) + daily `cron` (06:17 UTC, off-peak, deterministic minute) + `workflow_dispatch` with `query` and `limit` inputs for developer self-service.
- **Standard-strictness assertions** (Q4 decision): batch-level, each entity type ≥ 1 across the batch. Specifically:
  - At least 1 `Paper` node.
  - At least 1 `BELONGS_TO` edge from a Paper to a Topic.
  - At least 1 `ResearchConcept` node.
  - At least 1 `Model` node OR at least 1 `Method` node (whichever the batch produced; `Model OR Method` rather than `AND` because some 3-paper batches genuinely won't produce both).
  - At least 1 `CITES` edge (citation graph populated via E-8 V2's `populate_citations`).
  - At least 1 Paper with `taxonomy_hash` set (proves V2 integration ran end-to-end + AC-21's skip check has its input data).
- **OpenAI flake tolerance** (Q5 decision): single automatic retry on the whole `agentic-kg ingest` invocation. If retry also fails, the workflow exits red. Developers re-run via GHA UI for persistent OpenAI degradation.
- **Cost ceiling**: ~3 papers × ~6 LLM calls each = ~15-20 LLM calls per smoke run. At `gpt-4o-mini` pricing that's ~$0.01/run. PR + daily cron at the project's PR rate → ~$3-8/month. Cheap enough to leave on by default; not so cheap that it's invisible.
- **Self-contained**: zero GCP secrets required for Neo4j (testcontainers in the runner); only `OPENAI_API_KEY` needs to live in GitHub Secrets. The workflow does NOT touch staging Neo4j, the Cloud Run Job, or any GCP project — that's a separate "deploy verification" smoke (deferred).
- **Failure artifact**: on every run (pass or fail), upload `ingest_result.json` (the `--json` output from the CLI) so operators can post-hoc inspect why the run failed.

## Non-Goals

- **Production deploy verification.** The Cloud Run Job + `gcloud run jobs execute` variant is a separate workflow whose primary signal is "Dockerfile + env vars + IAM still work". Deferred per Q1 decision.
- **Prompt-effectiveness evaluation.** E-7 AC-21 + E-8 V2 AC-17 + entity-pipeline-orchestration AC-25 follow-up — the hand-labeled fixture set + precision/recall floors are a separate (and bigger) feature.
- **Performance/throughput regression detection.** Wall-clock per paper isn't asserted. AC-18 in entity-pipeline-orchestration already notes wall-clock is a manual staging check.
- **Cost telemetry.** Tracking LLM-call counts across batches is the deferred E-7 / E-8 V2 / entity-pipeline-orchestration open question.
- **Cross-paper / cross-batch idempotency.** The smoke test asserts a single batch's graph shape. AC-21's skip check is exercised by virtue of running a fresh container every time (skip check always returns False on the empty graph); a dedicated re-ingest idempotency test is a future spec.
- **Per-PR cost gating** (PR labels, fork PR opt-in). Standard GHA defaults apply: fork PRs from untrusted contributors don't get access to `OPENAI_API_KEY`, so the workflow will fail-fast with a clear "no API key" error on fork PRs. Not a problem until/unless the project takes outside contributions.
- **Real-network smoke against staging Neo4j.** Q2 chose testcontainers for hermeticity; running against staging is a different feature that needs cleanup logic (TEST_ prefix or AC-13 purge) to avoid polluting hand-curated data.
- **`master` push trigger.** PR + cron + manual is enough; `master` push would be redundant because PR runs already gate merges.

## User Stories

- **As a developer opening a PR that touches `packages/core/`**, I want a smoke-test CI check that confirms `agentic-kg ingest` still produces the expected graph shape end-to-end, so I catch wiring regressions before merging.
- **As an oncall responder seeing a PR's smoke test go red**, I want the workflow to upload `ingest_result.json` as an artifact so I can read the failure mode (which extractor errored, which entity counts came up zero) without re-running locally.
- **As a maintainer of the project**, I want a daily cron smoke test that catches upstream API drift (OpenAI prompt-template shifts, arXiv search ranking changes, Semantic Scholar reference-list schema breaks) before a developer hits it on a Tuesday morning.
- **As a developer debugging the smoke test itself**, I want `workflow_dispatch` with overridable `query` and `limit` inputs so I can iterate without committing throwaway YAML changes.
- **As a contributor opening a fork PR**, I want a clear failure message ("OPENAI_API_KEY not available; smoke test requires API key") rather than a cryptic OpenAI auth error, so I know the failure isn't my code.

## Design Approach

### Architecture (one workflow, one assertion script, one artifact)

```
GHA trigger (PR / cron / dispatch)
    │
    ▼
ubuntu-latest runner
    │
    ├─► testcontainers Neo4j service (5.26-community, healthcheck-gated)
    │
    ├─► Install: `pip install -e ./packages/core`
    │
    ├─► Initialize schema: `initialize_schema(force=True)`
    │
    ├─► Ingest 3 papers (single retry on failure):
    │     `agentic-kg ingest --query "$Q" --limit "$L" --json > result.json`
    │
    ├─► Assert: `python scripts/smoke_assert.py result.json`
    │     │
    │     ├─► Parse IngestionResult JSON; status must be "completed".
    │     │
    │     └─► Cypher query against the testcontainers Neo4j;
    │         check 6 conditions; exit 1 on any fail.
    │
    └─► Upload artifact: ingest_result.json (always, pass or fail)
```

### Files created

| File | Purpose |
|---|---|
| `.github/workflows/smoke-ingest.yml` | The workflow — PR + cron + dispatch triggers, Neo4j service, install + ingest + assert + artifact. |
| `scripts/smoke_assert.py` | Standalone Python script that reads `IngestionResult` JSON + queries Neo4j + reports per-check pass/fail; exits 1 on any failure. |

### Files modified

| File | Purpose |
|---|---|
| `Makefile` (root) | Add `smoke-local` target that mirrors the workflow's steps line-for-line: spin a local Docker Neo4j, init schema, ingest, run `smoke_assert.py`. Per QA Q2 review — gives developers a one-command local reproduction loop when a PR's smoke check goes red. |

### Why `scripts/` and not `tests/`

The assertion script runs in CI against a live Neo4j the workflow just populated. Putting it under `tests/` would imply it's run by `pytest`, which is misleading. The closest precedent is `scripts/smoke_test.py` referenced in `systemPatterns.md` ("scripts/ — Utilities"). The new `smoke_assert.py` lives alongside that.

### Triggers + path filters

```yaml
on:
  workflow_dispatch:
    inputs:
      query: { description: 'Search query', default: 'retrieval augmented generation' }
      limit: { description: 'Paper count', default: '3' }
  pull_request:
    branches: [master]
    paths:
      - 'packages/core/**'
      - 'pyproject.toml'
      - '.github/workflows/smoke-ingest.yml'
      - 'scripts/smoke_assert.py'
  schedule:
    - cron: '17 6 * * *'  # 06:17 UTC daily
```

The cron minute is `17` (not `0`) so the run lands on an off-peak GHA queue and produces a stable artifact timestamp without collision pressure.

### Single-retry mechanism (Q5)

Bash-driven, not GHA-action-driven, because the existing `integration-tests.yml` is also raw bash. Two attempts: if the first `agentic-kg ingest` exits non-zero, sleep 30s, retry once; if the second also exits non-zero, the step fails. The 30s sleep handles transient OpenAI rate-limit windows without doubling the run time on persistent outages.

The retry is **on the whole ingest invocation**, not on individual papers. Per-paper failure isolation already lives inside `ingest_papers` (entity-pipeline-orchestration's outer try/except); the retry catches cases where the orchestrator itself hits an unexpected exception (the `except Exception as e` in `ingestion.py` that sets `result.status = "failed"`).

### Assertion contract (Q4 standard strictness)

The script first checks `IngestionResult.status == "completed"` (proves the orchestrator didn't fatal-error and the per-paper loop ran to the sanity-checks phase). Then it runs ONE Cypher query (six counts in a single round trip — chained `OPTIONAL MATCH ... WITH ... count(*)` clauses) and checks six conditions:

1. `papers >= 1` — at least one Paper landed (Phase 1 metadata import worked).
2. `topic_edges >= 1` — at least one `BELONGS_TO` edge (V2 entity integrator's topic writer worked).
3. `concepts >= 1` — at least one `ResearchConcept` node (V2 concept writer worked).
4. `models + methods >= 1` — sum across kinds (V2 model OR method writer worked, accepting that 3 papers may not hit both kinds).
5. `cites >= 1` — at least one `CITES` edge (E-8 V2's `populate_citations` ran).
6. `tagged >= 1` — at least one Paper with `taxonomy_hash` set (V2 integrator's audit metadata write fired).

Failures print to stdout in a `PASS:/FAIL:` table so the GHA log is the diagnostic.

### `OPENAI_API_KEY` secret + fork PR handling

The workflow uses `env.OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}`. GHA's default behavior on `pull_request` from a fork: secrets are not exposed. The smoke test will fail immediately on the `agentic-kg ingest` step (`OpenAIError: api_key required`). The error message will be cryptic; we accept that until/unless the project takes outside contributors. A fork-PR-detection guard could be added later as a one-liner (`if: github.event.pull_request.head.repo.full_name == github.repository`).

### Artifact retention

`actions/upload-artifact@v4` with `if: always()` so failures upload too. Default retention is 90 days, which is plenty for post-mortem.

### Cost model

| Trigger | Frequency | Per-run cost (gpt-4o-mini, 3 papers × 6 calls × ~1K tokens) | Monthly |
|---|---|---|---|
| PR | ~10-30/month at current PR rate | $0.005-0.01 | $0.05-0.30 |
| cron | 30/month | $0.005-0.01 | $0.15-0.30 |
| dispatch | ad-hoc | $0.005-0.01 | <$1 |
| **Total** | | | **$3-8/month** (matches the Q3 estimate) |

LLM cost is a single-digit % of the Cloud Run Job's existing monthly bill. Not a budget concern.

## Sample Implementation

```yaml
# === .github/workflows/smoke-ingest.yml ===

name: Smoke Test — Ingest

on:
  workflow_dispatch:
    inputs:
      query:
        description: 'Search query'
        default: 'retrieval augmented generation'
      limit:
        description: 'Paper count'
        default: '3'
  pull_request:
    branches: [master]
    paths:
      - 'packages/core/**'
      - 'pyproject.toml'
      - '.github/workflows/smoke-ingest.yml'
      - 'scripts/smoke_assert.py'
  schedule:
    - cron: '17 6 * * *'  # 06:17 UTC

# TL Q2: cancel stale runs when a new commit arrives on the same ref.
# Saves ~10-30 LLM calls on multi-commit PRs.
concurrency:
  group: smoke-ingest-${{ github.ref }}
  cancel-in-progress: true

jobs:
  smoke:
    name: Ingest + Assert
    runs-on: ubuntu-latest
    timeout-minutes: 15

    services:
      neo4j:
        image: neo4j:5.26-community
        ports: ['7687:7687', '7474:7474']
        env:
          NEO4J_AUTH: neo4j/testpassword
          NEO4J_PLUGINS: '["apoc"]'
        options: >-
          --health-cmd "wget -qO /dev/null http://localhost:7474 || exit 1"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 18

    env:
      OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
      NEO4J_URI: bolt://localhost:7687
      NEO4J_USERNAME: neo4j
      NEO4J_PASSWORD: testpassword
      NEO4J_DATABASE: neo4j

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: 'pip'

      - name: Install package
        run: |
          pip install --upgrade pip
          pip install -e ./packages/core

      - name: Initialize Neo4j schema
        run: python -c "from agentic_kg.knowledge_graph.schema import initialize_schema; initialize_schema(force=True)"

      - name: Ingest (with single retry)
        run: |
          set +e
          ATTEMPT=1
          MAX=2
          QUERY="${{ github.event.inputs.query || 'retrieval augmented generation' }}"
          LIMIT="${{ github.event.inputs.limit || '3' }}"
          while [ $ATTEMPT -le $MAX ]; do
            echo "::group::Ingest attempt $ATTEMPT/$MAX"
            agentic-kg ingest --query "$QUERY" --limit "$LIMIT" --json > "ingest_result_$ATTEMPT.json"
            STATUS=$?
            echo "::endgroup::"
            if [ $STATUS -eq 0 ]; then
              cp "ingest_result_$ATTEMPT.json" ingest_result.json
              exit 0
            fi
            if [ $ATTEMPT -lt $MAX ]; then
              echo "::warning::Ingest attempt $ATTEMPT failed; sleeping 30s before retry"
              sleep 30
            fi
            ATTEMPT=$((ATTEMPT + 1))
          done
          echo "::error::Ingest failed on both attempts"
          [ -f "ingest_result_2.json" ] && cp "ingest_result_2.json" ingest_result.json
          exit 1

      - name: Assert graph shape
        run: python scripts/smoke_assert.py ingest_result.json

      - name: Upload artifact
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: smoke-result-${{ github.run_id }}
          path: |
            ingest_result*.json
          retention-days: 14
```

```makefile
# === Makefile (additions) ===

# Per QA Q2 review — local smoke-test loop that mirrors the CI workflow.
# Usage: make smoke-local
#        QUERY="custom query" LIMIT=5 make smoke-local
.PHONY: smoke-local
smoke-local:
	@command -v docker >/dev/null || { echo "docker required"; exit 1; }
	@[ -n "$$OPENAI_API_KEY" ] || { echo "OPENAI_API_KEY env required"; exit 1; }
	@docker rm -f smoke-neo4j >/dev/null 2>&1 || true
	@docker run -d --name smoke-neo4j \
		-e NEO4J_AUTH=neo4j/testpassword \
		-e NEO4J_PLUGINS='["apoc"]' \
		-p 7687:7687 -p 7474:7474 \
		neo4j:5.26-community
	@echo "Waiting for Neo4j..."
	@until curl -sf http://localhost:7474 >/dev/null 2>&1; do sleep 2; done
	@NEO4J_URI=bolt://localhost:7687 NEO4J_USERNAME=neo4j NEO4J_PASSWORD=testpassword \
		python -c "from agentic_kg.knowledge_graph.schema import initialize_schema; initialize_schema(force=True)"
	@NEO4J_URI=bolt://localhost:7687 NEO4J_USERNAME=neo4j NEO4J_PASSWORD=testpassword \
		agentic-kg ingest \
		--query "$${QUERY:-retrieval augmented generation}" \
		--limit "$${LIMIT:-3}" \
		--json > ingest_result.json
	@NEO4J_URI=bolt://localhost:7687 NEO4J_USERNAME=neo4j NEO4J_PASSWORD=testpassword \
		python scripts/smoke_assert.py ingest_result.json
	@docker rm -f smoke-neo4j >/dev/null 2>&1 || true
```

```python
# === scripts/smoke_assert.py ===

"""Smoke test assertions — batch-level entity coverage.

Run after `agentic-kg ingest --json > result.json`. Asserts that the
expected graph shape landed for at least one paper in the batch.
Exits 0 on pass, 1 on fail with a per-check PASS/FAIL report on stdout.

See: llm/features/ci-smoke-test-ingestion.md (AC-3 / AC-4).
"""

from __future__ import annotations

import json
import sys


def main(result_path: str) -> int:
    # Sanity check: the ingest run itself completed.
    try:
        with open(result_path) as f:
            result = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"FAIL: cannot read result file {result_path!r}: {e}")
        return 1

    if result.get("status") != "completed":
        print(f"FAIL: ingest_papers status={result.get('status')!r}")
        if errs := result.get("extraction_errors"):
            print(f"  extraction_errors: {errs}")
        return 1

    # Cypher graph-shape checks. Imported here so the import cost is
    # not paid on the early-exit path above.
    from agentic_kg.knowledge_graph.repository import get_repository

    repo = get_repository()
    with repo.session() as session:
        row = session.run("""
            OPTIONAL MATCH (p:Paper)
              WITH count(p) AS papers
            OPTIONAL MATCH (:Paper)-[r1:BELONGS_TO]->(:Topic)
              WITH papers, count(r1) AS topic_edges
            OPTIONAL MATCH (c:ResearchConcept)
              WITH papers, topic_edges, count(c) AS concepts
            OPTIONAL MATCH (m:Model)
              WITH papers, topic_edges, concepts, count(m) AS models
            OPTIONAL MATCH (mt:Method)
              WITH papers, topic_edges, concepts, models, count(mt) AS methods
            OPTIONAL MATCH (:Paper)-[r2:CITES]->()
              WITH papers, topic_edges, concepts, models, methods,
                   count(r2) AS cites
            OPTIONAL MATCH (p2:Paper) WHERE p2.taxonomy_hash IS NOT NULL
              WITH papers, topic_edges, concepts, models, methods, cites,
                   count(p2) AS tagged
            RETURN papers, topic_edges, concepts, models, methods, cites, tagged
        """).single()

    checks: dict[str, bool] = {
        "papers >= 1":                  row["papers"] >= 1,
        "BELONGS_TO topic edges >= 1":  row["topic_edges"] >= 1,
        "ResearchConcept nodes >= 1":   row["concepts"] >= 1,
        "Model OR Method >= 1":         (row["models"] + row["methods"]) >= 1,
        "CITES edges >= 1":             row["cites"] >= 1,
        "taxonomy_hash on >= 1 Paper":  row["tagged"] >= 1,
    }

    print("\n=== Smoke-test graph-shape assertions ===")
    print(f"  papers={row['papers']}, topic_edges={row['topic_edges']}, "
          f"concepts={row['concepts']}, models={row['models']}, "
          f"methods={row['methods']}, cites={row['cites']}, "
          f"taxonomy_hash_papers={row['tagged']}")
    print()

    failed: list[str] = []
    for name, ok in checks.items():
        status = "PASS" if ok else "FAIL"
        print(f"  {status}: {name}")
        if not ok:
            failed.append(name)

    if failed:
        print(f"\nSmoke test FAILED: {len(failed)} check(s) failed.")
        return 1
    print("\nSmoke test PASSED.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: smoke_assert.py <ingest_result.json>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
```

## Edge Cases & Error Handling

### `OPENAI_API_KEY` not available (fork PR)
- **Scenario**: PR opened from a fork; GitHub does not expose repository secrets.
- **Behavior**: `agentic-kg ingest` step fails on attempt 1 with `OpenAIError`. Retry sleep + attempt 2 also fails with the same error. Workflow exits red. Cryptic failure message; documented as accepted residual risk until the project takes outside contributors.
- **Test**: Manually verifiable by opening a fork PR; not in the assertion script's scope.

### Neo4j service container fails to start
- **Scenario**: Docker hub rate-limited the image pull, OR the healthcheck timed out before Neo4j 5.x finished booting.
- **Behavior**: GHA marks the job as failed before any of the steps run. The runner's service-container log is in the GHA UI by default; no extra wiring needed.
- **Test**: Not in scope; handled by GHA's service-container layer.

### `agentic-kg ingest` succeeds on attempt 1 — never enters retry path
- **Scenario**: Happy path; the OpenAI API + arXiv + S2 are all healthy.
- **Behavior**: Loop body `exit 0` triggers after attempt 1 produces a 0-exit-code JSON; attempt 2 never runs. Artifact upload picks up `ingest_result_1.json` (copied to `ingest_result.json` by the loop).
- **Test**: Manual run with `workflow_dispatch`; assertion script picks up the file and runs the Cypher.

### `agentic-kg ingest` exits non-zero on both attempts
- **Scenario**: OpenAI is down for an extended window OR the orchestrator itself fatal-errored both times.
- **Behavior**: Both `ingest_result_1.json` and `ingest_result_2.json` exist (we write the JSON before checking the exit code). The loop falls through, copies `ingest_result_2.json` to `ingest_result.json` if it exists, prints `::error::Ingest failed on both attempts`, and exits 1. Artifact step uploads both per-attempt files for post-mortem.
- **Test**: Local rehearsal: invoke the script with a deliberately-failing `OPENAI_API_KEY=invalid`; confirm both attempts ran + both JSONs uploaded.

### `agentic-kg ingest` succeeds but `result.status != "completed"`
- **Scenario**: The orchestrator hit a fatal error AFTER recording it in the result; CLI still exits 0 (it doesn't translate `status="failed"` to a non-zero exit).
- **Behavior**: Retry loop sees exit 0, doesn't retry. Assertion script sees `status != "completed"` and FAILs at the first check. The JSON's `extraction_errors` dict is printed for diagnosis.
- **Test**: Mock `ingest_papers` to return `IngestionResult(status="failed", error="...")`; run `smoke_assert.py` against it; expect exit 1 with "FAIL: status=..." stdout.

### All 3 papers had failed extraction; orchestrator status is "completed"
- **Scenario**: `status="completed"` because the orchestrator's outer try/except didn't fatal, but every paper recorded `extraction_errors[doi]`. No Paper nodes were written by V2 integration because every paper was an error-case skip.
- **Behavior**: The Cypher checks find zero papers / zero edges / zero concepts. `papers >= 1` fails first. The assertion script prints the failure table and exits 1.
- **Test**: Mock the ingest result to have `status="completed"` AND non-empty `extraction_errors`; run the assert script against an empty graph; expect exit 1.

### Per-batch LLM cost spike
- **Scenario**: An OpenAI pricing change or token-count drift causes a single batch to cost (say) $5 instead of $0.01.
- **Behavior**: Smoke test is silent on cost; the test passes as long as the graph shape lands. Cost telemetry is a separate (deferred) feature; documented as an accepted blind-spot.
- **Test**: Not testable in this spec; the open-question follow-up tracks it.

### Concurrent PR runs queue up
- **Scenario**: 4 PRs open simultaneously; each triggers a smoke run.
- **Behavior**: GHA queues the runs. Each gets its own runner with its own testcontainers Neo4j. No contention.
- **Test**: Not in scope; verified by GHA's standard runner-allocation behavior.

### Artifact upload fails (rare GHA flake)
- **Scenario**: GHA's upload-artifact step transient 5xx.
- **Behavior**: The step retries internally (action's own retry); if it still fails, the artifact is lost but the smoke test's pass/fail status is unaffected (artifact step uses `if: always()` so it runs after the assert step but doesn't gate the workflow's exit code).
- **Test**: Not in scope.

## Acceptance Criteria

### AC-1: Workflow file present and triggers documented
- **Given** `.github/workflows/smoke-ingest.yml` exists in the repo
- **When** GHA evaluates the workflow file
- **Then** the workflow is active with three triggers: `workflow_dispatch` (with `query` and `limit` inputs), `pull_request` on `master` (path-filtered), and `schedule` at `17 6 * * *` UTC
- **And** the workflow's `timeout-minutes` is at most 15

### AC-2: Path filter scope
- **Given** a PR opens against `master`
- **When** the changed files include only files outside `packages/core/**`, `pyproject.toml`, `.github/workflows/smoke-ingest.yml`, and `scripts/smoke_assert.py`
- **Then** the smoke workflow does NOT run for that PR
- **And** when the PR touches any of those paths, the workflow runs

### AC-3: Neo4j service container + schema init
- **Given** the workflow is running
- **When** the Neo4j service container starts
- **Then** it uses image `neo4j:5.26-community` with the APOC plugin enabled
- **And** the `Initialize Neo4j schema` step calls `agentic_kg.knowledge_graph.schema.initialize_schema(force=True)` and exits 0
- **And** the workflow exports `NEO4J_URI=bolt://localhost:7687`, `NEO4J_USERNAME=neo4j`, `NEO4J_PASSWORD=testpassword`, `NEO4J_DATABASE=neo4j` to the steps that follow

### AC-4: Ingest invocation with default-on entity extraction
- **Given** the `Ingest (with single retry)` step
- **When** the step runs
- **Then** it invokes `agentic-kg ingest --query "$QUERY" --limit "$LIMIT" --json > "ingest_result_*.json"` without `--no-extract-entities` or `--no-normalize-cross-entity`
- **And** `$QUERY` defaults to "retrieval augmented generation" (workflow-dispatch override possible)
- **And** `$LIMIT` defaults to 3 (workflow-dispatch override possible)

### AC-5: Single-retry mechanism
- **Given** the first `agentic-kg ingest` invocation exits non-zero
- **When** the retry loop runs
- **Then** the loop sleeps 30 seconds, then retries one more time
- **And** if the second invocation exits 0, the workflow's step exits 0 (using `ingest_result_2.json` copied to `ingest_result.json`)
- **And** if the second invocation also exits non-zero, the step exits 1 (with `::error::Ingest failed on both attempts` in the log)
- **And** the step DOES NOT retry more than once (max 2 attempts total)

### AC-6: Standard-strictness assertions
- **Given** `scripts/smoke_assert.py ingest_result.json` runs against a populated Neo4j
- **When** the script's Cypher query returns 6 counts
- **Then** the script asserts: papers ≥ 1; BELONGS_TO topic edges ≥ 1; ResearchConcept nodes ≥ 1; (Model nodes + Method nodes) ≥ 1; CITES edges ≥ 1; Papers with non-null `taxonomy_hash` ≥ 1
- **And** the script exits 0 when all 6 checks pass
- **And** the script exits 1 when any check fails
- **And** the script prints a `PASS:`/`FAIL:` table to stdout with per-check breakdown
- **And** the script prints the raw counts (e.g., `papers=3, topic_edges=5, ...`) for diagnosis

### AC-7: IngestionResult status check precedes Cypher
- **Given** `ingest_result.json` carries `status != "completed"`
- **When** the assertion script runs
- **Then** the script exits 1 BEFORE running the Cypher query
- **And** the script prints `FAIL: ingest_papers status=...`
- **And** if `extraction_errors` is present in the JSON, the script prints them too

### AC-8: Missing or unparseable result JSON
- **Given** `ingest_result.json` is missing OR contains invalid JSON
- **When** the assertion script runs
- **Then** the script exits 1
- **And** the script prints a `FAIL: cannot read result file ...` message identifying the problem

### AC-9: Artifact upload
- **Given** the workflow finishes (pass or fail)
- **When** the `Upload artifact` step runs
- **Then** `ingest_result.json` AND any `ingest_result_N.json` per-attempt files are uploaded to artifact `smoke-result-${{ github.run_id }}`
- **And** retention is set to 14 days (lower than GHA's 90-day default; sized to typical PR review windows)
- **And** the step uses `if: always()` so failed runs still upload artifacts

### AC-10: Cost ceiling documented and inherited
- **Given** the workflow's default config (3 papers, gpt-4o-mini-class LLM)
- **When** per-batch cost is estimated
- **Then** the spec's Goals section documents the ~$0.01/run cost ceiling and ~$3-8/month projection
- **And** no additional gating beyond standard GHA defaults is required for the project's current PR rate

### AC-11: `workflow_dispatch` overrides
- **Given** a developer invokes the workflow via the GHA UI with custom `query` and `limit` inputs
- **When** the workflow runs
- **Then** the `Ingest` step uses the custom values (not the defaults)
- **And** when inputs are omitted, the defaults ("retrieval augmented generation", 3) apply

### AC-12: No GCP secrets required
- **Given** the workflow's env block
- **When** the workflow runs against a fresh GHA runner
- **Then** the only GitHub Secret referenced is `OPENAI_API_KEY`
- **And** no GCP Workload Identity Federation, no `gcloud` invocations, no Cloud Run Job triggers happen
- **And** the workflow does NOT touch staging Neo4j (NEO4J_URI always points at localhost via the service container)

### AC-13: Workflow runs in CI without modification to existing test workflows
- **Given** existing workflows (`integration-tests.yml`, `test.yml`, etc.)
- **When** this feature is merged
- **Then** none of the existing workflow files are modified
- **And** the smoke workflow has its own name "Smoke Test — Ingest" so the PR check label is distinguishable

### AC-14: Concurrency group cancels stale in-progress runs (TL Q2)
- **Given** a PR with 3 commits pushed in quick succession
- **When** the smoke workflow is triggered for each commit
- **Then** the workflow's `concurrency` config (group `smoke-ingest-${{ github.ref }}`, `cancel-in-progress: true`) cancels in-progress runs for the same ref
- **And** only the latest commit's smoke run completes
- **And** cancelled runs do NOT count against the cost ceiling (LLM calls already made are sunk; the retry+assert steps are skipped)

### AC-15: `make smoke-local` mirrors the CI workflow (QA Q2)
- **Given** the developer runs `make smoke-local` from the repo root
- **When** Docker and `OPENAI_API_KEY` are available locally
- **Then** the target starts a local Neo4j container, initializes schema, runs `agentic-kg ingest --query "$QUERY" --limit "$LIMIT" --json > ingest_result.json`, and runs `python scripts/smoke_assert.py ingest_result.json`
- **And** the target's behavior matches the CI workflow's behavior step-for-step (so a green local run predicts a green CI run, modulo external API drift)
- **And** the target is documented in the spec's Sample Implementation section and in repo's Makefile help target if one exists
- **And** the target accepts `QUERY` and `LIMIT` environment variables to override defaults (matching `workflow_dispatch` inputs)
- **And** the target is informational; a non-zero exit does not interfere with other Makefile targets

## Technical Notes

- **Affected files:**
  - Create: `.github/workflows/smoke-ingest.yml`, `scripts/smoke_assert.py`
  - Modify: `Makefile` (root) — add `smoke-local` target (per QA Q2 review)
  - Existing workflows (`integration-tests.yml`, etc.) are NOT modified (per AC-13)
- **Reuse:** existing `agentic-kg ingest --json` CLI surface (entity-pipeline-orchestration); `SchemaManager.initialize_schema` (existing); `get_repository()` + neo4j Python driver (existing).
- **No new Python dependencies.** `scripts/smoke_assert.py` imports `agentic_kg.knowledge_graph.repository.get_repository` which is already in the dependency tree.
- **No new GHA actions beyond `actions/checkout@v4`, `actions/setup-python@v5`, `actions/upload-artifact@v4`.** All three are pinned in existing workflows.
- **GitHub Secrets required:** `OPENAI_API_KEY` (assumed to already exist for the staging deploy workflow; if not, must be added before merge).
- **Cost: ~$3-8/month** at current PR rate + daily cron. LLM-only; runner minutes are within GHA's free-tier allocation for the project's plan.
- **Failure mode hierarchy** (for operator triage when red):
  1. Neo4j service didn't start → GHA service-container log.
  2. Schema init failed → `Initialize Neo4j schema` step log.
  3. Ingest failed both attempts → `ingest_result_*.json` artifact + GHA step log.
  4. Ingest succeeded but graph shape wrong → `Assert graph shape` step log shows which check failed + raw counts.

## Dependencies

- **entity-pipeline-orchestration (VERIFIED)** — provides the `agentic-kg ingest --json` CLI invocation, the in-process orchestration, the default-on entity extraction, the AC-21 skip check, and the audit metadata (`taxonomy_hash`, `extraction_incomplete`, `normalization_audit`) that the smoke test asserts on.
- **All of E-1..E-8 V2 + E-7 (VERIFIED)** — provide the underlying extractors, writers, and graph-shape contracts.
- **Existing `OPENAI_API_KEY` GitHub Secret** — assumed to exist; verify before merge.
- **No new infrastructure** (Cloud Run Jobs, GCP IAM, Neo4j VMs).

## Open Questions

- **Cost telemetry inside the smoke test.** Would be nice to assert `ingest_result.json` carries an `llm_calls_made` counter under a threshold — defer until the cost-telemetry feature lands.
- **Should we add a "deploy verification" sibling workflow** that triggers the Cloud Run Job + asserts against staging Neo4j? Out of scope here; tracked as a follow-up.
- **Should we generalize the assertion script for other smoke targets** (community detection, R-1 query-facing vector search) when those land? YAGNI today; revisit when there's a second consumer.
- **Fork PR handling.** Current behavior: cryptic OpenAI auth error. Not a problem until/unless the project takes outside contributors.

## Review Record

Interview decisions (5 questions answered):

- **Q1 — Primary regression class.** Decision: **option (a)** — end-to-end pipeline contract (in-process). Catches code regressions in the orchestrator → extractors → integrators chain at PR time. Production deploy verification deferred to a separate spec.
- **Q2 — Neo4j target.** Decision: **option (a)** — testcontainers Neo4j inside the GHA runner. Clean slate every run; no staging pollution; no GCP secrets needed.
- **Q3 — Trigger cadence.** Decision: **option (a)** — PR (path-filtered to packages/core/**, pyproject.toml, the workflow file, and the assertion script) + daily cron (06:17 UTC) + workflow_dispatch for self-service.
- **Q4 — Assertion strictness.** Decision: **option (a)** — standard, batch-level ≥ 1 per entity type. Specifically: papers, topic edges, concepts, models OR methods, cites edges, and at least one Paper with taxonomy_hash set.
- **Q5 — Flake handling.** Decision: **option (a)** — single automatic retry with 30s sleep. Persistent OpenAI degradation surfaces as a workflow failure that developers can re-run from the GHA UI.

Dual-persona review (3 Tech Lead + 3 QA):

- **TL Q1 — Real-data smoke = nondeterministic test.** Decision: **option (a)** — accept the drift; red is a real signal. Real-data ingestion IS the point; pinning to fixed queries or DOIs would defer the question of what "reliable" means and add engineering cost. The retry mechanism + daily cron will surface upstream drift; developers can investigate via the artifact JSON.
- **TL Q2 — Concurrency on rapid pushes.** Decision: **option (a)** — add a GHA concurrency group with `cancel-in-progress: true`. AC-14 codifies the contract. Saves ~10-30 LLM calls per multi-commit PR; standard CI pattern.
- **TL Q3 — Merge gate.** Decision: **option (a)** — informational; not a required status check. Fork PRs without `OPENAI_API_KEY` access don't get blocked; arXiv drift doesn't block real PRs. Developers can investigate red signals or merge anyway. The spec's framing as a smoke test (not a quality gate) supports this.
- **QA Q1 — Failure-mode artifact richness.** Decision: **option (a)** — `ingest_result.json` only. `IngestionResult.extraction_errors` dict + the assertion script's raw-count output + the GHA step logs cover common debug paths. A graph-dump artifact (option b) is heavier than the spec's smoke-test scope warrants; per-paper breakdown (option c) could be added later if real failures show the current diagnostic is too coarse.
- **QA Q2 — Local reproduction.** Decision: **option (a)** — add a `make smoke-local` Makefile target that mirrors the CI workflow line-for-line. AC-15 codifies the contract. Matches the existing `make smoke-test` reference in CLAUDE.md.
- **QA Q3 — Cron-failure alerting.** Decision: **option (a)** — accept GHA defaults; no extra alerting. Daily cron failures email the last committer + show as red in the Actions tab. Slack webhook (option c) or auto-issue (option b) adds infra; for an informational smoke (TL Q3), not worth the cost.
