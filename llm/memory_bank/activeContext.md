# Active Context

Last updated: 2026-04-21

## Current Work Focus

**enhance-github-pages Phase A VERIFIED (2026-04-21).** Ran `/constellize:feature:implement` followed by `/constellize:feature:verify` on `feat/enhance-github-pages` (branched off master). 4 units shipped: (1) `generate_site_data.py` emitter with Pydantic `DocsStats` validation + 31 unit tests at 100% coverage; (2) Jekyll `just-the-docs` site skeleton with `about/` + `status/` sections, 3 Liquid includes, and placeholder page content; (3) rewrote `update-docs.yml` with new watched paths + concurrency + HTMLProofer, added `preview-docs.yml` for PR previews via `rossjrw/pr-preview-action`; (4) 56-test static structure/workflow suite. 87 tests total, ruff clean, all 4 quality gates pass. Phase A acceptance criteria (AC-1,2,4,5,6,7,8,9,10,11,12,15,16) satisfied — AC-1's real Jekyll build is CI-only (local Ruby 2.6 < 3.0); AC-8 delegated to unmodified just-the-docs theme defaults. Phase B (AC-3, 13, 14) deferred — placeholder content + TODO markers in every `about/*.md` and `status/*.md` page. Closes backlog items P-6 (auto-regenerate backlog from source) and P-7 (workflow trigger paths migrated away from stale `memory-bank/`).

**Tooling/docs consolidation phase.** Ingestion pipeline working on GCP staging (commit `01d67f9`, 2026-04-01) — graph has 282 nodes, 151 edges, 18 ProblemMentions linked to ProblemConcepts, 5/5 sanity checks passing. Since then, tooling initiatives committed or specified:

- **Constellize migration (committed `caf013d`, 2026-04-15):** retired `construction-agent`, `memory-agent`, and `code-review/` sub-agents in favor of `constellize:feature:*` + `constellize:memory:*` skills and new personas (`construction-lead`, `knowledge-steward`, `feature-architect`). `CLAUDE.md` rewritten to reflect the new workflow. Backlog published to Pages as `docs/backlog.md`.
- **Enhance-github-pages spec (committed `fb7176c`, PR #19, 2026-04-16):** `llm/features/enhance-github-pages.md` fully specified. Phased delivery (A: infra migration, B: content). Replaces fragile regex HTML generator with Jekyll `just-the-docs` + YAML data pipeline; adds PR preview deploys, HTMLProofer, Lighthouse CI, and structured `# docs-stats` block in `activeContext.md`.
- **E-1 Topic entity spec (committed `6a2ccde`, PR #20, 2026-04-16):** `llm/features/topic-research-area-entities.md` fully specified. First-class Topic nodes with 3-level hierarchy, seeded taxonomy, domain migration, manual assignment. Decoupled from E-8. Flags T-1 (taxonomy at scale).
- **E-2 ResearchConcept entity spec (2026-04-17):** `llm/features/research-concept-entities.md` fully specified. Generic research concepts as first-class nodes, embedding-based dedup, manual population. Introduces shared `BaseGraphEntity`/`EntityService` abstraction. Drops `RELATED_TO` in favor of typed edges in E-8/C-1. Decoupled from E-8.

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

1. **Three specs ready to implement:** enhance-github-pages (Phase A), E-1 (Topic entities), E-2 (ResearchConcept entities). All decoupled — can be implemented in any order or parallel.
2. Spec remaining high-priority features: E-8 (extraction prompt expansion), then E-3/E-4/E-5
3. Add OpenAI client timeout (60s) to prevent hanging extraction calls
4. Add `instructor` to `pyproject.toml` dependencies
5. Run full 20-paper ingestion to complete AC-10 (human review of graph quality ≥90%)
