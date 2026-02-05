# Active Context

**Last Updated:** 2026-02-05

## Current Work Phase

### Phase 9: Post-Documentation - Test Fixes & Production Readiness

Sprint 08 (Documentation & Service Cleanup) complete! GitHub Pages live at <https://djjay0131.github.io/agentic-kg/> with automated documentation updates. Focus returning to test fixes and production readiness.

## Current State

**Branch:** `master` (all PRs merged)

**Test Status:**
- Unit Tests: 754 passed, 33 failed, 50 skipped
- E2E Tests: Infrastructure ready (import errors to fix)
- Smoke Test: 7/7 checks passing

**Infrastructure:**
- **Staging API**: https://agentic-kg-api-staging-tqpsba7pza-uc.a.run.app
- **Neo4j Bolt**: bolt://34.173.74.125:7687
- **Neo4j Browser**: http://34.173.74.125:7474
- **Terraform IaC**: `infra/` directory

## Priority Tasks

### 0. Fix UI Deployment (URGENT - In Progress)

**Issue:** UI shows "Offline" status - can't connect to API

- **Root Cause:** `NEXT_PUBLIC_API_URL` not embedded at build time
- **Fix:** Rebuild UI with proper build args (in progress)
- **Status:** Build running - commit 38aaa5c has the fix
- **Files:** packages/ui/src/lib/api.ts:5, docker/Dockerfile.ui, cloudbuild.yaml

### 1. Fix Failing Tests (High Priority)

33 failing tests in extraction module:
- `test_kg_integration.py` (11 failures) - Mock/fixture issues
- `test_pipeline.py` (11 failures) - Mock/fixture issues
- `test_llm_client.py` (2 failures) - Error handling
- `test_pdf_extractor.py` (2 failures) - Mocking issues
- `test_problem_extractor.py` (1 failure) - Validation
- `test_section_segmenter.py` (2 failures) - Segmentation
- `test_importer.py` (1 failure) - Batch import
- 3 E2E collection errors - HybridSearchService import

### 2. Address Backlog Items (Medium Priority)

From `construction/backlog/sprint-01-deferred.md`:
- Multi-hop graph traversal (FR-2.3.4)
- Neo4j Aura production documentation
- Update techContext.md with Neo4j details

### 3. Ingest Real Data (Medium Priority)

- Use data acquisition to fetch papers
- Run extraction pipeline to populate KG
- Validate with E2E tests

### 4. Production Deployment (Low Priority - When Ready)

- `terraform apply -var-file=envs/prod.tfvars`

## Completed Sprints

All 8 sprints merged to master:
- Sprint 00: GCP Infrastructure
- Sprint 01: Knowledge Graph Foundation
- Sprint 02: Data Acquisition
- Sprint 03: Extraction Pipeline
- Sprint 04: API + Web UI
- Sprint 05: Agent Implementation
- Sprint 06: Full-Stack Integration
- Sprint 07: End-to-End Testing
- Sprint 08: Documentation & Service Cleanup (GitHub Pages live!)

## Key Commands

```bash
# Run unit tests (excludes E2E)
source .venv/bin/activate
pytest packages/core/tests/ --ignore=packages/core/tests/e2e -q

# Run smoke test
make smoke-test

# Get Neo4j password
cd infra && terraform output -raw neo4j_password
```

## Reference Materials

- **Documentation Hub**: <https://djjay0131.github.io/agentic-kg/>
- **Staging API**: <https://agentic-kg-api-staging-tqpsba7pza-uc.a.run.app>
- **Neo4j Browser**: <http://34.173.74.125:7474>
- **Terraform**: `infra/` directory
- **Backlog**: `construction/backlog/sprint-01-deferred.md`

## Notes for Next Session

- Read ALL memory-bank files on context reset
- **Sprint 08 complete** - GitHub Pages live with auto-documentation
- Documentation automation via GitHub Actions (.github/workflows/update-docs.yml)
- Focus on test fixes: 33 tests failing in extraction module
- E2E tests have import errors to fix
- Backlog items documented in progress.md
