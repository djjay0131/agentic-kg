# Active Context

Last updated: 2026-05-20

## Current Work Focus

**All four open PRs landed (2026-05-19).** PR #25 (`fix/ci-health`, `f78d3f5`), PR #22 (E-1 Topic, `60b3f8a`), PR #26 (E-2 ResearchConcept, replacing auto-closed #23, `1d32bf5`), and PR #24 (enhance-github-pages Phase A, `42ee5fe`) all merged to master in that order. Master CI is now green end-to-end for the first time since 2026-04-17.

**The merge effort surfaced and fixed seven pre-existing bugs** that had been hidden because CI was broken upstream:

1. **148 ruff lint errors on master.** `code-review.yml` only ran lint on PRs, so master drifted red invisibly. Auto-fixable cleared via `ruff --fix`; 59 E501 lines wrapped (string content byte-identical); 3 dead variables removed.
2. **`feedparser` `ModuleNotFoundError` in CI.** `integration-tests.yml` and `code-review.yml` installed via `pip install -e ".[dev]"` from the root, whose `pyproject.toml` declared only 4 deps and never pulled `packages/core`'s dependency set. Now both workflows install `./packages/core` and `./packages/api` directly.
3. **e2e tests leaking into the unit job.** `test.yml` ran the entire `packages/core/tests` tree, so `TestSemanticScholarE2E::test_search_papers` hit the live API and flaked on rate limits. Now ignores `packages/core/tests/e2e`.
4. **`Problem` double-JSON-encoding.** `Problem.to_neo4j_properties()` already JSON-encoded nested fields, but `repository.create_problem` / `update_problem` JSON-encoded them again. On read-back, a single `json.loads` left a string, and indexing it (`meta["extracted_at"]`) raised `TypeError: string indices must be integers`. Removed the redundant encoding; aligned `Problem` storage with `ProblemMention`/`Paper`. Added `decode_json_field` helper to `_problem_from_neo4j` (both repository.py and search.py) so legacy double-encoded staging nodes still read; `list_problems` / `structured_search` skip-and-log a single malformed node instead of crashing the whole query.
5. **Integration-test data pollution.** Fixtures created Problems with fixed statements, but the repo dedups by statement, so re-runs collided (DuplicateError). `sample_problem_data`, `two_problems`, and `p3` now use per-run unique, TEST_-marked statements; the `neo4j_repository` cleanup also matches `statement STARTS WITH 'TEST_'` and runs at setup as well as teardown so a crashed run self-heals.
6. **E-1's integration tests had never run.** When the pipeline started working, E-1's integration job revealed 33 failures: (a) v3-migration Cypher had a syntax error (`pair.topic` property access inside a node pattern) and a NULL `parent_id` MERGE that Neo4j 5 rejects; (b) `merge_topic` / `taxonomy._topic_exists` used `{parent_id: $pid}` patterns with `$pid=None`, which never matches and breaks MERGE in Neo4j 5; (c) Unit 7's "domain→topic rename sweep" never updated `conftest.py`, `test_repository.py`, `test_relations.py`, or `test_search.py`. All fixed; the test files now build real `Topic` entities and filter by `topic_id`.
7. **HTMLProofer 5 breakage on `enhance-github-pages`.** The PR's `preview-docs.yml` and `update-docs.yml` passed `--check-html` and `--check-favicon` (removed in HTMLProofer 5.x). Stripped both; added `--swap-urls` to handle the Jekyll baseurl prefix; `--no-enforce-https` to allow the staging Neo4j browser endpoint.

**Spec work in flight.** `llm/features/extraction-prompt-expansion.md` (E-8) drafted and fully reviewed (Tech Lead + QA personas, 4 questions, locked decisions) but **not yet committed** (still untracked in working tree). Spec is implementation-ready; ready for `/constellize:feature:implement extraction-prompt-expansion` whenever you want to start it.

<!-- docs-stats: authoritative source for the Pages status dashboard. Keep in sync with prose. -->
```yaml
# docs-stats
last_updated: 2026-05-20
graph_nodes: 282
graph_edges: 151
problem_mentions: 18
problem_concepts: 18
sanity_checks: "5/5 passing"
completed_sprints: 11
tests_passing: 1312
```

## Recent Significant Changes

- Merged: `fix/ci-health` → master (`f78d3f5`) — lint debt cleared, test workflows fixed.
- Merged: E-1 Topic entities (`60b3f8a`) — 8 units; integration tests now passing with Cypher and merge fixes.
- Merged: E-2 ResearchConcept entities (`1d32bf5`, PR #26 — old #23 auto-closed when E-1's base branch was deleted). 7 commits cherry-picked onto fixed master.
- Merged: enhance-github-pages Phase A (`42ee5fe`) — Jekyll `just-the-docs`, PR previews, HTMLProofer wired up.
- Fixed `create_problem`/`update_problem` double-encoding bug (latent on master since the original `to_neo4j_properties` serialization change).
- Test suite: 1312 core unit tests pass + integration green on master.

## Open Decisions / Unresolved Questions

- Production Neo4j hosting decision (Aura vs. self-managed on GCP)
- Entity extraction scope: all 9 types or prioritize top 4?
- Community detection algorithm: Leiden (hierarchical) vs. Louvain (simpler)?
- E-8 implementation start — depends on E-1 and E-2 being merged (now both done). Operator decision: kick off `/constellize:feature:implement extraction-prompt-expansion`?

## Known Issues

- OpenAI API intermittently hangs on longer extraction calls (no timeout configured) — larger ingestion runs get stuck
- `instructor` package not in `pyproject.toml` — installed locally but missing from dependency declarations
- `mentions_linked_to_paper` sanity check sometimes fails when papers pre-exist from earlier imports (DOI casing mismatch in EXTRACTED_FROM edge creation)
- arXiv_pdf variable scope bug in Denario core (`denario/langgraph_agents/literature.py:114`) — external dependency
- Legacy `memory-bank/` directory is stale; `llm/memory_bank/` is authoritative
- `docs/status/service-inventory.html` publishes the staging Neo4j browser endpoint (`http://34.173.74.125:7474`) — should be scrubbed; advertising an open Neo4j browser is not ideal even on a private staging DB
- PR #16 (Cloud Build triggers) — still open, no checks, stale; needs a decision (keep / close / revisit)
- E-8 spec (`llm/features/extraction-prompt-expansion.md`) is **untracked** in working tree — commit before reboot if you want it persisted to git

## Immediate Next Steps

1. Commit the untracked E-8 spec so it survives reboot
2. Decide on PR #16 (Cloud Build triggers)
3. Scrub the staging Neo4j IP from `docs/status/service-inventory.html`
4. Start E-8 implementation via `/constellize:feature:implement extraction-prompt-expansion` (E-1 and E-2 deps now satisfied)
5. Add OpenAI client timeout (60s) to prevent hanging extraction calls
6. Add `instructor` to `pyproject.toml` dependencies
7. Run full 20-paper ingestion to complete AC-10 (human review of graph quality ≥90%)
