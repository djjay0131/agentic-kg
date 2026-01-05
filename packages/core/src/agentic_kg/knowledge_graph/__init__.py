"""
Knowledge Graph module for Agentic KG.

Provides:
- Pydantic models for Problem, Paper, Author entities
- Neo4j repository for CRUD operations
- Embedding generation and vector search
- Hybrid search combining graph queries and semantic similarity
"""

from agentic_kg.knowledge_graph.models import (
    Author,
    Paper,
    Problem,
    ProblemStatus,
    RelationType,
)

__all__ = [
    "Author",
    "Paper",
    "Problem",
    "ProblemStatus",
    "RelationType",
]
