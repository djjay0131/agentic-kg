# API Problems Read Model — Canonical ProblemConcept Fix

Status: IMPLEMENTED (2026-10-06)
Raised: 2026-10-06, verification of the deployed staging build
Issue: T17 (orchestrated brief)

## Problem

A staging ingest run (`trace orch-20261006-legacy-refill`) logged
`Ingestion complete: status=completed, problems=45` and created
Model/Method nodes, yet:

- `GET /api/stats` → `total_problems: 0`
- `GET /api/problems` → `total: 0`

Root cause: the read paths matched `(:Problem)`, but since the Canonical
Problem Architecture (Sprint 09/10, M7/M8) ingestion no longer writes
`:Problem`.

## Ground truth: what ingestion writes

Verified against `packages/core/src/agentic_kg/extraction/kg_integration_v2.py`
(`integrate_extracted_problems`), `auto_linker.py`, and `ingestion.py`.

| Written entity / edge | Where | Label / rel |
|---|---|---|
| Paper-specific problem | `KGIntegratorV2._store_mention_node` | `(:ProblemMention)` |
| Canonical problem | `AutoLinker.create_new_concept` / `_create_instance_of_relationship` | `(:ProblemConcept)` |
| Mention → its canonical problem | `auto_linker._create_*` | `(:ProblemMention)-[:INSTANCE_OF]->(:ProblemConcept)` |
| Mention → source paper | `_store_mention_node` | `(:ProblemMention)-[:EXTRACTED_FROM]->(:Paper)` |
| Problem concept → research concept (B3) | `integrate_paper_entities` | `(:ProblemConcept)-[:INVOLVES_CONCEPT]->(:ResearchConcept)` |
| Paper → topic | `integrate_paper_entities` | `(:Paper)-[:RESEARCHES]->(:Topic)` |
| Problem → topic (direct) | v3 topic migration / manual `assign_entity_to_topic` | `(:ProblemConcept|:ProblemMention|:Problem)-[:BELONGS_TO]->(:Topic)` |

Legacy `:Problem` nodes can still exist (pre-canonical ingestion; the v3
topic migration copies `domain` → `Topic` onto any source label including
`:Problem`). Nothing migrated `:Problem` → `:ProblemConcept`.

## Read paths that assumed `:Problem`

| Surface | File | Was |
|---|---|---|
| `/api/stats` | `api/main.py` | `MATCH (p:Problem)` ×3 |
| `/api/problems` list/detail/update/delete | `api/routers/problems.py` → `repository.list_problems/get_problem` | `MATCH (p:Problem)` |
| `/api/topics/{id}/problems` | `api/routers/topics.py` | `MATCH (p:Problem)-[:BELONGS_TO]->(t:Topic)` |
| `/api/graph`, `/api/graph/neighbors` | `api/routers/graph.py` | `(:Problem)`, `EXTRACTED_FROM`, `BELONGS_TO` |
| `/api/search` | `core/knowledge_graph/search.py` | `problem_embedding_idx`, `MATCH (p:Problem)` |
| `/api/agents/workflows` inputs | `agents/ranking.py`, `continuation.py`, `evaluation.py`, `synthesis.py` | `repo.list_problems` / `repo.get_problem` / `structured_search` |
| `/api/concepts/{id}/problems` | `api/routers/concepts.py` | already `ProblemConcept` (correct) |

UI (`packages/ui/src/lib/api.ts`) calls `/api/stats`, `/api/problems`,
`/api/problems/{id}`, `/api/search`, `/api/graph`, `/api/topics` and the
workflow endpoints; it renders `statement`, `status`, `confidence`,
`evidence`, `assumptions`, etc.

## Decision

Serve the canonical `ProblemConcept` as the problem, with a **union** over
legacy `:Problem`:

- A problem is a `ProblemConcept` whose `statement` is its
  `canonical_statement`; evidence and extraction metadata are taken from its
  first mention (joined to the source paper).
- Any legacy `:Problem` node still resolves, so no historical problem
  disappears. Concepts are considered first and ids are de-duplicated.
- `mention_count` / `paper_count` / `mentions` / `papers` are **additive**
  fields; existing response fields (`statement`, `status`, `confidence`,
  `evidence`, `assumptions`, `constraints`, `datasets`, `metrics`,
  `baselines`, `extraction_metadata`) keep their shape.

Topic association is a direct `BELONGS_TO` edge **or**, concept-side, a
mention extracted from a paper that `RESEARCHES` the topic. This is why a
freshly ingested concept with no direct BELONGS_TO edge still appears under
`/api/topics/{id}/problems`.

## Changes

- `core/knowledge_graph/repository.py` — new canonical read model:
  `list_problem_views`, `get_problem_view`, `update_problem_concept`,
  `delete_problem_concept`, `list_problem_views_for_topic`,
  `problem_ids_for_topic`, `semantic_problem_views`,
  `structured_problem_views`, `get_problem_stats`, plus the
  `problem_view_to_problem` adapter. `list_problems` / `get_problem` /
  `delete_problem` are now concept-aware for the research agents.
- `core/knowledge_graph/search.py` — vector + structured search union
  `concept_embedding_idx` and `problem_embedding_idx`.
- `api/main.py` — `/api/stats` delegates to `get_problem_stats()`.
- `api/routers/problems.py` — list/detail/update/delete serve the read model.
- `api/routers/topics.py` — `/api/topics/{id}/problems` serves concepts.
- `api/routers/graph.py` — problems are `ProblemConcept`; topic edges are
  direct or paper-derived.
- `api/schemas.py` — additive `ProblemSummary` / `ProblemDetail` fields and
  `ProblemMentionResponse`.
- `agents/continuation.py` — `_lookup_topic_name` resolves via concept too.

## Acceptance

- API unit tests (repository mocked) cover list/detail/update/delete, topic
  problems, stats, and concept-backed summaries/details.
- `packages/api/tests/integration/test_problem_read_model.py` builds
  `Paper-[:RESEARCHES]->Topic`,
  `ProblemMention-[:EXTRACTED_FROM]->Paper`,
  `ProblemMention-[:INSTANCE_OF]->ProblemConcept` the way ingestion does and
  asserts `/api/stats`, `/api/problems`, `/api/problems/{id}` (including
  mentions/evidence) and `/api/topics/{id}/problems` return it. It fails on
  the pre-fix code.

## Out of scope / noted

- Problem↔problem relation edges (`EXTENDS`/`CONTRADICTS`/…) are only ever
  written on legacy `:Problem` nodes by `relations.py`; ingestion writes none
  for concepts. `get_related_problems` is therefore unchanged and is a
  follow-up if relation inference is wired to concepts.
- `ProblemMention` is not surfaced as its own list resource; a mention id
  resolves to its canonical concept.
- The integration test runs in GitHub CI (Docker unavailable on the
  implementation host).
