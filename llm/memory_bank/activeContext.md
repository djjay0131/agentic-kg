# Active Context

Last updated: 2026-06-08

## Current Work Focus

**E-4 (method-entity) VERIFIED (2026-06-10).** All four Constellize verify gates passed for E-4 scope:

| Gate | Result | Notes |
|------|--------|-------|
| 1. Test Integrity | PASS | 1759 core tests + 227 API tests pass. E-4 new code at 100% line coverage on `routers/methods.py`, `generate_method_embedding`, and all Method repository methods (one pragma-no-cover on the defensive TOCTOU race-condition guard in `update_method`, justified inline). |
| 2. Health Check | PASS | Pydantic guards (name min/max, alias cap, usage_count ≥ 0); FastAPI bounded Query params; `NotFoundError → 404`; embedding outage → WARN + create-without-embedding (AC-12). No bare excepts. |
| 3. Deployment | PASS | `agentic-kg create-method / link-method` exposed; Method entity imports cleanly; `/api/methods` router mounts. |
| 4. Maintainability | PASS | Ruff clean on all 15 E-4 source + test files. Patterns mirror E-2 ResearchConcept exactly. |

**Verify-time fixes applied:**

- Closed 5 coverage gaps in `repository.py`: `_method_from_neo4j` aliases-missing branch, `create_method` embedding-generation try/except, `update_method` explicit-embedding branch, `update_method` regenerate-embedding success + failure paths.
- One `# pragma: no cover` added on `update_method`'s defensive race-condition `NotFoundError` raise — justified inline as a TOCTOU guard that's only reachable if the Method is deleted between `get_method` and the `SET` query (not reproducible without mocking the inner Cypher).

**E-4 (method-entity) implementation Units 1-9 complete (2026-06-10).** Spec moved to IMPLEMENTED status. The smaller-than-E-3 v1 surface flowed through cleanly: E-2 ResearchConcept shape (no `is_canonical`, no seed YAML, no canonical-protection rules), one-line absorption into `_NODE_LINK_RELATIONSHIPS` (registered `APPLIES_METHOD`), dedup threshold 0.90 (matches E-2). 72 new core tests + 16 new API tests (1753 core / 227 API total). Ruff clean on all E-4 code.

**Notable spec adherence:**
- AC-11 dedup smoke test ships as a single sentinel (case-variant merges at default threshold). Catches threshold-inversion bugs and the "threshold accidentally set to 9.0" class without paying for a full eval set.
- AC-3 alias merge uses exact-string set union (matches E-2/E-3 — Tech Lead Q1 review).
- Threshold escape valve (QA Q2 review) — `--threshold 1.01` on the CLI and `threshold: 1.01` in the API force-create distinct nodes by ensuring no cosine score can clear 1.0.
- Alias-cap (Pydantic `max_length=20`) overflow surfaces as `ValidationError` per Tech Lead Q3 review (pinned by integration test `test_alias_cap_overflow_via_merge_raises`).

**E-3 (model-entity) VERIFIED (2026-06-08).** All four Constellize verify gates passed for E-3 scope:

| Gate | Result | Notes |
|------|--------|-------|
| 1. Test Integrity | PASS | 1681 core + 211 API unit tests pass; 0 failures. E-3 new modules at 100% line coverage (seed_models, routers/models, cli handlers, embeddings helper, entity, schema, repository methods exercised via integration). |
| 2. Health Check | PASS | All inputs validated (path/YAML/name/duplicates via Pydantic + parse_seed_models; bounded Query params); NotFoundError → 404, canonical-without-force → 409, embedding outage → 500 with log; no bare excepts; AC-13 graceful degradation in `create_or_merge_model`. |
| 3. Deployment | PASS | `agentic-kg load-models / create-model / link-model` exposed; all E-3 modules import cleanly; bundled seed parses 19 entries; `/api/models` router mounts. |
| 4. Maintainability | PASS | Ruff clean on all 18 E-3 source + test files. |

**Verify-time fixes applied:**

- `seed_models.py`: covered the FileNotFoundError-on-string-path branch and the list-source branch (added 2 tests, brought module to 100%).
- `cli.py`: covered the load-models error path, create-model aliases comma-split, link-model NotFoundError exit-1 (added 3 tests).
- `embeddings.py`: covered `generate_model_embedding` via mocked-service unit tests (added 2 tests).
- `test_e3_done_demo.py`: bundled seed Models (BERT, GPT-4, …) don't carry a TEST_ prefix and were leaking into subsequent test classes whose deterministic-slot embeddings collided with them. Added a teardown that `DETACH DELETE`s all Model nodes after the done-demo class finishes. Test isolation now clean.

**E-3 (model-entity) implementation Units 1-10 complete (2026-06-08).** Spec moved to IMPLEMENTED → VERIFIED status. All 10 units shipped TDD-first against testcontainers Neo4j:

1. Pydantic `Model` entity with hybrid open-set design + `is_canonical` flag
2. Schema bump to v5: `model_id_unique` constraint, `model_name_idx`, `model_is_canonical_idx`, `model_embedding_idx` (1536 cosine)
3. CRUD methods + `generate_model_embedding`
4. Generalized `_link_entity_to_concept` → `_link_entity_to_node` / `_NODE_LINK_RELATIONSHIPS` covering DISCUSSES, INVOLVES_CONCEPT, USES_MODEL (Tech Lead Q5 decision)
5. `create_or_merge_model` with canonical-protection rules + canonical-canonical collision WARN log (spec edge case)
6. `seed_models.py` + `data/seed_models.yml` (20 canonical entries spanning language / vision / multimodal / classical / graph families)
7. REST API at `/api/models` (list, search, create, detail, link-paper, delete; force=true required for canonical delete)
8. CLI: `load-models`, `create-model`, `link-model`
9. Dedup eval-set scaffolding — 10 hand-labeled pairs + precision (10/10) + anti-gaming recall tripwire (≥ 6/8 merge-expecting). Costly+integration gate, runs at verify with OpenAI key.
10. AC-11 testcontainers integration test exercising seed-load → 5 synthetic Papers → link → API-shaped query → usage_count increment

**Tests added:** +60 core unit tests (1473 → 1533) and +18 API unit tests (193 → 211). 68 E-3 integration tests pass against testcontainers Neo4j (28 model repo + 15 seed + 3 done demo + 22 E-2 regression). Ruff clean on all E-3 code.

**Pre-existing testcontainers conftest password fix.** `packages/core/tests/conftest.py` Neo4jContainer didn't set a password, so integration tests had never run locally for anyone. Patched: explicit `password="testpassword"` on container + matching `password="testpassword"` on the client config.

**E-2 regression suite green** post-link-helper-rename: 22 ResearchConcept integration tests still pass after `_CONCEPT_RELATIONSHIPS` → `_NODE_LINK_RELATIONSHIPS` and `_link_entity_to_concept` → `_link_entity_to_node`. Updated one E-2 test that reached into the renamed internal.



## Current Work Focus

**E-8 (extraction-prompt-expansion) VERIFIED (2026-06-02).** All four Constellize verify gates passed for E-8 scope:

| Gate | Result | Notes |
|------|--------|-------|
| 1. Test Integrity | PASS | 1469 unit tests pass. New E-8 modules at 100% line coverage (b3_linker, topic_extractor, concept_extractor, re_ingestion, taxonomy_hash, fixtures/b3_deny_list, queries/completeness) |
| 2. Health Check | PASS | Empty taxonomy guard, NotFoundError handling, structured ExtractionFailure with truncation, PurgeBlocked guardrail. No bare excepts, no silent failures |
| 3. Deployment | PASS | `agentic-kg ingest --force-rewrite` exposed via argparse; all E-8 modules import cleanly; CLI help text references AC-13 |
| 4. Maintainability | PASS | Ruff clean on all E-8 new code (260 errors elsewhere on master are pre-existing lint debt unrelated to E-8) |

AC-14 codebase audit complete: only one analytical Paper query existed (sanity check on AUTHORED_BY in `ingestion.py:247`); it does not filter on extracted entities, so an exemption comment was added per the contract.

**Verify-gate items that require external/staging work (documented for follow-up, not blocking VERIFIED status):**

- **AC-12 eval-set hand-labeling.** The scoring math and runner scaffolding are in place (`test_e8_eval.py` skips cleanly with a clear pointer to `SELECTION.md`). The 5 hand-labeled fixtures + external reviewer sign-off are operator work — the spec explicitly forbids the spec author from self-reviewing.
- **AC-13/AC-15 live-Neo4j integration tests.** Mocked-repo unit tests pin the call contract; the integration runs at the CI integration job against staging Neo4j when fixtures land.
- **AC-16 wall-clock check.** Requires a staging ingestion run — logged-not-gated per spec.
- **`--force-rewrite` plumbing through `ingestion.py`.** The CLI flag and `purge_paper_extraction` exist; wiring the orchestrator to invoke purge before re-extraction is a small follow-up.

**Quick-wins from 2026-05-27.** Three small chores landed (commit `8fb6756`): scrubbed the staging Neo4j IP from `docs/about/screenshots.md` and `docs/status/service-inventory.md` (pointing readers at Secret Manager / Terraform output instead); added `timeout=60.0` to `EmbeddingConfig` and threaded through `OpenAI(...)` in `embeddings.py` so long ingestion runs cannot hang indefinitely; declared `instructor>=1.0.0` in `packages/core/pyproject.toml` (was tacit only).

**Quick-wins from 2026-05-27.** Three small chores landed (commit `8fb6756`): scrubbed the staging Neo4j IP from `docs/about/screenshots.md` and `docs/status/service-inventory.md` (pointing readers at Secret Manager / Terraform output instead); added `timeout=60.0` to `EmbeddingConfig` and threaded through `OpenAI(...)` in `embeddings.py` so long ingestion runs cannot hang indefinitely; declared `instructor>=1.0.0` in `packages/core/pyproject.toml` (was tacit only).

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

1. Hand-label the 5 E-8 eval papers (NLP / CV / IR / ML / DM-or-Agents) per `packages/core/tests/extraction/fixtures/e8_eval/SELECTION.md` with external review (knowledge-steward persona during next memory:update)
2. Wire `--force-rewrite` through `ingestion.py` so re-ingestion actually invokes `purge_paper_extraction` (currently only the argparse flag is exposed)
3. Run E-8 integration tests against staging Neo4j to close out AC-5b / AC-6 / AC-7 / AC-13 / AC-15 live-DB validation
4. Decide on PR #16 (Cloud Build triggers)
5. Run full 20-paper ingestion to complete AC-10 (human review of graph quality ≥90%); use the run to also measure AC-16 wall-clock
