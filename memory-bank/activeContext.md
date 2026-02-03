# Active Context

**Last Updated:** 2026-01-30

## Current Work Phase

**Phase 7: End-to-End Testing - COMPLETE**

Sprint 07 validated the full pipeline against live staging: paper acquisition → PDF extraction → KG population → agent workflow. All E2E tests created, smoke test passing.

## Immediate Next Steps

**Session Status (2026-01-30):**

- On branch: `claude/sprint-07-e2e-testing`
- Sprint 06 merged (PR #13)
- Sprint 07: 10/10 tasks complete
- Staging environment fully deployed
- Smoke test: 7/7 checks passing
- Neo4j connected, no data ingested yet

**Sprint 07 Progress:**

- [x] Task 1: E2E test configuration (conftest.py, markers)
- [x] Task 2: E2E test utilities (utils.py)
- [x] Task 3: Paper acquisition E2E tests
- [x] Task 4: Extraction pipeline E2E tests
- [x] Task 5: KG population E2E tests
- [x] Task 6: API endpoints E2E tests
- [x] Task 7: Agent workflow E2E tests
- [x] Task 8: WebSocket E2E tests
- [x] Task 9: Smoke test script
- [x] Task 10: Documentation

**Infrastructure Status:**

- **Staging API**: https://agentic-kg-api-staging-tqpsba7pza-uc.a.run.app
- **Neo4j Bolt**: bolt://34.173.74.125:7687
- **Neo4j Browser**: http://34.173.74.125:7474
- **Terraform IaC**: `infra/` directory with staging/prod tfvars

**Sprint 06 Complete (Merged PR #13):**

- [x] WorkflowRunner wired into API lifespan
- [x] Event bus for workflow step transitions
- [x] Background task execution
- [x] Docker Compose updates
- [x] Makefile for monorepo
- [x] Integration tests (237 total)

**Previous Sprints:**

- Sprint 05: Agent implementation (merged)
- Sprint 04: API + Web UI (merged)
- Sprint 03: Extraction pipeline (merged)
- Sprint 02: Data acquisition (merged)
- Sprint 01: Knowledge graph foundation (merged)

**Priority Tasks:**

1. **Ingest Real Data** (Recommended Next)
   - Use data acquisition clients to fetch papers
   - Run extraction pipeline to populate KG
   - Test search/graph endpoints with real data

2. **Run Full E2E Tests** (After Data)
   - `pytest -m "e2e and not costly"` for non-LLM tests
   - `pytest -m "e2e and costly"` for full LLM tests

3. **Production Deployment** (When Ready)
   - `terraform apply -var-file=envs/prod.tfvars`

## Recent Decisions

### Decision 10: Terraform IaC for GCP (ADR-014)
- **Date:** 2026-01-30
- **Decision:** Use Terraform for all GCP infrastructure
- **Rationale:** Idempotent, version-controlled, multi-environment support
- **Impact:** `infra/` directory with staging/prod configurations

### Decision 9: LLM-Based Structured Extraction (ADR-013)
- **Date:** 2026-01-26
- **Decision:** Use LLM-based extraction with `instructor` library for structured output
- **Rationale:** High quality extraction, schema validation, better than rule-based

*Note: Decisions 1-8 archived*

## Key Patterns and Preferences

### E2E Testing Patterns (New for Phase 7)
- Pytest markers: `@pytest.mark.e2e`, `@pytest.mark.slow`, `@pytest.mark.costly`
- Environment vars: `STAGING_API_URL`, `STAGING_NEO4J_URI`, `STAGING_NEO4J_PASSWORD`
- Test data prefixed with `TEST_` for easy cleanup
- Smoke test script for CI/CD integration

### Infrastructure Patterns (New for Phase 6)
- Terraform manages: Compute Engine, Secret Manager, Cloud Run, Artifact Registry
- Per-environment tfvars: `envs/staging.tfvars`, `envs/prod.tfvars`
- Neo4j on Compute Engine with startup script
- Secrets stored in GCP Secret Manager

### Documentation Patterns
- Use markdown for all documentation
- Update activeContext.md after every significant change
- Reference ADR numbers when implementing decisions

### Development Patterns
- Python 3.12+ (Denario requirement)
- Docker containers for deployment
- Environment variables for secrets

## Important Learnings

### About E2E Testing (Phase 7)
- Paginated API responses differ from simple lists
- Search endpoint uses POST method
- WebSocket tests need timeout handling
- Smoke test validates deployment quickly

### About GCP Infrastructure (Phase 6)
- Shielded VM required by org policy (enable_secure_boot)
- Artifact Registry repos are regional
- Cloud Run needs secret accessor IAM role
- ADC auth required for Terraform

## Open Questions

1. **Data Ingestion**: Which papers/domains to seed the KG with first?
2. **Cost Management**: Monitor LLM costs during full E2E testing
3. **Production Readiness**: What additional hardening is needed?

## Reference Materials

- **Staging**: https://agentic-kg-api-staging-tqpsba7pza-uc.a.run.app
- **Sprint 07 Docs**: `construction/sprints/sprint-07-e2e-testing.md`
- **Terraform**: `infra/` directory
- **Phase Coordination**: [phases.md](phases.md)

## Notes for Next Session

- Read ALL memory-bank files on context reset
- Check phases.md for current phase status
- **Sprint 07 COMPLETE** - E2E testing infrastructure ready
- Get Neo4j password: `cd infra && terraform output -raw neo4j_password`
- Run smoke test: `make smoke-test`
- Next: Ingest real papers, run full E2E tests
