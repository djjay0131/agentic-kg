# Active Context

Last updated: 2026-04-16

## Current Work Focus

**Tooling/docs consolidation phase.** Ingestion pipeline working on GCP staging (commit `01d67f9`, 2026-04-01) — graph has 282 nodes, 151 edges, 18 ProblemMentions linked to ProblemConcepts, 5/5 sanity checks passing. Since then, two tooling initiatives committed or specified:

- **Constellize migration (committed `caf013d`, 2026-04-15):** retired `construction-agent`, `memory-agent`, and `code-review/` sub-agents in favor of `constellize:feature:*` + `constellize:memory:*` skills and new personas (`construction-lead`, `knowledge-steward`, `feature-architect`). `CLAUDE.md` rewritten to reflect the new workflow. Backlog published to Pages as `docs/backlog.md`.
- **Enhance-github-pages spec (2026-04-16, uncommitted):** `llm/features/enhance-github-pages.md` fully specified. Phased delivery (A: infra migration, B: content). Replaces fragile regex HTML generator with Jekyll `just-the-docs` + YAML data pipeline; adds PR preview deploys, HTMLProofer, Lighthouse CI, and structured `# docs-stats` block in `activeContext.md` to prevent content drift.

No source code changes committed since 01d67f9.

<!-- docs-stats: authoritative source for the Pages status dashboard. Keep in sync with prose. -->
```yaml
# docs-stats
last_updated: 2026-04-16
graph_nodes: 282
graph_edges: 151
problem_mentions: 18
problem_concepts: 18
sanity_checks: "5/5 passing"
completed_sprints: 11
tests_passing: 1217
```

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

1. Decide order of attack: implement `enhance-github-pages` Phase A vs. advance on KG schema enhancement gap analysis (Sprint 11 planning)
2. If pages work: run `/constellize:feature:implement enhance-github-pages` — Phase A is scoped to one sprint
3. Add OpenAI client timeout (60s) to prevent hanging extraction calls
4. Add `instructor` to `pyproject.toml` dependencies
5. Run full 20-paper ingestion to complete AC-10 (human review of graph quality ≥90%)
