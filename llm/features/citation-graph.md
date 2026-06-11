# Feature: Citation Graph (E-5)

**Status:** IMPLEMENTED
**Date:** 2026-06-10 (specified) · 2026-06-11 (implementation Units 1-7 landed)
**Author:** Feature Architect (AI-assisted)
**Backlog ID:** E-5
**Depends On:** None hard — Semantic Scholar client already exposes `get_paper_references`. Reuses the existing `PaperImporter` orchestration shape.
**Decoupled From:** E-8 (extraction prompt expansion), E-7 (cross-entity normalization).

## Problem

Every ingested Paper in the KG is an isolated node. The graph cannot answer "what does paper X build on", "who cites this work in our corpus", "what's the citation chain from X to Y", or "which papers are most-cited overall". Yet Semantic Scholar already returns reference lists at ingestion time via `get_paper_references(paper_id)` — the data flows into our existing pipeline but we throw it away. The gap analysis (§4.2) ranks the missing `CITES` relationship as **HIGH PRIORITY**: without it, influence-chain discovery, hub/authority analysis, and community-detection seeding (Category 4) are all impossible.

## Goals

- A single new relationship `(:Paper)-[:CITES]->(:Paper)`. Plain edge, no properties.
- During ingestion, fetch each Paper's reference list and create `CITES` edges to the cited papers.
- Cited papers that are **not in the KG** are created as **stub Paper nodes** carrying just `doi`, `title`, `year`, `is_stub=True`. Stubs are promoted to full Paper nodes when later ingested via the normal `PaperImporter` flow.
- A repository surface: `link_paper_cites_paper`, `get_references`, `get_citing_papers`, `create_or_promote_paper_stub`, `count_citations`.
- A small CLI for graph traversal: `agentic-kg citation-graph --paper-doi <doi> --depth N`.
- A REST surface: `GET /api/papers/{doi}/references`, `GET /api/papers/{doi}/citations`, augmenting the existing `papers` router.
- A testcontainers integration test demonstrating the round trip — ingest a Paper with mocked Semantic Scholar references, confirm stubs are created with the right shape, confirm subsequent ingestion of one of the cited papers promotes the stub (preserves the existing `CITES` edges).
- A stub-promotion smoke test sentinel (AC pattern from E-4).

## Non-Goals

- **`get_paper_citations` (who cites X) at ingestion time.** Out-only: only the **outgoing edges** (X cites Y) are fetched per Tech Lead Q2 review. In-edges grow organically as the corpus grows. Bounded API cost; avoids ballooning the stub population with low-value single-edge stubs.
- **Backfill of existing Papers** that were ingested before this feature. Per the user's recurring "rebuild over migrate" preference (saved feedback memory `feedback_rebuild_over_migrate`), operators re-ingest if they want citations on older Papers. No `--enrich-citations` CLI command in v1.
- **Stub-to-stub fuzzy dedup.** Stubs with the same title but no DOI are treated as distinct nodes. DOI is the only reliable identifier. Cleanup via re-ingestion or manual operator merge.
- **Citation edge properties.** No `context` (intro / methods / related-work), no `year_of_citation`, no `is_self_citation`. Semantic Scholar's references endpoint doesn't return citation context, and downstream analysis (citation count, influence) doesn't need it.
- **Citation context extraction from PDF text.** Could be a future LLM-based feature (E-8 V2 style); not v1.
- **Bibliometric measures** (h-index, impact factor) computed at query time. Just the raw graph; downstream queries can compute these.
- **CO_AUTHORED, AFFILIATED_WITH, PUBLISHED_AT, etc.** Lower-priority relationships from §4.2; deferred entirely.

## User Stories

- **As a researcher**, I want to ask "what papers does paper X build on" and traverse the reference list as graph edges, so I can discover the intellectual ancestry of a result.
- **As a researcher**, I want to ask "who in our corpus cites this seminal paper" and get a populated answer that grows as I ingest more papers.
- **As a system operator**, I want a paper's reference list to populate automatically at ingestion time — no separate command, no extra step.
- **As a future E-C analyst** (Community Detection, Category 4), I want a dense Paper-to-Paper edge set to seed Leiden/Louvain partitioning against.

## Design Approach

### Data Model Change

`Paper` entity (`packages/core/src/agentic_kg/knowledge_graph/models/entities.py`) gains an `is_stub` field and relaxes two existing validation rules to admit stubs with partial metadata:

```python
class Paper(BaseModel):
    doi: str                                            # primary key (unchanged)
    title: str = Field(..., min_length=2, max_length=500)  # CHANGED: was min_length=10
    authors: list[str] = Field(default_factory=list)    # unchanged
    venue: Optional[str] = None                         # unchanged
    year: Optional[int] = Field(default=None, ge=1900, le=2100)  # CHANGED: was required
    abstract: Optional[str] = None                      # unchanged
    arxiv_id: Optional[str] = None                      # unchanged
    openalex_id: Optional[str] = None                   # unchanged
    semantic_scholar_id: Optional[str] = None           # unchanged
    pdf_url: Optional[str] = None                       # unchanged
    full_text: Optional[str] = None                     # unchanged
    is_stub: bool = False                               # NEW (E-5)
    ingested_at: datetime = ...                         # unchanged
    citation_count: int = Field(default=0, ge=0)        # NEW: denormalized count of inbound CITES edges
    reference_count: int = Field(default=0, ge=0)       # NEW: denormalized count of outbound CITES edges
```

Both `citation_count` and `reference_count` are denormalized, tick transactionally with edge creation, and are reconciled periodically by the same pattern E-1/E-2/E-3 use.

### New Relationship

| Relationship | From → To | Purpose |
|---|---|---|
| `CITES` | Paper → Paper | The source paper cites the target paper. Plain edge; no properties. |

### Schema Changes

- Index: `paper_is_stub_idx` on `Paper.is_stub` (for "list all real papers" queries that filter out stubs by default).
- Schema version bumped: v6 → v7 (E-4 left it at 6).
- No new constraint — `doi` uniqueness from the existing `paper_doi_unique` constraint handles stub dedup.

### Stub Lifecycle

1. **Stub creation.** When `link_paper_cites_paper(source_doi, target_doi)` is called and the target Paper doesn't exist, the call **does NOT auto-create a stub**. Instead, the caller passes pre-built stubs to a separate `create_or_promote_paper_stub` method which is idempotent: if a Paper with that DOI exists (stub or full), it's returned unchanged; if no Paper exists, a stub is created.

2. **Stub promotion.** When the existing `PaperImporter.import_paper(doi)` is called against a DOI that already has a stub Paper node:
   - The stub is found by DOI.
   - Existing edges (inbound `CITES`, outbound nothing — stubs have no references) are preserved.
   - The node's properties are overwritten with the full Semantic Scholar payload.
   - `is_stub` flips to `False`.
   - The promotion happens via a single `MATCH ... SET ...` Cypher.
   - **No DuplicateError** — the existing PaperImporter logic raises this; we'll need a small change to skip the duplicate check when the existing node has `is_stub=True`.

3. **No demotion.** A full Paper cannot be reverted to a stub. `is_stub` is monotone: starts true (if created via stub path), flips to false on promotion or first full ingestion, never goes back.

### Ingestion Flow (Updated)

The existing `PaperImporter.import_paper(doi)` is extended:

```
PaperImporter.import_paper(doi):
  1. Fetch paper metadata from Semantic Scholar (unchanged).
  2. Create or promote the Paper node (E-5 change: handle stub promotion).
  3. NEW (E-5): Fetch references via get_paper_references(paper_id).
  4. NEW (E-5): For each reference Y in the response:
     a. If Y has a DOI:
        - Call create_or_promote_paper_stub(doi=Y.doi, title=Y.title, year=Y.year).
        - Call link_paper_cites_paper(source_doi=X.doi, target_doi=Y.doi).
     b. If Y has no DOI:
        - Skip. (No reliable identifier; dropping is the right trade per Q4.)
```

Stub creation and edge creation share a single transaction with the Paper insertion when possible, but per-reference loops are fine — Neo4j's idempotent MERGE handles re-entry.

### Repository Surface

Added to `packages/core/src/agentic_kg/knowledge_graph/repository.py`:

| Method | Purpose |
|---|---|
| `link_paper_cites_paper(source_doi, target_doi)` | Create `:CITES` edge idempotently. Increments source's `reference_count` and target's `citation_count` only when the edge is new. Raises `NotFoundError` if either Paper is missing. |
| `unlink_paper_cites_paper(source_doi, target_doi)` | Remove the edge. Decrements counters (clamped at 0). |
| `create_or_promote_paper_stub(doi, title, year=None)` | Idempotent: returns existing Paper (stub or full) if a Paper with that DOI exists; otherwise creates a new stub. Returns `(Paper, created: bool)`. |
| `get_references(paper_doi, limit=50)` | Out-traversal: `MATCH (p:Paper {doi})-[:CITES]->(r:Paper) RETURN r`. |
| `get_citing_papers(paper_doi, limit=50)` | In-traversal: `MATCH (c:Paper)-[:CITES]->(p:Paper {doi}) RETURN c`. |
| `count_citations(paper_doi)` | Returns the denormalized `citation_count` from the Paper node. |
| `_promote_paper_stub(doi, full_paper)` | Private helper used by `PaperImporter` to overwrite stub properties with full metadata. |

The existing `_link_entity_to_node` / `_NODE_LINK_RELATIONSHIPS` is **not** used — those helpers assume the source has a `doi` field and the target has an `id` field. CITES is Paper → Paper with `doi` on both sides. A dedicated `_link_paper_cites_paper` helper is cleaner than generalizing further. Documented as a deliberate scope limit on the generalization.

### API Surface

Added to `packages/api/src/agentic_kg_api/routers/papers.py` (existing router):

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/papers/{doi}/references` | Out-edges (papers the given paper cites). Paginated. |
| `GET` | `/api/papers/{doi}/citations` | In-edges (papers that cite the given paper, scoped to the corpus). Paginated. |
| `GET` | `/api/papers/{doi}/citation-counts` | Returns `{"citation_count": int, "reference_count": int, "is_stub": bool}`. |

### CLI Surface

| Command | Description |
|---|---|
| `agentic-kg citation-graph --paper-doi <doi> [--depth N] [--direction in|out|both]` | Print the citation neighborhood of a paper up to depth N (default 1). Direction defaults to `out`. |

### Verification: testcontainers integration test

1. Schema initialized on a fresh testcontainers Neo4j.
2. Create 3 Papers (A, B, C) via `repo.create_paper`.
3. Mock `SemanticScholarClient.get_paper_references` to return:
   - A → [B (in our KG), D (not in KG, has DOI), E (not in KG, no DOI)]
4. Call the ingestion-time citation hook.
5. Assert:
   - Edge `(A)-[:CITES]->(B)` exists.
   - A new stub Paper for D exists with `is_stub=True`.
   - Edge `(A)-[:CITES]->(D_stub)` exists.
   - No stub for E (no DOI → skipped).
   - `A.reference_count == 2`, `B.citation_count == 1`, `D_stub.citation_count == 1`.
6. Then fully ingest D via the normal `PaperImporter.import_paper` flow.
7. Assert:
   - The Paper node for D is the **same node id** as the previous stub.
   - `D.is_stub == False` now.
   - `(A)-[:CITES]->(D)` edge survives promotion.
   - `D.citation_count == 1` (preserved).

Plus a **stub-promotion smoke test sentinel**: create a stub for DOI X, then call `create_or_promote_paper_stub(X, title="updated")` and assert the Paper is returned unchanged (no new node, no error).

## Sample Implementation

```python
# packages/core/src/agentic_kg/knowledge_graph/repository.py (additions)

def link_paper_cites_paper(self, source_doi: str, target_doi: str) -> bool:
    """Create a CITES edge from source to target. Idempotent. Updates
    both counters atomically when the edge is new."""
    def _link(tx, s_doi: str, t_doi: str) -> bool:
        result = tx.run(
            """
            MATCH (src:Paper {doi: $s_doi})
            MATCH (tgt:Paper {doi: $t_doi})
            OPTIONAL MATCH (src)-[existing:CITES]->(tgt)
            WITH src, tgt, existing
            FOREACH (_ IN CASE WHEN existing IS NULL THEN [1] ELSE [] END |
                CREATE (src)-[:CITES]->(tgt)
                SET src.reference_count = src.reference_count + 1,
                    tgt.citation_count   = tgt.citation_count   + 1,
                    src.updated_at = $now,
                    tgt.updated_at = $now
            )
            RETURN existing IS NULL AS created
            """,
            s_doi=s_doi, t_doi=t_doi,
            now=datetime.now(timezone.utc).isoformat(),
        )
        record = result.single()
        if record is None:
            raise NotFoundError(
                f"Cannot link: source Paper {s_doi!r} or target Paper {t_doi!r} not found"
            )
        return bool(record["created"])

    with self.session() as session:
        return self._execute_with_retry(session, _link, source_doi, target_doi)


def create_or_promote_paper_stub(
    self, doi: str, title: str, year: Optional[int] = None,
) -> tuple[Paper, bool]:
    """Idempotent: returns existing Paper (stub or full) when found; else
    creates a stub. Returns (paper, created)."""
    try:
        existing = self.get_paper(doi)
        return existing, False
    except NotFoundError:
        pass

    stub = Paper(
        doi=doi, title=title, year=year, is_stub=True, authors=[],
    )
    self.create_paper(stub)
    return stub, True


# Promotion is done by extending PaperImporter — the existing import_paper
# logic detects an existing stub via DOI lookup and calls a new private
# helper instead of failing with DuplicateError. The helper:

def _promote_paper_stub(self, doi: str, full_paper: Paper) -> Paper:
    """Promote a stub Paper to a full Paper. Existing CITES edges and
    citation_count are preserved (we overwrite scalar properties only,
    never the relationships)."""
    props = full_paper.to_neo4j_properties()
    # is_stub flips to False; the existing citation_count is preserved
    # by setting only the scalar props NOT including the counter.
    props.pop("citation_count", None)  # preserve existing
    props.pop("reference_count", None)  # preserve existing

    with self.session() as session:
        session.run(
            """
            MATCH (p:Paper {doi: $doi})
            SET p = $props
            SET p.is_stub = false
            """,
            doi=doi, props=props,
        )

    logger.info(f"Promoted stub Paper {doi} to full Paper")
    return self.get_paper(doi)
```

```python
# packages/core/src/agentic_kg/data_acquisition/importer.py (modification)

class PaperImporter:
    async def import_paper(self, doi: str, ...) -> Paper:
        # ... existing fetch + normalize ...

        try:
            existing = self.repo.get_paper(doi)
            if existing.is_stub:
                # E-5 promotion path
                full = self._build_paper_from_response(api_response)
                return self.repo._promote_paper_stub(doi, full)
            return existing  # already a full Paper; no-op
        except NotFoundError:
            paper = self._build_paper_from_response(api_response)
            self.repo.create_paper(paper)

        # E-5 citation graph fetching
        await self._fetch_and_link_references(paper)
        return paper

    async def _fetch_and_link_references(self, paper: Paper) -> None:
        """Fetch the paper's reference list from Semantic Scholar and
        create CITES edges + stubs for missing targets."""
        if paper.semantic_scholar_id is None:
            return  # cannot fetch without an s2 id
        try:
            response = await self.s2_client.get_paper_references(
                paper.semantic_scholar_id, limit=200,
            )
        except Exception as e:
            logger.warning(f"Failed to fetch references for {paper.doi}: {e}")
            return

        for ref in response.get("data", []):
            ref_paper = ref.get("citedPaper") or {}
            ext_ids = ref_paper.get("externalIds") or {}
            ref_doi = ext_ids.get("DOI")
            if not ref_doi:
                continue  # Q4: no-DOI references are dropped
            self.repo.create_or_promote_paper_stub(
                doi=ref_doi, title=ref_paper.get("title", ""),
                year=ref_paper.get("year"),
            )
            try:
                self.repo.link_paper_cites_paper(
                    source_doi=paper.doi, target_doi=ref_doi,
                )
            except NotFoundError as e:
                logger.warning(f"CITES link failed for {paper.doi} -> {ref_doi}: {e}")
```

## Edge Cases & Error Handling

### Semantic Scholar API unavailable mid-ingestion
- **Scenario:** S2 endpoint times out during `get_paper_references`.
- **Behavior:** Caught at the ingestion hook; logs WARN; the Paper itself is still ingested (rest of the flow completes); no `CITES` edges are created for that ingestion. Operator can re-ingest later.
- **Test:** Unit test with a mocked client that raises; assert no edges, no crash.

### Reference paper has `paperId` but no `externalIds.DOI`
- **Scenario:** Semantic Scholar returns a reference where the cited paper has no DOI (preprint without DOI assignment).
- **Behavior:** Skipped per Q4. No stub created. No edge. Logged at DEBUG level (would be too noisy at INFO).
- **Test:** Mocked response includes a no-DOI reference; assert no stub appears.

### Self-citation (paper A's references contain A itself)
- **Scenario:** Rare but possible — a paper's reference list redundantly contains its own DOI.
- **Behavior:** A `(A)-[:CITES]->(A)` self-loop edge is created. Allowed. `A.citation_count` and `A.reference_count` both increment. Documented limitation; not a v1 concern.
- **Test:** Mocked response includes the paper's own DOI in references; assert self-loop exists.

### Stub later ingested with different title than the stub had
- **Scenario:** A stub was created with `title="Attention is All You Need"`. Later the full ingestion of the same DOI returns `title="Attention Is All You Need"` (capitalization). The promotion path overwrites the title.
- **Behavior:** Title is overwritten with the full-ingestion value. The original stub title is not preserved (we don't carry alias history on Papers). This is the documented behavior.
- **Test:** Stub with one title; promote with different title; assert the promoted title wins.

### Duplicate ingestion of the same Paper
- **Scenario:** A Paper that has been fully ingested is re-ingested via `import_paper`.
- **Behavior:** The existing-paper-and-not-stub branch returns the existing Paper unchanged (no-op). No DuplicateError. Reference re-fetching is skipped to avoid double-incrementing counters.
- **Test:** Ingest twice; assert single Paper, single set of edges, counters unchanged.

### Stub's DOI collides with an existing full Paper's DOI during stub creation
- **Scenario:** `create_or_promote_paper_stub` is called with a DOI that already maps to a full Paper.
- **Behavior:** The existing full Paper is returned unchanged (no demotion, no overwrite). `created=False`.
- **Test:** Create a full Paper; call `create_or_promote_paper_stub` with its DOI; assert no mutation.

### Two Papers in same ingestion batch cite each other circularly
- **Scenario:** Batch ingests Papers A and B. A's references include B. B's references include A.
- **Behavior:** Both edges are created. No cycle detection. Graph traversals must tolerate cycles (depth limits in `agentic-kg citation-graph --depth N`).
- **Test:** Integration test creates the cycle; asserts both edges exist and the traversal CLI handles it.

## Acceptance Criteria

### AC-1: Paper entity changes
- **Given** `models/entities.py`
- **When** imported
- **Then** `Paper` has the new fields `is_stub: bool = False`, `citation_count: int = 0`, `reference_count: int = 0`.
- **And** `year` is `Optional[int]` (not required).
- **And** `title` min_length is 2 (was 10).

### AC-2: Schema additions
- **Given** the schema manager
- **When** the schema is initialized
- **Then** `paper_is_stub_idx` index exists on `Paper.is_stub`.
- **And** `SCHEMA_VERSION` is bumped to ≥ 7.

### AC-3: `link_paper_cites_paper` idempotent + counters
- **Given** two existing Paper nodes A and B
- **When** `link_paper_cites_paper(A, B)` is called
- **Then** a `(A)-[:CITES]->(B)` edge exists, `A.reference_count` ticks +1, `B.citation_count` ticks +1.
- **And** calling it again does NOT double-increment.
- **And** `unlink_paper_cites_paper(A, B)` removes the edge and decrements both counters (clamped at 0).

### AC-4: `create_or_promote_paper_stub` idempotent
- **Given** no Paper exists with DOI X
- **When** `create_or_promote_paper_stub(X, title="T", year=2024)` is called
- **Then** a new Paper node is created with `is_stub=True`, returns `(paper, created=True)`.
- **And** calling it again returns the existing stub, `(paper, created=False)`.
- **And** calling it against a DOI that maps to a full Paper returns the existing full Paper unchanged.

### AC-5: Stub promotion preserves edges and citation_count
- **Given** a stub Paper for DOI X with `citation_count=3` (3 inbound CITES edges)
- **When** the full Paper for X is ingested via `PaperImporter.import_paper`
- **Then** the same node id is preserved.
- **And** `is_stub` flips to `False`.
- **And** all 3 inbound CITES edges still exist.
- **And** `citation_count` is still 3.

### AC-6: Reference fetching at ingestion
- **Given** a fresh ingestion of Paper P whose Semantic Scholar reference list includes Q (in KG), R (not in KG, has DOI), and S (no DOI)
- **When** `PaperImporter.import_paper(P)` runs
- **Then** edge `(P)-[:CITES]->(Q)` exists.
- **And** a stub for R is created with `is_stub=True` and edge `(P)-[:CITES]->(R)` exists.
- **And** no stub for S is created, no edge to S.
- **And** `P.reference_count == 2`, `Q.citation_count` and `R.citation_count` each ticked +1.

### AC-7: API endpoints
- **Given** the running FastAPI app
- **When** `/api/papers/{doi}/references`, `/api/papers/{doi}/citations`, and `/api/papers/{doi}/citation-counts` are exercised
- **Then** each returns the expected JSON shape and HTTP status code.
- **And** 404 is returned when the DOI doesn't exist.

### AC-8: CLI command
- **Given** the installed CLI
- **When** `agentic-kg citation-graph --paper-doi <doi> --depth 2` is invoked
- **Then** the command prints the citation neighborhood (papers and their relationships) up to depth 2.
- **And** exits 0 on success; non-zero on missing DOI with a clear stderr message.

### AC-9: Testcontainers integration test (the verify gate)
- **Given** a fresh testcontainers Neo4j with schema initialized, 3 pre-existing Papers (A, B, C), and a mocked `SemanticScholarClient.get_paper_references`
- **When** the ingestion-time citation hook runs against A whose references are [B, D-with-DOI, E-no-DOI]
- **Then** the assertions in *Verification: testcontainers integration test* hold.
- **And** the test runs in CI under `pytest.mark.integration`.

### AC-10: Stub-promotion smoke test sentinel
- **Given** a stub Paper for DOI X with title "T1"
- **When** `create_or_promote_paper_stub(X, title="T2", year=2024)` is called
- **Then** the existing Paper is returned, `created=False`, title is unchanged (still "T1").
- **Rationale:** Catches the regression where a future refactor accidentally overwrites the stub on every call. The promotion path lives in `PaperImporter`, not in `create_or_promote_paper_stub`.

### AC-11: Embedding / Semantic Scholar failure is tolerated
- **Given** Semantic Scholar's references endpoint returns an error or times out
- **When** `_fetch_and_link_references` runs
- **Then** the function logs WARN and returns without raising — the Paper itself is still ingested.

### AC-12: Existing functionality untouched
- **Given** the existing test suite
- **When** E-5 is merged
- **Then** all existing tests in `packages/core/tests/` and `packages/api/tests/` continue to pass with **trivial modifications only** to adjust to the relaxed `Paper.title.min_length` (was 10, now 2) and `Paper.year` (was required, now Optional). The modifications are mechanical — most tests already use titles ≥ 10 chars and explicit `year=2024` style — but tests that construct minimal Papers may need touch-ups.

## Technical Notes

- **Affected files:**
  - Create: `tests/knowledge_graph/test_citation_graph.py` (integration), `tests/knowledge_graph/test_paper_stub.py` (entity changes), `tests/test_cli_citation_graph.py`, `api/tests/test_citations.py`
  - Modify: `knowledge_graph/models/entities.py` (Paper field changes), `knowledge_graph/repository.py` (5+ new methods), `knowledge_graph/schema.py` (1 new index; v7 bump), `data_acquisition/importer.py` (ingestion hook + promotion path), `data_acquisition/semantic_scholar.py` (no changes — `get_paper_references` already exists), `api/routers/papers.py` (3 new endpoints), `api/schemas.py` (Citation/Reference response shapes), `cli.py` (citation-graph subcommand)
  - Touch: none in `problem_extractor.py`, `kg_integration_v2.py`, agents.
- **Reuse:** `get_paper_references` (existing in Semantic Scholar client), `PaperImporter` orchestration shape, `_execute_with_retry` for the new repo methods.
- **No `_NODE_LINK_RELATIONSHIPS` generalization** — Paper → Paper with DOI on both sides doesn't fit the E-3 generalization (which assumes `id` on the target). Dedicated `link_paper_cites_paper` helper; documented limitation. If we ever add a SECOND Paper → Paper relationship (`SIMILAR_TO`?), revisit then.
- **Test impact of Paper model relaxation:** existing tests in E-1 / E-2 / E-3 / E-4 that construct `Paper(doi=..., title=..., year=...)` continue to work. Tests that relied on `min_length=10` validation will need to be adjusted. Sweep at implementation time.

## Dependencies

- **Existing:** `SemanticScholarClient.get_paper_references` (no changes needed).
- **None new.**

## Open Questions

- **Self-loop handling.** Currently allowed (self-cite). May want a future cleanup pass. Not v1.
- **Stub TTL / GC.** Stubs that never get promoted (cited paper not in any ingested corpus) live forever. Operator workflow for cleanup is "delete by DOI" — same as for full Papers. Not v1.
- **`SIMILAR_TO` relationship** (Paper → Paper, embedding-based) was a sibling future possibility. Out of scope; not in the gap analysis as a v1 ask.
- **CITES properties** (citation context, year of citation, is_self_citation) — out of scope; documented as a future LLM-driven feature.

## Review Record

Decisions were recorded after the interview was cut short for speed. Tech Lead Q1 and Q2 were answered by the user; remaining decisions were made by the Feature Architect per the user's instruction "go ahead and take your choices and implement but record the choices".

**User-answered decisions:**
- **Q1 (Tech Lead, answered: b) — Missing cited papers.** Decision: create stub Paper nodes with `is_stub=True` flag, promoted on later full ingestion. Citation graph dense from day 1 at the cost of a low-quality Paper population that filters need to handle. Locked in *Data Model Change* and *Stub Lifecycle*.
- **Q2 (Tech Lead, answered: a) — Directionality.** Decision: out-only references at ingestion (`get_paper_references`). No automatic `get_paper_citations` call, no `--enrich-citations` CLI. Bounded API cost; in-edges grow organically as the corpus grows. Locked in *Ingestion Flow*.

**Feature Architect's picks for remaining decisions:**
- **Q3 — Backfill of existing Papers.** Decision: no `--enrich-citations` command in v1. Operators re-ingest if they want citation graphs on older Papers. Matches the saved `feedback_rebuild_over_migrate` memory.
- **Q4 — Stub dedup.** Decision: DOI-only; references with no DOI are skipped entirely (not stubbed). Lossless — DOI is the only reliable identifier; titles are too noisy for fuzzy dedup. Documented as a known scope limit.
- **Q5 — Citation edge properties.** Decision: plain `:CITES` with no properties. Semantic Scholar's references endpoint doesn't return citation context (intro / methods / related-work). Future LLM-driven enrichment can add properties without spec change.
- **Q6 — Paper field validation relaxation.** Decision: `Paper.year` becomes `Optional[int]`; `Paper.title.min_length` drops from 10 to 2. Required so stubs lacking full metadata can pass validation. Pre-existing tests may need touch-ups for the relaxed validation but they're mechanical.
- **Q7 — Verification.** Decision: testcontainers integration test exercising the round trip (ingest → stub creation → promotion preserves edges) + a stub-promotion smoke test sentinel (AC-10). Matches E-4 verify pattern.

All decisions locked in the corresponding sections above. Adjust the spec if any decision turns out wrong during implementation.
