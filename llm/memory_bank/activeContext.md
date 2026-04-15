# Active Context

Last updated: 2026-04-15

## Current Work Focus

**Ingestion pipeline working on GCP staging (last commit `01d67f9`, 2026-04-01).** Graph has 282 nodes, 151 edges from live papers. 18 ProblemMentions with INSTANCE_OF links to ProblemConcepts. 5/5 sanity checks passing. New KG schema enhancement gap analysis drafted (`construction/design/kg-schema-enhancement-gap-analysis.md`) — next feature planning input. No source changes committed in last 2 weeks.

## Recent Significant Changes

- Committed `01d67f9`: full ingestion pipeline (Cloud Run Jobs, CLI, API, serialization fixes)
- Fixed `to_neo4j_properties()` serialization — nested objects now JSON-serialized for Neo4j
- Initialized Neo4j schema on staging (constraints, indexes, 3 vector indexes ONLINE)
- Live test: 18 problems extracted from 2 papers, 18 concepts created, all linked
- All Cloud Build images rebuilt and deployed with all fixes
- `papers_have_authors` sanity check now passes (link_paper_to_author fix deployed)
- Test suite: 1059 core + 158 API = 1217 passing, 0 failures

## Open Decisions / Unresolved Questions

- Sprint 11 scope: entity expansion (Topics/Concepts) vs. real data ingestion first
- Production Neo4j hosting decision (Aura vs. self-managed on GCP)
- Entity extraction scope: all 9 types or prioritize top 4?
- Community detection algorithm: Leiden (hierarchical) vs. Louvain (simpler)?

## Known Issues

- OpenAI API intermittently hangs on longer extraction calls (no timeout configured) — larger ingestion runs get stuck
- `instructor` package not in `pyproject.toml` — installed locally but missing from dependency declarations
- `mentions_linked_to_paper` sanity check sometimes fails when papers pre-exist from earlier imports (DOI casing mismatch in EXTRACTED_FROM edge creation)
- arXiv_pdf variable scope bug in Denario core (`denario/langgraph_agents/literature.py:114`) — external dependency
- Legacy `memory-bank/` directory is stale; `llm/memory_bank/` is authoritative

## Immediate Next Steps

1. Review KG schema enhancement gap analysis → decide on scope of entity expansion
2. Add OpenAI client timeout (60s) to prevent hanging extraction calls
3. Add `instructor` to `pyproject.toml` dependencies
4. Run full 20-paper ingestion to complete AC-10 (human review of graph quality ≥90%)
5. Review graph in Neo4j Browser at `http://34.173.74.125:7474`
6. Commit new `.claude/agents/` scaffolding (construction-lead, feature-architect, knowledge-steward) or discard if experimental
