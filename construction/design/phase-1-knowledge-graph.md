# Phase 1: Knowledge Graph Foundation - Design Document

**Created:** 2025-12-22
**Status:** Draft
**Related ADRs:** ADR-003 (Problems as First-Class Entities), ADR-005 (Hybrid Retrieval)

---

## Overview

This document defines the design for the Knowledge Representation Layer - the core of the Agentic Knowledge Graph system. The goal is to create a graph database structure where **research problems are first-class entities** that can be queried, linked, and progressed.

---

## 1. Graph Database Selection

### Recommendation: Neo4j

**Rationale:**
- Well-documented with mature Python drivers (`neo4j` package)
- Native property graph model fits our entity-relation design
- Built-in vector index support (Neo4j 5.x+) for hybrid retrieval
- Can run locally for development, cloud (Aura) for production
- Cypher query language is expressive for graph traversal

**Alternatives Considered:**
- **Amazon Neptune**: More complex setup, better for AWS-native deployments
- **Memgraph**: Good performance, less ecosystem support
- **NetworkX + SQLite**: Too limited for production scale

**Decision:** Use Neo4j Community Edition for development, evaluate Neo4j Aura for production.

---

## 2. Problem Entity Schema

### 2.1 Problem Node

```json
{
  "node_type": "Problem",
  "properties": {
    "id": "uuid",
    "statement": "string (required) - The research problem statement",
    "domain": "string - Research domain/field (e.g., 'NLP', 'Computer Vision')",
    "status": "enum: open | in_progress | resolved | deprecated",

    "assumptions": [
      {
        "text": "string - The assumption statement",
        "implicit": "boolean - Whether explicitly stated or inferred",
        "confidence": "float 0-1"
      }
    ],

    "constraints": [
      {
        "text": "string - The constraint description",
        "type": "enum: computational | data | methodological | theoretical",
        "confidence": "float 0-1"
      }
    ],

    "datasets": [
      {
        "name": "string - Dataset name",
        "url": "string (optional) - Link to dataset",
        "available": "boolean - Whether publicly available",
        "size": "string (optional) - Dataset size description"
      }
    ],

    "metrics": [
      {
        "name": "string - Metric name (e.g., 'F1-score', 'BLEU')",
        "description": "string (optional)",
        "baseline_value": "float (optional) - Current best/baseline"
      }
    ],

    "baselines": [
      {
        "name": "string - Baseline method name",
        "paper_doi": "string (optional)",
        "performance": "object - Metric-value pairs"
      }
    ],

    "evidence": {
      "source_doi": "string - DOI of source paper",
      "source_title": "string - Paper title",
      "section": "string - Section where extracted (e.g., 'limitations', 'future_work')",
      "quoted_text": "string - Original text from paper",
      "char_offset_start": "int",
      "char_offset_end": "int"
    },

    "extraction_metadata": {
      "extracted_at": "datetime",
      "extractor_version": "string",
      "extraction_model": "string (e.g., 'gpt-4', 'claude-3')",
      "confidence_score": "float 0-1",
      "human_reviewed": "boolean",
      "reviewed_by": "string (optional)",
      "reviewed_at": "datetime (optional)"
    },

    "embedding": "vector[1536] - Problem statement embedding for semantic search",

    "created_at": "datetime",
    "updated_at": "datetime",
    "version": "int - Version number for tracking updates"
  }
}
```

### 2.2 Relation Types

| Relation | Description | Properties |
|----------|-------------|------------|
| `EXTENDS` | Problem B extends/builds on Problem A | `confidence`, `evidence_doi`, `inferred_by` |
| `CONTRADICTS` | Problem B presents conflicting findings to A | `confidence`, `evidence_doi`, `contradiction_type` |
| `DEPENDS_ON` | Problem B requires solution to Problem A first | `confidence`, `dependency_type` |
| `REFRAMES` | Problem B redefines the problem space of A | `confidence`, `evidence_doi` |
| `EXTRACTED_FROM` | Problem was extracted from Paper | `section`, `extraction_date` |
| `CITES` | Problem references another paper | `citation_context` |

### 2.3 Supporting Node Types

**Paper Node:**
```json
{
  "node_type": "Paper",
  "properties": {
    "doi": "string (primary key)",
    "title": "string",
    "authors": ["string"],
    "venue": "string",
    "year": "int",
    "abstract": "string",
    "arxiv_id": "string (optional)",
    "openalex_id": "string (optional)",
    "pdf_url": "string (optional)",
    "ingested_at": "datetime"
  }
}
```

**Author Node:**
```json
{
  "node_type": "Author",
  "properties": {
    "id": "string",
    "name": "string",
    "affiliations": ["string"],
    "orcid": "string (optional)"
  }
}
```

---

## 3. Vector Index Design

### 3.1 Embedding Strategy

**What to embed:**
1. **Problem statements** - Primary semantic search target
2. **Assumption text** - For finding problems with similar assumptions
3. **Constraint descriptions** - For constraint-based similarity

**Embedding Model:** OpenAI `text-embedding-3-small` (1536 dimensions)
- Good balance of quality and cost
- Can switch to local models (e.g., `sentence-transformers`) if needed

### 3.2 Neo4j Vector Index

```cypher
// Create vector index for problem statements
CREATE VECTOR INDEX problem_embedding_index IF NOT EXISTS
FOR (p:Problem)
ON p.embedding
OPTIONS {indexConfig: {
  `vector.dimensions`: 1536,
  `vector.similarity_function`: 'cosine'
}}
```

### 3.3 Hybrid Query Pattern

```cypher
// Example: Find open problems similar to query, filtered by domain
CALL db.index.vector.queryNodes('problem_embedding_index', 10, $query_embedding)
YIELD node, score
WHERE node.status = 'open' AND node.domain = $domain
RETURN node, score
ORDER BY score DESC
LIMIT 5
```

---

## 4. Database Schema (Cypher)

```cypher
// Constraints
CREATE CONSTRAINT problem_id IF NOT EXISTS FOR (p:Problem) REQUIRE p.id IS UNIQUE;
CREATE CONSTRAINT paper_doi IF NOT EXISTS FOR (p:Paper) REQUIRE p.doi IS UNIQUE;
CREATE CONSTRAINT author_id IF NOT EXISTS FOR (a:Author) REQUIRE a.id IS UNIQUE;

// Indexes for common queries
CREATE INDEX problem_status IF NOT EXISTS FOR (p:Problem) ON (p.status);
CREATE INDEX problem_domain IF NOT EXISTS FOR (p:Problem) ON (p.domain);
CREATE INDEX paper_year IF NOT EXISTS FOR (p:Paper) ON (p.year);

// Vector index
CREATE VECTOR INDEX problem_embedding_index IF NOT EXISTS
FOR (p:Problem)
ON p.embedding
OPTIONS {indexConfig: {
  `vector.dimensions`: 1536,
  `vector.similarity_function`: 'cosine'
}};
```

---

## 5. Python Integration

### 5.1 Dependencies

```python
# requirements.txt additions
neo4j>=5.0.0
pydantic>=2.0.0
openai>=1.0.0  # for embeddings
```

### 5.2 Pydantic Models

```python
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum
import uuid

class ProblemStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    DEPRECATED = "deprecated"

class ConstraintType(str, Enum):
    COMPUTATIONAL = "computational"
    DATA = "data"
    METHODOLOGICAL = "methodological"
    THEORETICAL = "theoretical"

class Assumption(BaseModel):
    text: str
    implicit: bool = False
    confidence: float = Field(ge=0, le=1, default=0.8)

class Constraint(BaseModel):
    text: str
    type: ConstraintType
    confidence: float = Field(ge=0, le=1, default=0.8)

class Dataset(BaseModel):
    name: str
    url: Optional[str] = None
    available: bool = True
    size: Optional[str] = None

class Metric(BaseModel):
    name: str
    description: Optional[str] = None
    baseline_value: Optional[float] = None

class Baseline(BaseModel):
    name: str
    paper_doi: Optional[str] = None
    performance: dict = Field(default_factory=dict)

class Evidence(BaseModel):
    source_doi: str
    source_title: str
    section: str
    quoted_text: str
    char_offset_start: Optional[int] = None
    char_offset_end: Optional[int] = None

class ExtractionMetadata(BaseModel):
    extracted_at: datetime = Field(default_factory=datetime.utcnow)
    extractor_version: str = "1.0.0"
    extraction_model: str
    confidence_score: float = Field(ge=0, le=1)
    human_reviewed: bool = False
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None

class Problem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    statement: str
    domain: Optional[str] = None
    status: ProblemStatus = ProblemStatus.OPEN

    assumptions: List[Assumption] = Field(default_factory=list)
    constraints: List[Constraint] = Field(default_factory=list)
    datasets: List[Dataset] = Field(default_factory=list)
    metrics: List[Metric] = Field(default_factory=list)
    baselines: List[Baseline] = Field(default_factory=list)

    evidence: Evidence
    extraction_metadata: ExtractionMetadata

    embedding: Optional[List[float]] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    version: int = 1
```

---

## 6. Implementation Tasks

### Sprint 01: Knowledge Graph Foundation

| Task | Description | Priority |
|------|-------------|----------|
| 1.1 | Set up Neo4j local instance (Docker) | High |
| 1.2 | Create Pydantic models for Problem entity | High |
| 1.3 | Implement Neo4j connection manager | High |
| 1.4 | Create schema initialization scripts | High |
| 1.5 | Implement Problem CRUD operations | High |
| 1.6 | Add vector embedding generation | Medium |
| 1.7 | Implement hybrid search (graph + vector) | Medium |
| 1.8 | Create sample data loader for testing | Medium |
| 1.9 | Write unit tests for graph operations | Medium |
| 1.10 | Document API and query patterns | Low |

---

## 7. Example Queries

### Find open problems in NLP domain
```cypher
MATCH (p:Problem)
WHERE p.status = 'open' AND p.domain = 'NLP'
RETURN p.statement, p.assumptions, p.datasets
ORDER BY p.created_at DESC
LIMIT 10
```

### Find problems that extend a specific problem
```cypher
MATCH (p1:Problem)-[r:EXTENDS]->(p2:Problem {id: $problem_id})
RETURN p1, r.confidence
ORDER BY r.confidence DESC
```

### Find problems with available datasets
```cypher
MATCH (p:Problem)
WHERE p.status = 'open'
  AND ANY(d IN p.datasets WHERE d.available = true)
RETURN p
```

### Semantic search + filtering
```cypher
CALL db.index.vector.queryNodes('problem_embedding_index', 20, $query_embedding)
YIELD node, score
WHERE node.status = 'open'
WITH node, score
MATCH (node)-[:EXTRACTED_FROM]->(paper:Paper)
WHERE paper.year >= 2023
RETURN node.statement, paper.title, score
ORDER BY score DESC
LIMIT 5
```

---

## 8. Open Questions

1. **Embedding updates**: How to handle re-embedding when problem statements are refined?
2. **Deduplication**: What similarity threshold indicates duplicate problems?
3. **Versioning strategy**: Full version history vs. latest + previous only?
4. **Multi-tenancy**: Do we need user/project isolation in the graph?

---

## Next Steps

1. Create ADR-010 for Neo4j selection
2. Create Sprint 01 task breakdown
3. Set up Neo4j Docker container
4. Implement core Pydantic models
