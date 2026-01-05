# Sprint 01: Knowledge Graph Foundation

**Sprint Goal:** Implement the core Knowledge Representation Layer with Neo4j

**Start Date:** 2025-12-22
**Status:** Not Started

**Prerequisites:** Sprint 00 complete (GCP deployment working)

**Related Components:** C2 (Neo4j Database), C3 (Problem Schema), C4 (KG Repository)

**Requirements Document:** [knowledge-graph-requirements.md](../requirements/knowledge-graph-requirements.md)

---

## Tasks

### Task 1: Project Structure & Configuration
- [ ] Create `agentic_kg/` package directory structure
- [ ] Create `agentic_kg/config.py` for environment-based settings
- [ ] Add Neo4j connection configuration (local/Aura)
- [ ] Add OpenAI API key configuration for embeddings
- [ ] Create `.env.example` with required environment variables

**Acceptance Criteria:**
- Clean package structure following Python conventions
- Configuration works for local development and production
- Environment variables documented

---

### Task 2: Neo4j Setup
- [ ] Add Neo4j to docker-compose for local development
- [ ] Create Neo4j database initialization script
- [ ] Configure connection settings via config module
- [ ] Test connection from Python
- [ ] Document Neo4j Aura setup for production

**Acceptance Criteria:**
- Neo4j running in Docker
- Can connect and run basic Cypher queries from Python
- Production deployment path documented

---

### Task 3: Pydantic Models
- [ ] Create `agentic_kg/knowledge_graph/models.py`
- [ ] Implement Problem model with all attributes from phase-1 design
- [ ] Implement Paper model with DOI, metadata, and content fields
- [ ] Implement Author model with affiliations and identifiers
- [ ] Implement relation models (Extends, Contradicts, DependsOn, Reframes)
- [ ] Implement Evidence and ExtractionMetadata models
- [ ] Add JSON serialization for Neo4j storage
- [ ] Add model validators for required fields

**Acceptance Criteria:**
- All models defined with proper validation
- Can serialize/deserialize to JSON
- Aligns with phase-1-knowledge-graph.md schema

---

### Task 4: Neo4j Repository Layer
- [ ] Create `agentic_kg/knowledge_graph/repository.py`
- [ ] Implement connection manager with retry logic
- [ ] Implement Problem CRUD: `create_problem()`, `get_problem()`, `update_problem()`, `delete_problem()`
- [ ] Implement Paper CRUD: `create_paper()`, `get_paper()`, `update_paper()`, `delete_paper()`
- [ ] Implement Author CRUD: `create_author()`, `get_author()`, `update_author()`
- [ ] Implement `list_problems()` with filtering
- [ ] Implement `get_papers_by_author()` query
- [ ] Add transaction support for complex operations

**Acceptance Criteria:**
- Full CRUD operations for Problem, Paper, and Author entities
- Proper error handling with custom exceptions
- Transaction support for multi-step operations

---

### Task 5: Schema Initialization
- [ ] Create `agentic_kg/knowledge_graph/schema.py`
- [ ] Define constraint creation queries (unique IDs, DOIs)
- [ ] Define index creation queries (status, domain, year)
- [ ] Create vector index for problem embeddings
- [ ] Add idempotent schema migration
- [ ] Create schema version tracking

**Acceptance Criteria:**
- Running init script creates all necessary indexes
- Can run multiple times without errors
- Schema versioning tracks migrations

---

### Task 6: Embedding Integration
- [ ] Create `agentic_kg/knowledge_graph/embeddings.py`
- [ ] Integrate OpenAI embeddings (text-embedding-3-small, 1536 dims)
- [ ] Implement `generate_problem_embedding()` function
- [ ] Add embedding generation on problem creation
- [ ] Add batch embedding for bulk imports
- [ ] Add fallback for embedding failures

**Acceptance Criteria:**
- Problems automatically get embeddings on creation
- Embeddings stored in Neo4j vector index
- Graceful handling of API failures

---

### Task 7: Hybrid Search
- [ ] Create `agentic_kg/knowledge_graph/search.py`
- [ ] Implement `semantic_search()` - vector similarity only
- [ ] Implement `structured_search()` - Cypher filters only
- [ ] Implement `hybrid_search()` - combined approach
- [ ] Add relevance score normalization
- [ ] Implement `find_similar_problems()` for deduplication

**Acceptance Criteria:**
- Can search by semantic similarity
- Can filter by domain, status, dataset availability
- Combined search returns ranked results
- Similarity detection for potential duplicates

---

### Task 8: Relation Operations
- [ ] Implement `create_relation()` method for all relation types
- [ ] Implement `get_related_problems()` method
- [ ] Implement `link_problem_to_paper()` - EXTRACTED_FROM relation
- [ ] Implement `link_paper_to_author()` - AUTHORED_BY relation
- [ ] Implement `infer_relations()` placeholder
- [ ] Add relation confidence tracking

**Acceptance Criteria:**
- Can create extends/contradicts/depends-on/reframes relations
- Can link problems to source papers
- Can traverse relations from a problem

---

### Task 9: Testing
- [ ] Create test fixtures with sample problems, papers, authors
- [ ] Write unit tests for all CRUD operations
- [ ] Write integration tests for search
- [ ] Write tests for relation operations
- [ ] Add test coverage for edge cases and error handling
- [ ] Set up pytest with Neo4j test container

**Acceptance Criteria:**
- >80% test coverage for knowledge_graph module
- All tests pass in CI
- Test isolation using Neo4j testcontainer

---

### Task 10: Sample Data
- [ ] Create `scripts/load_sample_problems.py`
- [ ] Define 10-20 sample research problems from real papers
- [ ] Include sample papers with metadata
- [ ] Create sample relations between problems
- [ ] Include varied domains (NLP, CV, ML)
- [ ] Document sample data schema

**Acceptance Criteria:**
- Can load sample data into fresh Neo4j instance
- Sample data covers all model features
- Realistic problem statements from actual papers

---

### Task 11: Documentation
- [ ] Create `agentic_kg/knowledge_graph/README.md`
- [ ] Document graph schema with diagrams
- [ ] Add API docstrings to all public methods
- [ ] Create query cookbook with examples
- [ ] Update memory-bank/techContext.md with Neo4j details

**Acceptance Criteria:**
- New developers can understand the graph schema
- Common queries documented with examples
- API fully documented

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
agentic_kg/
├── __init__.py
├── config.py                 # Environment configuration
├── knowledge_graph/
│   ├── __init__.py
│   ├── models.py             # Pydantic models (Problem, Paper, Author)
│   ├── repository.py         # Neo4j CRUD operations
│   ├── schema.py             # Database schema management
│   ├── embeddings.py         # Vector embedding generation
│   ├── search.py             # Hybrid search implementation
│   ├── relations.py          # Relation operations
│   └── README.md             # Module documentation
├── tests/
│   ├── __init__.py
│   ├── conftest.py           # Pytest fixtures, Neo4j testcontainer
│   └── knowledge_graph/
│       ├── __init__.py
│       ├── test_models.py
│       ├── test_repository.py
│       ├── test_search.py
│       └── test_relations.py
├── scripts/
│   └── load_sample_problems.py
├── docker/
│   └── docker-compose.yml    # Neo4j local development
└── .env.example              # Environment variable template
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
