# Active Context

Last updated: 2026-04-21

## Current Work Focus

**Entity expansion phase — E-1 shipped, E-2 VERIFIED.** E-1 (Topic/Research Area entities) implemented on branch `feat/e-1-topic-model-unit-1` across 8 units, PR #22. E-2 (ResearchConcept entities) implemented on branch `feat/e-2-research-concept` across 6 units, PR #23 stacked on E-1. Both branches stacked; E-2 targets E-1's branch.

**E-2 verification (2026-04-21):** ran `/constellize:feature:verify` scoped to E-2 files. All four gates PASS: 99 E-2 unit tests (77 core + 22 API), 100% coverage on every E-2 source file's unit-testable code, ruff clean on every E-2 file, CLI + API smoke checks green. 22 integration repo tests (`test_research_concept_repository.py`) require live Neo4j and ran to plan in CI but not locally. Fixes applied during verification: added `test_non_mapping_entry_raises` and tests for `compute_pair_similarities` / `run_calibration` / `generate_research_concept_embedding` / `calibrate-concepts` CLI dispatch / inner `list_concepts` tx closure to reach 100% scoped coverage; ruff autofixed import ordering across six E-2 test files and `routers/concepts.py`. Feature status flipped SPECIFIED → IMPLEMENTED → VERIFIED.

**Descoped during verification:**
- **AC-7** (shared `BaseGraphEntity` + `EntityService` abstraction): not payable with only two entity types in hand. Deferred as a follow-up refactor; revisit when E-3 (Model) or E-4 (Method) adds a third call site. Spec updated accordingly.
- **AC-13** (staging deploy + operator review): pending operator action; not verifiable from local environment.

**Pre-dating tooling context:**
- **Constellize migration (committed `caf013d`, 2026-04-15):** retired `construction-agent`, `memory-agent`, and `code-review/` sub-agents in favor of `constellize:feature:*` + `constellize:memory:*` skills and new personas (`construction-lead`, `knowledge-steward`, `feature-architect`). `CLAUDE.md` rewritten to reflect the new workflow. Backlog published to Pages as `docs/backlog.md`.
- **Enhance-github-pages spec (committed `fb7176c`, PR #19, 2026-04-16):** `llm/features/enhance-github-pages.md` fully specified. Phased delivery (A: infra migration, B: content).

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

1. **Three specs ready to implement:** enhance-github-pages (Phase A), E-1 (Topic entities), E-2 (ResearchConcept entities). All decoupled — can be implemented in any order or parallel.
2. Spec remaining high-priority features: E-8 (extraction prompt expansion), then E-3/E-4/E-5
3. Add OpenAI client timeout (60s) to prevent hanging extraction calls
4. Add `instructor` to `pyproject.toml` dependencies
5. Run full 20-paper ingestion to complete AC-10 (human review of graph quality ≥90%)
