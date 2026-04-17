# Feature: Topic / Research Area Entities (E-1)

**Status:** SPECIFIED
**Date:** 2026-04-16
**Author:** Feature Architect (AI-assisted)
**Depends On:** None (builds on existing entity patterns from Sprint 09-10)
**Decoupled From:** E-8 (extraction prompt expansion — adds LLM topic mapping, separate feature)
**Backlog ID:** E-1

## Problem

Research topics are stored as flat `domain` strings on Problem, ProblemMention, and ProblemConcept nodes (e.g., `"NLP"`, `"Computer Vision"`). This makes topics invisible to graph traversal, unsearchable by vector similarity, and unable to represent hierarchy (domain → area → subtopic). A researcher cannot ask "show me all problems in NLP" via a graph query, cannot browse the research landscape by topic, and cannot discover cross-topic connections. The gap analysis (§4.1 Gap 1) identifies this as the highest-priority missing entity type — it blocks topic-based navigation, community detection (C-1), and RAG retrieval (R-1).

## Goals

- First-class `Topic` nodes in Neo4j with hierarchy via `SUBTOPIC_OF` relationships
- Seeded taxonomy (~30-50 nodes) hand-curated from OpenAlex top-level concepts, loaded from a YAML fixture
- Existing `domain` string fields migrated to `BELONGS_TO` edges and removed from all entity models
- API endpoints for topic browsing, topic-based problem discovery, and manual topic assignment
- Vector embeddings on Topic nodes for semantic topic search
- Schema version bumped to v3
- CLI commands for taxonomy import/export

## Non-Goals

- LLM-based topic extraction from papers (deferred to E-8 — extraction prompt expansion)
- Automated taxonomy growth / LLM-proposed topics (deferred to E-8 or a dedicated taxonomy management feature T-1)
- Full OpenAlex concept import (65k nodes — too broad; curate manually instead)
- Topic-based community detection (deferred to C-1, Sprint 13)
- Taxonomy management service / external datastore (deferred to T-1 — taxonomy at scale)
- Changes to the ProblemMention → ProblemConcept matching pipeline (BELONGS_TO is orthogonal to INSTANCE_OF)
- Frontend UI for topic browsing (use API + Neo4j Browser for now)

## User Stories

- As a researcher, I want to browse problems by topic hierarchy so I can find open problems in my field without keyword guessing.
- As a researcher, I want to see which topics a paper touches so I can understand its cross-disciplinary relevance.
- As a system operator, I want new topics proposed by the LLM to be reviewable so the taxonomy stays clean.
- As a developer, I want Topic to follow the same Pydantic entity pattern as other nodes so I don't learn a new abstraction.

## Design Approach

### Data Model

New entity in `packages/core/src/agentic_kg/knowledge_graph/models/entities.py`:

```python
class TopicLevel(str, Enum):
    DOMAIN = "domain"       # e.g., "Computer Science"
    AREA = "area"           # e.g., "Natural Language Processing"
    SUBTOPIC = "subtopic"   # e.g., "Machine Translation"

class Topic(BaseModel):
    id: str                           # UUID
    name: str                         # min_length=2
    description: Optional[str]        # For richer embeddings (name + description)
    level: TopicLevel                 # Hierarchy tier
    parent_id: Optional[str]          # Parent Topic ID (None for root domains)
    source: str                       # "manual", "openalex", "llm_proposed", "migrated"
    openalex_id: Optional[str]        # External ID if seeded from OpenAlex
    embedding: Optional[list[float]]  # 1536-dim (text-embedding-3-small)
    problem_count: int                # Denormalized count of linked problems
    paper_count: int                  # Denormalized count of linked papers
    created_at: datetime
    updated_at: datetime
```

### New Relationships

| Relationship | From → To | Purpose |
|---|---|---|
| `BELONGS_TO` | Problem/ProblemMention/ProblemConcept → Topic | Topic assignment (replaces `domain` string) |
| `RESEARCHES` | Paper → Topic | Paper covers this topic |
| `SUBTOPIC_OF` | Topic → Topic | Hierarchy (child → parent) |

### Schema Changes (v2 → v3)

- Constraint: `topic_id_unique` on `Topic.id`
- Indexes: `topic_name_idx`, `topic_level_idx`, `topic_source_idx`
- Vector index: `topic_embedding_idx` (1536 dims, cosine)
- Remove: `problem_domain_idx` (no longer needed after `domain` field removal)

### Seed Taxonomy

Hand-curated ~30-50 nodes organized as a three-level tree, focused on the project's paper corpus (graph retrieval, knowledge graphs, NLP, ML). Structure seeded from OpenAlex top-level CS concepts, pruned to relevant areas. Stored as a JSON/YAML fixture file in `packages/core/src/agentic_kg/knowledge_graph/data/seed_taxonomy.yml`.

Example (abbreviated):
```yaml
- name: Computer Science
  level: domain
  children:
    - name: Natural Language Processing
      level: area
      children:
        - name: Information Extraction
          level: subtopic
        - name: Question Answering
          level: subtopic
        - name: Machine Translation
          level: subtopic
    - name: Information Retrieval
      level: area
      children:
        - name: Graph-Based Retrieval
          level: subtopic
        - name: Dense Retrieval
          level: subtopic
        - name: Retrieval-Augmented Generation
          level: subtopic
    - name: Knowledge Representation
      level: area
      children:
        - name: Knowledge Graphs
          level: subtopic
        - name: Ontology Engineering
          level: subtopic
    - name: Machine Learning
      level: area
      children:
        - name: Deep Learning
          level: subtopic
        - name: Transfer Learning
          level: subtopic
        - name: Graph Neural Networks
          level: subtopic
```

### Topic Assignment (E-1 scope: manual only)

E-1 provides the infrastructure for topic assignment but does NOT add automatic LLM-based assignment during ingestion. Topics are assigned via:

1. **Migration**: existing `domain` strings converted to `BELONGS_TO` edges (one-shot)
2. **API**: `POST /api/topics/{id}/assign` manually links a problem or paper to a topic
3. **CLI**: `cli.py assign-topic --entity-id <id> --topic-id <id>`
4. **Neo4j Browser**: direct Cypher for bulk manual assignment

LLM-based automatic topic assignment during ingestion is deferred to E-8.

### Migration

One-shot Cypher script to convert existing `domain` strings:

```cypher
// Create Topic nodes from distinct domain values
MATCH (n) WHERE n.domain IS NOT NULL
  AND (n:Problem OR n:ProblemMention OR n:ProblemConcept)
WITH DISTINCT n.domain AS domain_name
MERGE (t:Topic {name: domain_name})
  ON CREATE SET t.id = randomUUID(), t.level = 'area',
    t.source = 'migrated', t.created_at = datetime()

// Create BELONGS_TO edges and remove domain property
WITH t, domain_name
MATCH (n) WHERE n.domain = domain_name
MERGE (n)-[:BELONGS_TO]->(t)
REMOVE n.domain
```

After migration, manually map migrated topics to the seed taxonomy via `SUBTOPIC_OF` edges (small number — likely <10 distinct domain values on 282 nodes).

### API Endpoints

Added to `routers/topics.py`:

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/topics` | List all topics, optionally filtered by level; supports `?tree=true` for hierarchical response |
| `GET` | `/api/topics/{id}` | Topic detail with children, parent, and counts |
| `GET` | `/api/topics/{id}/problems` | Problems belonging to this topic (including subtopic descendants) |
| `GET` | `/api/topics/search?q=...` | Vector similarity search over topic embeddings |
| `POST` | `/api/topics/{id}/assign` | Assign a problem or paper to a topic (manual topic assignment) |

### CLI Commands

Added to `cli.py`:

| Command | Description |
|---|---|
| `load-taxonomy [--file path]` | Load seed taxonomy from YAML into Neo4j (idempotent MERGE) |
| `export-taxonomy [--file path]` | Export current taxonomy from Neo4j to YAML (snapshot for version control) |
| `assign-topic --entity-id <id> --topic-id <id>` | Manually link a problem/paper to a topic |

## Sample Implementation

```python
# === Topic model (follows existing entity pattern) ===

class Topic(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(..., min_length=2)
    description: Optional[str] = Field(default=None)
    level: TopicLevel = Field(...)
    parent_id: Optional[str] = Field(default=None)
    source: str = Field(default="manual")  # "manual", "openalex", "migrated"
    openalex_id: Optional[str] = Field(default=None)
    embedding: Optional[list[float]] = Field(default=None)
    problem_count: int = Field(default=0, ge=0)
    paper_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    def to_neo4j_properties(self) -> dict:
        data = self.model_dump(exclude={"embedding"})
        data["level"] = self.level.value
        data["created_at"] = self.created_at.isoformat()
        data["updated_at"] = self.updated_at.isoformat()
        return data

# === Seed taxonomy loader (idempotent via MERGE) ===

def load_taxonomy(repo, taxonomy: list[dict], parent_id=None):
    for entry in taxonomy:
        topic = Topic(name=entry["name"], level=TopicLevel(entry["level"]),
                      parent_id=parent_id, source="manual")
        topic.embedding = embed(f"{topic.name}: {entry.get('description', '')}")
        repo.merge_topic(topic)  # MERGE on name+level+parent_id
        if parent_id:
            repo.link_topic_parent(topic.id, parent_id)  # SUBTOPIC_OF
        load_taxonomy(repo, entry.get("children", []), parent_id=topic.id)

# === Taxonomy export (Neo4j → YAML snapshot) ===

def export_taxonomy(repo) -> list[dict]:
    roots = repo.get_topics_by_level(TopicLevel.DOMAIN)
    def build_tree(topic_id):
        topic = repo.get_topic(topic_id)
        children = repo.get_topic_children(topic_id)
        return {
            "name": topic.name, "level": topic.level.value,
            "description": topic.description, "source": topic.source,
            "children": [build_tree(c.id) for c in children]
        }
    return [build_tree(r.id) for r in roots]

# === Migration: domain strings → BELONGS_TO edges (with dedup) ===

MIGRATION_STEP_1 = """
// Create Topic nodes from distinct domain values
MATCH (n) WHERE n.domain IS NOT NULL
  AND (n:Problem OR n:ProblemMention OR n:ProblemConcept)
WITH DISTINCT n.domain AS domain_name
MERGE (t:Topic {name: domain_name})
  ON CREATE SET t.id = randomUUID(), t.level = 'area',
    t.source = 'migrated', t.created_at = datetime()
WITH t, domain_name
MATCH (n) WHERE n.domain = domain_name
MERGE (n)-[:BELONGS_TO]->(t)
REMOVE n.domain
"""

# Step 2: embed migrated topics, find duplicates via cosine similarity,
# merge pairs above 0.9 threshold (keeps longer/more descriptive name)

# === Denormalized count reconciliation (sanity check) ===

RECONCILE_COUNTS = """
MATCH (t:Topic)
OPTIONAL MATCH (t)<-[:BELONGS_TO]-(p:ProblemConcept)
OPTIONAL MATCH (t)<-[:RESEARCHES]-(paper:Paper)
WITH t, count(DISTINCT p) AS pc, count(DISTINCT paper) AS pac
WHERE t.problem_count <> pc OR t.paper_count <> pac
SET t.problem_count = pc, t.paper_count = pac
RETURN t.name, pc, pac
"""
```

## Edge Cases & Error Handling

### Migration encounters unknown domain strings
- **Scenario**: Existing `domain` values like "AI" or "artificial intelligence" don't match any seed taxonomy topic
- **Behavior**: Migration creates a Topic node with `source='migrated'`, `level='area'`. Operator manually maps it to the taxonomy post-migration.
- **Test**: Run migration on test data with varied domain strings, verify all get Topic nodes

### Duplicate topic names at different levels
- **Scenario**: "Information Retrieval" exists as both an area and a subtopic
- **Behavior**: Uniqueness is on `id`, not `name`. Name + level + parent_id together identify a topic. LLM prompt includes full path ("Computer Science > Information Retrieval") to disambiguate.
- **Test**: Create two topics with same name, different parents; verify both exist and are distinct

## Acceptance Criteria

### AC-1: Topic Pydantic model
- **Given** the `Topic` model is added to `entities.py`
- **When** a Topic is created with valid fields
- **Then** it validates, generates a UUID, and `to_neo4j_properties()` returns a flat dict with serialized enums and datetimes

### AC-2: Neo4j schema v3
- **Given** `SchemaManager` runs with `SCHEMA_VERSION = 3`
- **When** schema initialization completes
- **Then** `Topic` uniqueness constraint, property indexes (name, level, source), and vector index (1536-dim cosine) exist; `problem_domain_idx` is dropped

### AC-3: Seed taxonomy loaded
- **Given** the seed taxonomy YAML fixture (~30-50 nodes)
- **When** the taxonomy loader runs against a clean database
- **Then** Topic nodes exist at all three levels with correct `SUBTOPIC_OF` edges forming a tree; every Topic has an embedding

### AC-4: Manual topic assignment via API
- **Given** a Problem/Paper and a Topic exist in Neo4j
- **When** `POST /api/topics/{topic_id}/assign` is called with the entity ID
- **Then** a `BELONGS_TO` (or `RESEARCHES`) edge is created; the topic's denormalized count is incremented

### AC-5: CLI taxonomy import/export
- **Given** a taxonomy exists in Neo4j with seed + migrated topics
- **When** `export-taxonomy` is run
- **Then** a YAML file is produced that captures the full tree including hierarchy and source provenance
- **And** running `load-taxonomy` with that exported file against a clean database reproduces the same topology (idempotent)

### AC-6: Domain field migration
- **Given** existing Problem/ProblemMention/ProblemConcept nodes with `domain` string fields
- **When** the migration Cypher runs
- **Then** `domain` property is removed from all nodes; each former `domain` value has a corresponding Topic node; `BELONGS_TO` edges connect the original nodes to their Topic

### AC-7: Domain field removed from models
- **Given** the updated `entities.py`
- **When** inspected
- **Then** `domain` field no longer exists on Problem, ProblemMention, or ProblemConcept; no code references `n.domain` except the migration script

### AC-8: Topic CRUD in repository
- **Given** `Neo4jRepository`
- **When** `create_topic`, `get_topic`, `search_topics_by_embedding`, `link_topic_parent`, `get_topic_tree` are called
- **Then** they correctly create, retrieve, search, link, and return hierarchical topic data

### AC-9: API endpoints
- **Given** the FastAPI app is running
- **When** `GET /api/topics?tree=true` is called
- **Then** a hierarchical JSON tree of all topics is returned with counts
- **And** `GET /api/topics/{id}/problems` returns problems belonging to that topic and its subtopics

### AC-10: Staging deployed and verified
- **Given** all code is deployed to staging
- **When** the operator reviews the graph in Neo4j Browser
- **Then** Topic nodes are visible in the graph, connected to existing problems and papers via `BELONGS_TO` and `RESEARCHES` edges; the old `domain` property is gone

### AC-11: Tests passing
- **Given** the full test suite
- **When** `pytest` runs
- **Then** all existing tests pass (no regressions from `domain` removal) and new tests cover: Topic model validation, schema migration, taxonomy loading, CRUD, manual assignment, API endpoints, CLI commands, count reconciliation

### AC-12: Denormalized count reconciliation
- **Given** Topic nodes with `problem_count` and `paper_count` fields
- **When** the reconciliation sanity check runs
- **Then** any drift between denormalized counts and actual edge counts is corrected and logged

## Technical Notes

- **Affected files**: `entities.py` (new model + remove `domain`), `enums.py` (add `TopicLevel`), `schema.py` (v3), `repository.py` (Topic CRUD), `cli.py` (load/export/assign commands), `routers/topics.py` (new), `dependencies.py` (DI wiring), all tests referencing `domain`
- **NOT affected**: `kg_integration_v2.py` — LLM topic mapping deferred to E-8
- **Pattern to follow**: ProblemMention/ProblemConcept entity pattern (Pydantic model → `to_neo4j_properties()` → repository CRUD → schema constraints/indexes)
- **Embedding strategy**: embed `"{name}: {description}"` using `text-embedding-3-small` (same as ProblemMention/ProblemConcept)
- **Taxonomy fixture**: `packages/core/src/agentic_kg/knowledge_graph/data/seed_taxonomy.yml`
- **Migration script**: `packages/core/src/agentic_kg/knowledge_graph/migrations/v3_topic_migration.py`
- **Count maintenance**: transactional delta on write + periodic reconciliation via sanity check query

## Dependencies

- OpenAI API (embeddings for Topic nodes) — already available
- No new external dependencies
- **Future dependency (E-8)**: `instructor` for structured LLM topic mapping output

## New Backlog Item: T-1 (Taxonomy Management at Scale)

E-1 uses YAML seed + Neo4j for taxonomy storage. If the taxonomy grows beyond ~200 nodes (likely once E-8 adds LLM proposals), a dedicated taxonomy management feature is needed:
- Versioned taxonomy with branching/merge (like a schema registry)
- Import/export across environments and projects
- Conflict resolution for overlapping proposals
- Possibly shared taxonomy service for Denario ecosystem
- Storage: dedicated datastore, separate graph, or taxonomy service TBD

This is **not in scope for E-1** — flag for backlog.

## Open Questions

- Exact taxonomy content (defer to implementation — start with ~30 CS-focused nodes, curated from OpenAlex top-level structure)
- Migration dedup: exact similarity threshold for merging domain strings that mean the same thing (recommend 0.9 cosine as starting point)
- Should exported taxonomy YAML include embeddings or regenerate on import? (Recommend: regenerate — keeps YAML human-readable)
