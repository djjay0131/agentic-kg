# Feature: ResearchConcept Entities (E-2)

**Status:** SPECIFIED
**Date:** 2026-04-17
**Author:** Feature Architect (AI-assisted)
**Backlog ID:** E-2
**Depends On:** None (follows same entity patterns as E-1; benefits from E-1 Topic nodes for BELONGS_TO but not blocked)
**Decoupled From:** E-7 (cross-entity normalization), E-8 (extraction prompt expansion — adds LLM concept extraction)

## Problem

The knowledge graph only models research **problems** as structured entities. Generic research concepts — "attention mechanism", "transfer learning", "knowledge distillation", "graph neural networks" — are the intellectual building blocks that connect problems, papers, and topics across the research landscape. Without them, a researcher cannot ask "which problems involve attention mechanisms?", cannot discover that two seemingly unrelated problems share a common technique, and cannot trace how a concept like "retrieval-augmented generation" is applied across different research areas. The gap analysis (§4.1 Gap 2) identifies this as the second-highest priority missing entity type, with ~520 concept nodes extracted in the reference paper's results.

## Goals

- First-class `ResearchConcept` nodes in Neo4j with name, description, aliases, and vector embedding
- Relationships: `INVOLVES_CONCEPT` (ProblemConcept → ResearchConcept), `DISCUSSES` (Paper → ResearchConcept), `BELONGS_TO` (ResearchConcept → Topic, if E-1 in place)
- Embedding-based dedup on create: check for existing concepts above cosine threshold; merge if found (reuses concept_matcher patterns)
- Repository CRUD for ResearchConcepts (create, get, search, link)
- API endpoints for concept browsing, search, manual creation, and linking
- CLI command for manual concept creation and linking
- Denormalized counts with transactional delta + periodic reconciliation (same pattern as E-1)
- Shared `BaseGraphEntity` model and `EntityService` abstraction extracted for reuse across E-1, E-2, and future entity types (E-3, E-4)
- Dedup threshold calibration study before finalizing the 0.90 default
- Schema version bumped (v3 or v4 depending on E-1 ordering)

## Non-Goals

- LLM-based concept extraction from papers (deferred to E-8)
- Full mention-to-concept dual-entity architecture for concepts (deferred to E-7)
- `concept_type` enum or categorization (let graph structure provide typing; add later if data shows clear clusters)
- Frontend UI for concept browsing (API + Neo4j Browser for now)
- Community detection on concepts (deferred to C-1)

## User Stories

- As a researcher, I want to see which research concepts are involved in a problem so I can understand the techniques and theories at play.
- As a researcher, I want to search for a concept like "attention mechanism" and find all problems and papers that involve it.
- As a researcher, I want to discover that two problems in different domains share a common concept, revealing cross-disciplinary connections.
- As a developer, I want ResearchConcept to follow the same Pydantic entity pattern as Topic and ProblemConcept so I don't learn a new abstraction.

## Design Approach

### Data Model

New entity in `packages/core/src/agentic_kg/knowledge_graph/models/entities.py`:

```python
class ResearchConcept(BaseModel):
    id: str                            # UUID
    name: str                          # min_length=2, canonical name
    description: Optional[str]         # For richer embeddings (name + description)
    aliases: list[str]                 # Alternative names (e.g., ["self-attention", "scaled dot-product attention"])
    embedding: Optional[list[float]]   # 1536-dim (text-embedding-3-small)
    mention_count: int                 # Denormalized: count of INVOLVES_CONCEPT edges
    paper_count: int                   # Denormalized: count of DISCUSSES edges
    created_at: datetime
    updated_at: datetime
```

### New Relationships

| Relationship | From → To | Purpose |
|---|---|---|
| `INVOLVES_CONCEPT` | ProblemConcept → ResearchConcept | Problem uses/relates to this concept |
| `DISCUSSES` | Paper → ResearchConcept | Paper discusses this concept |
| `BELONGS_TO` | ResearchConcept → Topic | Concept belongs to a topic (requires E-1) |

**Deferred:** Typed concept-to-concept relationships (`ENABLES`, `BUILDS_ON`, `ALTERNATIVE_TO`, `GENERALIZES`, `PREREQUISITE_OF`) — these emerge from LLM extraction (E-8) or community detection (C-1), not manual creation. Generic `RELATED_TO` deliberately omitted — the relationship name should convey the semantics.

### Schema Changes

- Constraint: `research_concept_id_unique` on `ResearchConcept.id`
- Indexes: `research_concept_name_idx`
- Vector index: `research_concept_embedding_idx` (1536 dims, cosine)

### Embedding-Based Dedup

On every concept creation attempt:

1. Embed `"{name}: {description}"` (or just `name` if no description)
2. Vector search existing ResearchConcept nodes, top-k=5
3. If best match ≥ 0.90 cosine similarity → **merge**: add the new name to `aliases`, update description if richer, return existing concept
4. If best match < 0.90 → **create new** ResearchConcept node

This reuses the same vector search infrastructure as `concept_matcher.py` and follows the E-1 migration dedup pattern. The 0.90 threshold is configurable.

### API Endpoints

Added to `routers/concepts.py`:

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/concepts` | List concepts, paginated, optional name filter |
| `GET` | `/api/concepts/{id}` | Concept detail with linked problems and papers counts |
| `GET` | `/api/concepts/{id}/problems` | ProblemConcepts linked via INVOLVES_CONCEPT |
| `GET` | `/api/concepts/{id}/papers` | Papers linked via DISCUSSES |
| `GET` | `/api/concepts/search?q=...` | Vector similarity search over concept embeddings |
| `POST` | `/api/concepts` | Create concept (with dedup check) |
| `POST` | `/api/concepts/{id}/link-problem` | Link a ProblemConcept to this ResearchConcept |
| `POST` | `/api/concepts/{id}/link-paper` | Link a Paper to this ResearchConcept |

### CLI Commands

| Command | Description |
|---|---|
| `create-concept --name <name> [--description <desc>] [--aliases a,b,c]` | Create with dedup check |
| `link-concept --concept-id <id> --entity-id <id> --rel-type <INVOLVES_CONCEPT\|DISCUSSES>` | Link concept to problem or paper |

### Shared Abstractions (extracted during E-2 implementation)

`BaseGraphEntity` — shared Pydantic base model providing: `id` (UUID), `name`, `description`, `embedding`, `created_at`, `updated_at`, `to_neo4j_properties()`.

`EntityService` — generic service class providing: create-with-dedup, CRUD, embedding generation, count reconciliation. Parameterized by entity type, label, and relationship types. Topic and ResearchConcept (and later Model, Method) instantiate this service with their specific config.

### Denormalized Counts

Same pattern as E-1 Topic:
- **On write**: increment/decrement in the same transaction as edge creation/deletion
- **Reconciliation**: periodic sanity check query corrects drift

## Sample Implementation

```python
# === ResearchConcept model ===

class ResearchConcept(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(..., min_length=2)
    description: Optional[str] = Field(default=None)
    aliases: list[str] = Field(default_factory=list)
    embedding: Optional[list[float]] = Field(default=None)
    mention_count: int = Field(default=0, ge=0)
    paper_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    def to_neo4j_properties(self) -> dict:
        import json
        data = self.model_dump(exclude={"embedding"})
        data["aliases"] = json.dumps(self.aliases)
        data["created_at"] = self.created_at.isoformat()
        data["updated_at"] = self.updated_at.isoformat()
        return data

# === Dedup on create ===

DEDUP_THRESHOLD = 0.90

async def create_or_merge_concept(repo, name, description=None, aliases=None):
    embed_text = f"{name}: {description}" if description else name
    embedding = await embed(embed_text)

    candidates = repo.search_research_concepts_by_embedding(
        embedding, top_k=5, min_score=DEDUP_THRESHOLD
    )
    if candidates:
        best = candidates[0]
        new_aliases = set(best.aliases + (aliases or []) + [name])
        new_aliases.discard(best.name)
        repo.update_research_concept(best.id, aliases=list(new_aliases))
        return best

    concept = ResearchConcept(
        name=name, description=description,
        aliases=aliases or [], embedding=embedding
    )
    repo.create_research_concept(concept)
    return concept

# === Repository CRUD ===

def create_research_concept(self, concept: ResearchConcept):
    props = concept.to_neo4j_properties()
    self._run("""
        CREATE (c:ResearchConcept $props)
        WITH c CALL db.create.setNodeVectorProperty(c, 'embedding', $emb)
    """, props=props, emb=concept.embedding)

def link_problem_to_concept(self, problem_concept_id, research_concept_id):
    self._run("""
        MATCH (pc:ProblemConcept {id: $pc_id})
        MATCH (rc:ResearchConcept {id: $rc_id})
        MERGE (pc)-[:INVOLVES_CONCEPT]->(rc)
        WITH rc
        SET rc.mention_count = rc.mention_count + 1
    """, pc_id=problem_concept_id, rc_id=research_concept_id)

def link_paper_to_concept(self, paper_doi, research_concept_id):
    self._run("""
        MATCH (p:Paper {doi: $doi})
        MATCH (rc:ResearchConcept {id: $rc_id})
        MERGE (p)-[:DISCUSSES]->(rc)
        WITH rc
        SET rc.paper_count = rc.paper_count + 1
    """, doi=paper_doi, rc_id=research_concept_id)

# === Count reconciliation ===

RECONCILE = """
MATCH (rc:ResearchConcept)
OPTIONAL MATCH (rc)<-[:INVOLVES_CONCEPT]-(pc:ProblemConcept)
OPTIONAL MATCH (rc)<-[:DISCUSSES]-(p:Paper)
WITH rc, count(DISTINCT pc) AS mc, count(DISTINCT p) AS pac
WHERE rc.mention_count <> mc OR rc.paper_count <> pac
SET rc.mention_count = mc, rc.paper_count = pac
"""
```

## Edge Cases & Error Handling

### Dedup merges concepts that are actually distinct
- **Scenario**: "Attention" (cognitive science concept) and "Attention mechanism" (ML concept) score above 0.90 and get merged
- **Behavior**: The merged concept has both names in `aliases`. Correctable: split via API (create new concept, reassign edges). Threshold is configurable.
- **Test**: Create two semantically close but distinct concepts; verify they merge. Create two clearly different concepts; verify they don't.

### Alias list grows very large
- **Scenario**: A popular concept like "deep learning" gets aliases from many papers (50+ variants)
- **Behavior**: Aliases stored as JSON string in Neo4j. No functional limit, but display should truncate. Periodic dedup of the alias list itself (remove near-duplicates within aliases).
- **Test**: Create concept with 100 aliases; verify storage and retrieval work.

### Concept linked to nonexistent ProblemConcept or Paper
- **Scenario**: API call with an entity ID that doesn't exist
- **Behavior**: Return 404 with a clear error message. No dangling edges created.
- **Test**: Call link endpoint with fake ID, verify 404 response.

### Concurrent dedup race condition
- **Scenario**: Two ingestion processes try to create "attention mechanism" simultaneously; both pass the dedup check before either writes
- **Behavior**: Two nodes created. Next reconciliation or dedup pass catches the duplicate. Not critical — merge is idempotent.
- **Test**: Difficult to test deterministically; document as a known edge case resolved by periodic reconciliation.

## Acceptance Criteria

### AC-1: ResearchConcept Pydantic model
- **Given** the `ResearchConcept` model is added to `entities.py`
- **When** a concept is created with valid fields
- **Then** it validates, generates a UUID, serializes aliases to JSON, and `to_neo4j_properties()` returns a flat dict

### AC-2: Neo4j schema updated
- **Given** `SchemaManager` runs with the new schema version
- **When** schema initialization completes
- **Then** `ResearchConcept` uniqueness constraint, name index, and vector index (1536-dim cosine) exist

### AC-3: Embedding-based dedup on create
- **Given** a ResearchConcept "attention mechanism" exists with embedding
- **When** `create_or_merge_concept("self-attention mechanism")` is called
- **Then** if cosine similarity ≥ 0.90, the existing concept is returned with "self-attention mechanism" added to aliases; no new node is created
- **And** if cosine similarity < 0.90, a new ResearchConcept node is created

### AC-4: Repository CRUD
- **Given** `Neo4jRepository`
- **When** `create_research_concept`, `get_research_concept`, `search_research_concepts_by_embedding`, `link_problem_to_concept`, `link_paper_to_concept` are called
- **Then** they correctly create, retrieve, search, and link ResearchConcept data with appropriate edges

### AC-5: INVOLVES_CONCEPT relationship
- **Given** a ProblemConcept and a ResearchConcept exist
- **When** they are linked via `INVOLVES_CONCEPT`
- **Then** `GET /api/concepts/{id}/problems` returns the ProblemConcept and `GET /api/problems/{id}` (if endpoint exists) reflects the concept link

### AC-6: DISCUSSES relationship
- **Given** a Paper and a ResearchConcept exist
- **When** they are linked via `DISCUSSES`
- **Then** `GET /api/concepts/{id}/papers` returns the Paper

### AC-7: Shared BaseGraphEntity and EntityService
- **Given** Topic (E-1) and ResearchConcept (E-2) models exist
- **When** inspected
- **Then** both inherit from `BaseGraphEntity` and use `EntityService` for create-with-dedup, CRUD, and count reconciliation — no duplicated boilerplate

### AC-8: API endpoints
- **Given** the FastAPI app is running
- **When** each endpoint in `routers/concepts.py` is called
- **Then** it returns correct responses: list, detail, search, create (with dedup), link-problem, link-paper, relate

### AC-9: Vector search
- **Given** multiple ResearchConcepts exist with embeddings
- **When** `GET /api/concepts/search?q=transformer architecture` is called
- **Then** semantically relevant concepts are returned sorted by similarity score

### AC-10: Denormalized count reconciliation
- **Given** ResearchConcept nodes with `mention_count` and `paper_count`
- **When** the reconciliation sanity check runs
- **Then** any drift is corrected and logged

### AC-11: Tests passing
- **Given** the full test suite
- **When** `pytest` runs
- **Then** all existing tests pass and new tests cover: model validation, schema, CRUD, dedup logic, API endpoints, CLI commands, count reconciliation

### AC-12: Dedup threshold calibration
- **Given** ~20-30 concept pairs with known same/different labels
- **When** cosine similarity is computed for each pair
- **Then** the threshold that maximizes separation is documented; the threshold is configurable via environment variable or config module

### AC-13: Staging deployed and verified
- **Given** all code is deployed to staging
- **When** the operator reviews the graph in Neo4j Browser
- **Then** ResearchConcept nodes are visible, linked to problems and papers via correct relationship types

## Technical Notes

- **Affected files**: `entities.py` (new model), `schema.py` (new constraint + indexes + vector index), `repository.py` (ResearchConcept CRUD), `cli.py` (create-concept, link-concept commands), `routers/concepts.py` (new), `dependencies.py` (DI wiring)
- **NOT affected**: `kg_integration_v2.py` — LLM concept extraction deferred to E-8
- **Pattern to follow**: Topic entity pattern from E-1 (Pydantic model → `to_neo4j_properties()` → repository CRUD → schema constraints/indexes → API router → CLI)
- **Embedding strategy**: embed `"{name}: {description}"` using `text-embedding-3-small`
- **Dedup threshold**: 0.90 cosine similarity (configurable, same as E-1 migration dedup)
- **Schema version**: increment from whatever E-1 leaves it at (v3 → v4, or v3 if E-1 hasn't shipped yet)

## Dependencies

- OpenAI API (embeddings) — already available
- E-1 (Topic) — optional; `BELONGS_TO` edges to Topics are a bonus if E-1 is in place, not a blocker
- No new external dependencies

## Open Questions

- Should dedup threshold be shared with E-1 or configurable per entity type? (Recommend: shared constant in a config module, overridable per entity)
- How should aliases be indexed for search? Options: full-text index on a denormalized `alias_text` string, or Neo4j full-text index on `name` only with alias expansion at query time. Defer to implementation.
- What typed concept-to-concept relationships will E-8/C-1 introduce? (Candidates: `ENABLES`, `BUILDS_ON`, `ALTERNATIVE_TO`, `GENERALIZES`, `PREREQUISITE_OF` — defer exact set to those specs)
- Should `BaseGraphEntity` and `EntityService` be extracted as part of E-2 implementation, or as a prerequisite refactor? (Recommend: extract during E-2, refactor E-1 Topic to use it in the same PR)
