# Knowledge Graph Module

The Knowledge Graph module implements the core Knowledge Representation Layer for storing and querying research problems, papers, and their relationships.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Knowledge Graph Module                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │   models.py  │  │ repository.py│  │  schema.py   │          │
│  │              │  │              │  │              │          │
│  │  Problem     │  │ Neo4jRepo    │  │ SchemaManager│          │
│  │  Paper       │  │ CRUD ops     │  │ Constraints  │          │
│  │  Author      │  │ Transactions │  │ Indexes      │          │
│  │  Relations   │  │              │  │ Versioning   │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │embeddings.py │  │  search.py   │  │ relations.py │          │
│  │              │  │              │  │              │          │
│  │ OpenAI API   │  │ Semantic     │  │ Problem-Prob │          │
│  │ Batch embed  │  │ Structured   │  │ Problem-Paper│          │
│  │ Retry logic  │  │ Hybrid       │  │ Paper-Author │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│                         Neo4j Database                           │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐                     │
│  │:Problem │───▶│:Paper   │───▶│:Author  │                     │
│  └─────────┘    └─────────┘    └─────────┘                     │
│       │              │                                          │
│       ▼              │                                          │
│  Vector Index        │                                          │
│  (1536 dims)         │                                          │
└─────────────────────────────────────────────────────────────────┘
```

## Graph Schema

### Node Types

#### Problem
Research problems extracted from papers.

| Property | Type | Description |
|----------|------|-------------|
| id | string | Unique identifier (UUID) |
| statement | string | Problem statement (min 20 chars) |
| domain | string | Research domain (e.g., "NLP", "CV") |
| status | enum | open, in_progress, resolved, deprecated |
| assumptions | json | List of assumptions |
| constraints | json | List of constraints |
| datasets | json | Associated datasets |
| metrics | json | Evaluation metrics |
| baselines | json | Baseline methods |
| evidence | json | Source paper evidence |
| extraction_metadata | json | Extraction details |
| embedding | float[] | 1536-dim vector (optional) |
| created_at | datetime | Creation timestamp |
| updated_at | datetime | Last update timestamp |
| version | int | Version number |

#### Paper
Scientific papers that problems are extracted from.

| Property | Type | Description |
|----------|------|-------------|
| doi | string | DOI (primary key, starts with "10.") |
| title | string | Paper title |
| authors | string[] | Author names |
| venue | string | Publication venue |
| year | int | Publication year (1900-2100) |
| abstract | string | Paper abstract |
| arxiv_id | string | arXiv identifier |
| openalex_id | string | OpenAlex identifier |
| semantic_scholar_id | string | Semantic Scholar ID |
| pdf_url | string | URL to PDF |
| ingested_at | datetime | Ingestion timestamp |

#### Author
Researchers linked to papers.

| Property | Type | Description |
|----------|------|-------------|
| id | string | Unique identifier (UUID) |
| name | string | Author name |
| affiliations | string[] | Institutional affiliations |
| orcid | string | ORCID (starts with "0000-") |
| semantic_scholar_id | string | Semantic Scholar author ID |

### Relationship Types

#### Problem-to-Problem Relations

| Relationship | Direction | Description |
|--------------|-----------|-------------|
| EXTENDS | (p1)-[:EXTENDS]->(p2) | p1 builds on p2 |
| CONTRADICTS | (p1)-[:CONTRADICTS]->(p2) | p1 conflicts with p2 |
| DEPENDS_ON | (p1)-[:DEPENDS_ON]->(p2) | p1 requires p2 solved first |
| REFRAMES | (p1)-[:REFRAMES]->(p2) | p1 redefines p2's problem space |

Relation properties:
- `confidence`: float (0-1)
- `evidence_doi`: string (supporting paper)
- `created_at`: datetime

#### Entity Relations

| Relationship | Description |
|--------------|-------------|
| (Problem)-[:EXTRACTED_FROM]->(Paper) | Problem's source paper |
| (Paper)-[:AUTHORED_BY]->(Author) | Paper authorship |

## Usage Examples

### Initialize Schema

```python
from agentic_kg.knowledge_graph import initialize_schema, get_schema_info

# Initialize database schema (idempotent)
initialize_schema()

# Check schema status
info = get_schema_info()
print(f"Schema version: {info['version']}")
```

### Create and Query Problems

```python
from agentic_kg.knowledge_graph import (
    Problem, Paper, Author,
    get_repository, ProblemStatus
)
from agentic_kg.knowledge_graph.models import Evidence, ExtractionMetadata

repo = get_repository()

# Create a paper
paper = Paper(
    doi="10.1234/example.2024",
    title="Example Research Paper",
    authors=["Alice Smith", "Bob Jones"],
    year=2024,
    venue="ICML 2024",
)
repo.create_paper(paper)

# Create a problem
problem = Problem(
    statement="Large language models struggle with multi-step reasoning tasks...",
    domain="NLP",
    status=ProblemStatus.OPEN,
    evidence=Evidence(
        source_doi="10.1234/example.2024",
        source_title="Example Research Paper",
        section="Introduction",
        quoted_text="We observe that...",
    ),
    extraction_metadata=ExtractionMetadata(
        extraction_model="gpt-4",
        confidence_score=0.85,
    ),
)
repo.create_problem(problem)

# Query problems
nlp_problems = repo.list_problems(domain="NLP", status=ProblemStatus.OPEN)
```

### Semantic Search

```python
from agentic_kg.knowledge_graph import semantic_search, hybrid_search

# Search by semantic similarity
results = semantic_search("attention mechanism efficiency", top_k=5)
for result in results:
    print(f"{result.score:.2f}: {result.problem.statement[:50]}...")

# Combined semantic + structured search
results = hybrid_search(
    query="transformer optimization",
    domain="NLP",
    top_k=10,
)
```

### Find Similar Problems (Deduplication)

```python
from agentic_kg.knowledge_graph import find_similar_problems

# Check for potential duplicates before creating
similar = find_similar_problems(new_problem, threshold=0.95)
if similar:
    print(f"Potential duplicate: {similar[0].problem.id}")
```

### Create Relations

```python
from agentic_kg.knowledge_graph import get_relation_service
from agentic_kg.knowledge_graph.models import RelationType

relations = get_relation_service()

# Create EXTENDS relation
relations.create_relation(
    from_problem_id=problem1.id,
    to_problem_id=problem2.id,
    relation_type=RelationType.EXTENDS,
    confidence=0.85,
)

# Get related problems
related = relations.get_related_problems(problem1.id)
for problem, relation in related:
    print(f"{relation.relation_type}: {problem.statement[:50]}...")
```

### Generate Embeddings

```python
from agentic_kg.knowledge_graph import generate_problem_embedding

# Generate embedding for a problem
embedding = generate_problem_embedding(problem)
problem.embedding = embedding
repo.update_problem(problem)
```

## Configuration

Set environment variables:

```bash
# Neo4j connection
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your-secure-password
NEO4J_DATABASE=neo4j

# OpenAI embeddings
OPENAI_API_KEY=sk-...
EMBEDDING_MODEL=text-embedding-3-small
```

## Indexes

The schema creates the following indexes:

| Index | Type | Purpose |
|-------|------|---------|
| problem_id_unique | Constraint | Unique problem IDs |
| paper_doi_unique | Constraint | Unique paper DOIs |
| author_id_unique | Constraint | Unique author IDs |
| problem_status_idx | Index | Filter by status |
| problem_domain_idx | Index | Filter by domain |
| problem_embedding_idx | Vector | Semantic search (cosine, 1536 dims) |
| paper_year_idx | Index | Filter by year |
| paper_arxiv_idx | Index | Lookup by arXiv ID |

## Error Handling

```python
from agentic_kg.knowledge_graph import (
    RepositoryError,
    ConnectionError,
    NotFoundError,
    DuplicateError,
    EmbeddingError,
)

try:
    problem = repo.get_problem("nonexistent-id")
except NotFoundError:
    print("Problem not found")

try:
    repo.create_paper(existing_paper)
except DuplicateError:
    print("Paper already exists")
```

## Testing

Run model tests (no Neo4j required):

```bash
PYTHONPATH=packages/core/src python -m pytest packages/core/tests/ -v
```

Integration tests require Neo4j (see Task 9 in sprint plan).
