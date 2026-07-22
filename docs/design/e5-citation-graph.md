---
title: E-5 · Citation graph
parent: Design
nav_order: 5
---

# E-5 · Citation graph

{: .label .label-green }
VERIFIED

**Backlog ID:** E-5 · **Depends on:** none (reuses the Semantic Scholar
client + `PaperImporter`) · **Enables:** influence chains, hub/authority
analysis, community-detection seeding (C-1) · **Spec:**
[`citation-graph.md`](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/citation-graph.md)

## Why

Every ingested Paper used to be an island. The graph could not answer "what
does this paper build on", "who in our corpus cites this seminal work", "what's
the citation chain from X to Y", or "which papers are most cited overall". Yet
Semantic Scholar already hands us a full reference list at ingestion time — the
data flowed through the pipeline and we discarded it. The gap analysis ranked
the missing paper-to-paper edge as the highest-priority relationship: without
it, influence-chain discovery, hub/authority analysis, and community-detection
seeding were all impossible.

## What shipped

A single self-referential relationship, `(:Paper)-[:CITES]->(:Paper)`,
populated automatically during ingestion from each paper's Semantic Scholar
reference list. Cited papers that aren't yet in the corpus are materialized as
**stub Paper nodes** so the graph is dense from day one. Two denormalized
counters on every Paper — `citation_count` (inbound edges) and
`reference_count` (outbound edges) — tick transactionally with edge creation.
The relationship is exposed through a repository surface, three REST endpoints,
and an `agentic-kg citation-graph` traversal CLI. See the
[Entity Relationships]({{ site.baseurl }}/reference/entity-relationships)
reference for the edge in context.

## Design decisions

**Stub nodes instead of dropping unknown citations.** A paper's reference list
routinely points at work we haven't ingested. Rather than silently drop those
edges (losing the citation graph's density) or block ingestion until every
cited paper is fetched (unbounded cost), E-5 creates a lightweight placeholder
Paper carrying just `doi`, `title`, `year`, and `is_stub=True`. The `CITES`
edge lands immediately, so influence chains and hub analysis work against the
full reference structure. When the stub's real paper is later ingested through
the normal `PaperImporter` flow, the placeholder is **promoted in place** — the
same node id, its scalar properties overwritten with the full payload,
`is_stub` flipped to `False`, and every inbound `CITES` edge and accumulated
`citation_count` preserved.

**`is_stub` is monotone.** A node starts as a stub (`true`), flips to `false`
on promotion or first full ingestion, and never reverts. There is no demotion
path. This makes "list all real papers" a simple index filter and guarantees
promotion is idempotent.

**Out-edges only at ingestion.** Only a paper's outbound references are fetched
(`get_paper_references`). Inbound citations grow organically as the corpus
grows — fetching "who cites X" per paper would balloon the stub population with
low-value single-edge nodes and multiply API cost. `citation_count` still
answers the inbound question against whatever's been ingested.

**DOI is the only identifier.** References without a DOI are skipped entirely —
no stub, no edge. Titles are too noisy for fuzzy dedup, and DOI uniqueness
(the existing `paper_doi_unique` constraint) handles stub deduplication for
free. No backfill command ships: operators re-ingest older papers to enrich
them, matching the project's "rebuild over migrate" stance.

**Plain edge, no properties.** Semantic Scholar's references endpoint doesn't
return citation context (intro / methods / related-work), and downstream
influence measures don't need it. A future LLM-driven pass could add properties
without a schema change.

## How it works

- **Model:** `Paper` in
  [`models/entities.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/knowledge_graph/models/entities.py)
  gains `is_stub`, `citation_count`, `reference_count`; `year` was relaxed to
  `Optional` and `title.min_length` from 10 → 2 so partial-metadata stubs
  validate.
- **Orchestration:**
  [`citation_graph.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/knowledge_graph/citation_graph.py)
  — `populate_citations` resolves the paper's Semantic Scholar id, fetches the
  reference list, and for each DOI-bearing reference creates-or-promotes a stub
  then links `CITES`. Every failure is logged WARN and absorbed into a
  `CitationPopulationResult`; citation enrichment never blocks ingestion.
- **Repository:**
  [`repository.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/knowledge_graph/repository.py)
  — `link_paper_cites_paper` / `unlink_paper_cites_paper` (idempotent edge +
  counter maintenance), `create_or_promote_paper_stub`, `_promote_paper_stub`,
  `get_references`, `get_citing_papers`, `count_citations`.
- **Schema:** `paper_is_stub_idx` index and the `CITES` edge, at
  `SCHEMA_VERSION = 7`, in
  [`schema.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/knowledge_graph/schema.py).
  No new constraint — DOI uniqueness already covers stub dedup.
- **Ingestion hook:** `PaperImporter.import_paper` calls `populate_citations`
  by default and routes existing-stub DOIs through the promotion path.
- **Surfaces:** REST `GET /api/papers/{doi}/references`, `/citations`,
  `/citation-counts`; CLI `agentic-kg citation-graph --paper-doi <doi>
  [--depth N] [--direction in|out|both]`.

For the node's attributes and its edges in context, see the
[Entity Catalog]({{ site.baseurl }}/reference/entity-catalog#paper) and
[Entity Relationships]({{ site.baseurl }}/reference/entity-relationships).

## Verification

- **Tests:** Paper stub/entity changes, idempotent edge + counter behavior,
  `create_or_promote_paper_stub` idempotency, and a testcontainers integration
  test exercising the full round trip — ingest a paper with mocked references,
  confirm stubs land with the right shape, then ingest a cited paper and
  confirm the stub is promoted with its `CITES` edges and `citation_count`
  preserved.
- **Sentinel:** a stub-promotion smoke test asserts a repeated
  `create_or_promote_paper_stub` returns the existing node unchanged — guarding
  the regression where the stub gets overwritten on every reference pass.
- **Status:** VERIFIED — Units 1-7 landed 2026-06-11; verify gates passed
  2026-06-12.

## Related

- Reference: [Entity Relationships]({{ site.baseurl }}/reference/entity-relationships) · [Entity Catalog]({{ site.baseurl }}/reference/entity-catalog#paper)
- Enables: community detection (**C-1**) — a dense Paper-to-Paper edge set to seed Leiden/Louvain partitioning
- Deferred: `get_paper_citations` at ingestion, stub GC/TTL, `SIMILAR_TO` edges, citation-context properties
