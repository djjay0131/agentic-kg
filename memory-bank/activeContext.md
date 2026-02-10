# Active Context

**Last Updated:** 2026-02-10

## Current Work Phase

### Phase 10: Canonical Problem Architecture

Sprint 09 Phase 1 (Data Model & Core Matching) COMPLETED! Implemented dual-entity architecture (ProblemMention/ProblemConcept) with vector similarity matching, auto-linking for HIGH confidence matches (>95%), and comprehensive test coverage (~90%).

## Current State

**Branch:** `sprint-09-canonical-architecture-phase-1` (ready for PR)

**Test Status:**
- Unit Tests (master): 754 passed, 33 failed, 50 skipped
- Sprint 09 Tests: 76 tests (64 unit + 12 integration), ~90% coverage
- E2E Tests: Infrastructure ready (import errors to fix)
- Smoke Test: 7/7 checks passing

**Infrastructure:**
- **Staging API**: https://agentic-kg-api-staging-tqpsba7pza-uc.a.run.app
- **Neo4j Bolt**: bolt://34.173.74.125:7687
- **Neo4j Browser**: http://34.173.74.125:7474
- **Terraform IaC**: `infra/` directory

## Priority Tasks

### 0. Sprint 09 PR & Testing (CURRENT - In Progress)

**Status:** Creating PR for canonical problem architecture

- Create PR from `sprint-09-canonical-architecture-phase-1` to `master`
- Run unit tests to validate (76 tests expected to pass)
- Run integration tests with live Neo4j (requires env vars)
- Review and merge

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

8 sprints merged to master + Sprint 09 ready for PR:
- Sprint 00: GCP Infrastructure
- Sprint 01: Knowledge Graph Foundation
- Sprint 02: Data Acquisition
- Sprint 03: Extraction Pipeline
- Sprint 04: API + Web UI
- Sprint 05: Agent Implementation
- Sprint 06: Full-Stack Integration
- Sprint 07: End-to-End Testing
- Sprint 08: Documentation & Service Cleanup (GitHub Pages live!)
- **Sprint 09 Phase 1**: Canonical Problem Architecture (ready for PR)
  - ProblemMention/ProblemConcept dual-entity model
  - Vector similarity matching with Neo4j VECTOR indexes
  - Auto-linking for HIGH confidence (>95%)
  - 76 tests (64 unit + 12 integration), ~90% coverage

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
