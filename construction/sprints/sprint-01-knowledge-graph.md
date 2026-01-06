# Sprint 01: Knowledge Graph Foundation

**Sprint Goal:** Implement the core Knowledge Representation Layer with Neo4j

**Start Date:** 2025-12-22
**Status:** In Progress

**Prerequisites:** Sprint 00 complete (GCP deployment working)

**Related Components:** C2 (Neo4j Database), C3 (Problem Schema), C4 (KG Repository)

**Requirements Document:** [knowledge-graph-requirements.md](../requirements/knowledge-graph-requirements.md)

---

## Tasks

### Task 1: Project Structure & Configuration
- [x] Create `agentic_kg/` package directory structure
- [x] Create `agentic_kg/config.py` for environment-based settings
- [x] Add Neo4j connection configuration (local/Aura)
- [x] Add OpenAI API key configuration for embeddings
- [x] Create `.env.example` with required environment variables

**Acceptance Criteria:**
- Clean package structure following Python conventions
- Configuration works for local development and production
- Environment variables documented

---

### Task 2: Neo4j Setup
- [x] Add Neo4j to docker-compose for local development
- [x] Create Neo4j database initialization script (schema.py)
- [x] Configure connection settings via config module
- [x] Test connection from Python (repository.py)
- [ ] Document Neo4j Aura setup for production

**Acceptance Criteria:**
- Neo4j running in Docker
- Can connect and run basic Cypher queries from Python
- Production deployment path documented

---

### Task 3: Pydantic Models
- [x] Create `agentic_kg/knowledge_graph/models.py`
- [x] Implement Problem model with all attributes from phase-1 design
- [x] Implement Paper model with DOI, metadata, and content fields
- [x] Implement Author model with affiliations and identifiers
- [x] Implement relation models (Extends, Contradicts, DependsOn, Reframes)
- [x] Implement Evidence and ExtractionMetadata models
- [x] Add JSON serialization for Neo4j storage
- [x] Add model validators for required fields

**Acceptance Criteria:**
- All models defined with proper validation
- Can serialize/deserialize to JSON
- Aligns with phase-1-knowledge-graph.md schema

---

### Task 4: Neo4j Repository Layer
- [x] Create `agentic_kg/knowledge_graph/repository.py`
- [x] Implement connection manager with retry logic
- [x] Implement Problem CRUD: `create_problem()`, `get_problem()`, `update_problem()`, `delete_problem()`
- [x] Implement Paper CRUD: `create_paper()`, `get_paper()`, `update_paper()`, `delete_paper()`
- [x] Implement Author CRUD: `create_author()`, `get_author()`, `update_author()`
- [x] Implement `list_problems()` with filtering
- [x] Implement `get_papers_by_author()` query
- [x] Add transaction support for complex operations

**Acceptance Criteria:**
- Full CRUD operations for Problem, Paper, and Author entities
- Proper error handling with custom exceptions
- Transaction support for multi-step operations

---

### Task 5: Schema Initialization
- [x] Create `agentic_kg/knowledge_graph/schema.py`
- [x] Define constraint creation queries (unique IDs, DOIs)
- [x] Define index creation queries (status, domain, year)
- [x] Create vector index for problem embeddings
- [x] Add idempotent schema migration
- [x] Create schema version tracking

**Acceptance Criteria:**
- Running init script creates all necessary indexes
- Can run multiple times without errors
- Schema versioning tracks migrations

---

### Task 6: Embedding Integration
- [x] Create `agentic_kg/knowledge_graph/embeddings.py`
- [x] Integrate OpenAI embeddings (text-embedding-3-small, 1536 dims)
- [x] Implement `generate_problem_embedding()` function
- [ ] Add embedding generation on problem creation (deferred to integration)
- [x] Add batch embedding for bulk imports
- [x] Add fallback for embedding failures

**Acceptance Criteria:**
- Problems automatically get embeddings on creation
- Embeddings stored in Neo4j vector index
- Graceful handling of API failures

---

### Task 7: Hybrid Search
- [x] Create `agentic_kg/knowledge_graph/search.py`
- [x] Implement `semantic_search()` - vector similarity only
- [x] Implement `structured_search()` - Cypher filters only
- [x] Implement `hybrid_search()` - combined approach
- [x] Add relevance score normalization
- [x] Implement `find_similar_problems()` for deduplication

**Acceptance Criteria:**
- Can search by semantic similarity
- Can filter by domain, status, dataset availability
- Combined search returns ranked results
- Similarity detection for potential duplicates

---

### Task 8: Relation Operations
- [x] Implement `create_relation()` method for all relation types
- [x] Implement `get_related_problems()` method
- [x] Implement `link_problem_to_paper()` - EXTRACTED_FROM relation
- [x] Implement `link_paper_to_author()` - AUTHORED_BY relation
- [x] Implement `infer_relations()` placeholder
- [x] Add relation confidence tracking

**Acceptance Criteria:**
- Can create extends/contradicts/depends-on/reframes relations
- Can link problems to source papers
- Can traverse relations from a problem

---

### Task 9: Testing
- [x] Create test fixtures with sample problems, papers, authors (conftest.py)
- [ ] Write unit tests for all CRUD operations (requires Neo4j container)
- [ ] Write integration tests for search (requires Neo4j container)
- [ ] Write tests for relation operations (requires Neo4j container)
- [x] Add test coverage for edge cases and error handling (171 tests for models/config)
- [ ] Set up pytest with Neo4j test container

**Acceptance Criteria:**
- >80% test coverage for knowledge_graph module
- All tests pass in CI
- Test isolation using Neo4j testcontainer

**Progress:** 171 tests for models.py and config.py passing. Repository/search/relations tests deferred until Neo4j testcontainer setup.

---

### Task 10: Sample Data
- [x] Create `scripts/load_sample_problems.py`
- [x] Define 10-20 sample research problems from real papers (6 problems)
- [x] Include sample papers with metadata (6 papers from landmark ML papers)
- [x] Create sample relations between problems (EXTENDS relation)
- [x] Include varied domains (NLP, CV, ML)
- [ ] Document sample data schema

**Acceptance Criteria:**
- Can load sample data into fresh Neo4j instance
- Sample data covers all model features
- Realistic problem statements from actual papers

**Progress:** Sample data includes 6 papers (Transformer, BERT, GPT-3, ResNet, ViT, InstructGPT), 3 authors, and 6 research problems covering attention scalability, data requirements, few-shot learning, deep network training, ViT data efficiency, and RLHF alignment.

---

### Task 11: Documentation
- [x] Create `agentic_kg/knowledge_graph/README.md`
- [x] Document graph schema with diagrams
- [x] Add API docstrings to all public methods (in source code)
- [x] Create query cookbook with examples
- [ ] Update memory-bank/techContext.md with Neo4j details

**Acceptance Criteria:**
- New developers can understand the graph schema
- Common queries documented with examples
- API fully documented

**Progress:** Created comprehensive README.md with architecture diagram, schema documentation, usage examples, configuration guide, and error handling.

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
agentic-kg/
├── packages/core/src/agentic_kg/
│   ├── __init__.py
│   ├── config.py                 # Environment configuration
│   └── knowledge_graph/
│       ├── __init__.py
│       ├── models.py             # Pydantic models (Problem, Paper, Author)
│       ├── repository.py         # Neo4j CRUD operations
│       ├── schema.py             # Database schema management
│       ├── embeddings.py         # Vector embedding generation
│       ├── search.py             # Hybrid search implementation
│       ├── relations.py          # Relation operations
│       └── README.md             # Module documentation
├── packages/core/tests/
│   ├── __init__.py
│   ├── conftest.py               # Pytest fixtures, Neo4j testcontainer
│   └── knowledge_graph/
│       ├── __init__.py
│       ├── test_models.py
│       ├── test_repository.py
│       ├── test_search.py
│       └── test_relations.py
├── scripts/
│   └── load_sample_problems.py
├── docker/
│   └── docker-compose.yml        # Neo4j local development
├── .env.example                  # Environment variable template
└── pyproject.toml                # Project dependencies
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
