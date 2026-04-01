# Active Context

Last updated: 2026-04-01

## Current Work Focus

**End-to-end ingestion pipeline working on GCP staging.** 30 problems extracted from 2 real papers, stored as ProblemMentions with INSTANCE_OF links to ProblemConcepts. 355 nodes, 115 edges. 4/5 sanity checks passing. Remaining: `papers_have_authors` (deployed fix awaiting re-test).

## Recent Significant Changes

- Fixed `to_neo4j_properties()` serialization — nested objects now JSON-serialized for Neo4j
- Initialized Neo4j schema on staging (constraints, indexes, 3 vector indexes ONLINE)
- Live test SUCCESS: 30 problems, 30 concepts, all INSTANCE_OF and EXTRACTED_FROM edges present
- All Cloud Build images rebuilt and deployed with all fixes
- Fixed `PaperImporter` NotFoundError mismatch (two different error classes)
- Fixed missing `link_paper_to_author` method in repository
- Test suite: 1059 core + 158 API = 1217 passing, 0 failures

## Open Decisions / Unresolved Questions

- Sprint 11 scope: entity expansion (Topics/Concepts) vs. real data ingestion first
- Production Neo4j hosting decision (Aura vs. self-managed on GCP)
- Entity extraction scope: all 9 types or prioritize top 4?
- Community detection algorithm: Leiden (hierarchical) vs. Louvain (simpler)?

## Known Issues

- `papers_have_authors` sanity check failing (26 papers) — `link_paper_to_author` fix deployed but needs re-test with fresh data
- `instructor` package not in `pyproject.toml` — installed locally but missing from dependency declarations
- arXiv_pdf variable scope bug in Denario core (`denario/langgraph_agents/literature.py:114`) — external dependency
- Legacy `memory-bank/` directory is stale; `llm/memory_bank/` is authoritative
- Large uncommitted changeset — needs commit and PR

## Immediate Next Steps

1. Run full 20-paper ingestion to complete AC-10 (human review of graph quality ≥90%)
2. Review graph in Neo4j Browser at `http://34.173.74.125:7474`
3. Commit all changes and create PR
4. Add `instructor` to `pyproject.toml` dependencies
5. Plan next feature from BACKLOG.md (D-1b GCS research log, or entity expansion E-1)
