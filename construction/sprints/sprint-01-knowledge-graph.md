# Sprint 01: Knowledge Graph Foundation

**Sprint Goal:** Implement the core Knowledge Representation Layer with Neo4j

**Start Date:** 2025-12-22
**Status:** Not Started

**Prerequisites:** Sprint 00 complete (GCP deployment working)

---

## Tasks

### Task 1: Neo4j Setup
- [ ] Add Neo4j to docker-compose for local development
- [ ] Create Neo4j database initialization script
- [ ] Configure connection settings in Denario config
- [ ] Test connection from Python

**Acceptance Criteria:**
- Neo4j running in Docker
- Can connect and run basic Cypher queries from Python

---

### Task 2: Pydantic Models
- [ ] Create `denario/knowledge_graph/models.py`
- [ ] Implement Problem model with all attributes
- [ ] Implement Paper model
- [ ] Implement relation models (Extends, Contradicts, etc.)
- [ ] Add JSON serialization for Neo4j storage

**Acceptance Criteria:**
- All models defined with proper validation
- Can serialize/deserialize to JSON

---

### Task 3: Neo4j Repository Layer
- [ ] Create `denario/knowledge_graph/repository.py`
- [ ] Implement connection manager with retry logic
- [ ] Implement `create_problem()` method
- [ ] Implement `get_problem()` method
- [ ] Implement `update_problem()` method
- [ ] Implement `delete_problem()` method
- [ ] Implement `list_problems()` with filtering

**Acceptance Criteria:**
- Full CRUD operations working
- Proper error handling

---

### Task 4: Schema Initialization
- [ ] Create `denario/knowledge_graph/schema.py`
- [ ] Define constraint creation queries
- [ ] Define index creation queries
- [ ] Create vector index for embeddings
- [ ] Add idempotent schema migration

**Acceptance Criteria:**
- Running init script creates all necessary indexes
- Can run multiple times without errors

---

### Task 5: Embedding Integration
- [ ] Create `denario/knowledge_graph/embeddings.py`
- [ ] Integrate OpenAI embeddings (text-embedding-3-small)
- [ ] Implement `generate_problem_embedding()` function
- [ ] Add embedding generation on problem creation
- [ ] Add batch embedding for bulk imports

**Acceptance Criteria:**
- Problems automatically get embeddings on creation
- Embeddings stored in Neo4j vector index

---

### Task 6: Hybrid Search
- [ ] Implement `semantic_search()` - vector similarity only
- [ ] Implement `structured_search()` - Cypher filters only
- [ ] Implement `hybrid_search()` - combined approach
- [ ] Add relevance score normalization

**Acceptance Criteria:**
- Can search by semantic similarity
- Can filter by domain, status, dataset availability
- Combined search returns ranked results

---

### Task 7: Relation Operations
- [ ] Implement `create_relation()` method
- [ ] Implement `get_related_problems()` method
- [ ] Implement `infer_relations()` placeholder
- [ ] Add relation confidence tracking

**Acceptance Criteria:**
- Can create extends/contradicts/depends-on relations
- Can traverse relations from a problem

---

### Task 8: Testing
- [ ] Create test fixtures with sample problems
- [ ] Write unit tests for CRUD operations
- [ ] Write integration tests for search
- [ ] Add test coverage for edge cases

**Acceptance Criteria:**
- >80% test coverage for knowledge_graph module
- All tests pass in CI

---

### Task 9: Sample Data
- [ ] Create `scripts/load_sample_problems.py`
- [ ] Define 10-20 sample research problems
- [ ] Include varied domains and relations
- [ ] Document sample data for testing

**Acceptance Criteria:**
- Can load sample data into fresh Neo4j instance
- Sample data covers all model features

---

### Task 10: Documentation
- [ ] Document graph schema in README
- [ ] Add API docstrings
- [ ] Create query cookbook with examples
- [ ] Update techContext.md with Neo4j details

**Acceptance Criteria:**
- New developers can understand the graph schema
- Common queries documented with examples

---

## Architecture Decisions

- **ADR-010**: Neo4j for Graph Database
- **ADR-003**: Problems as First-Class Entities (existing)
- **ADR-005**: Hybrid Symbolic-Semantic Retrieval (existing)

---

## Dependencies

- Neo4j 5.x (Community Edition for dev, Aura for prod)
- `neo4j` Python driver
- `pydantic` for data models
- `openai` for embeddings

---

## File Structure

```
denario/
├── knowledge_graph/
│   ├── __init__.py
│   ├── models.py          # Pydantic models
│   ├── repository.py      # Neo4j CRUD operations
│   ├── schema.py          # Database schema management
│   ├── embeddings.py      # Vector embedding generation
│   └── search.py          # Hybrid search implementation
├── tests/
│   └── knowledge_graph/
│       ├── test_models.py
│       ├── test_repository.py
│       └── test_search.py
└── scripts/
    └── load_sample_problems.py
```

---

## Risks

| Risk | Mitigation | Status |
|------|------------|--------|
| Neo4j learning curve | Use established patterns, document queries | Open |
| Embedding costs | Use smaller model, batch requests | Open |
| Schema evolution | Version schemas, migration scripts | Open |

---

## Notes

- Design document: [phase-1-knowledge-graph.md](../design/phase-1-knowledge-graph.md)
- Reference paper: [files/Agentic_Knowledge_Graphs_for_Research_Progression.pdf](../../files/)
