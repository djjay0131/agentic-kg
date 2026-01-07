"""
Knowledge Graph module for Agentic KG.

Provides:
- Pydantic models for Problem, Paper, Author entities
- Neo4j repository for CRUD operations
- Embedding generation and vector search
- Hybrid search combining graph queries and semantic similarity
"""

from agentic_kg.knowledge_graph.embeddings import (
    EmbeddingError,
    EmbeddingService,
    generate_problem_embedding,
    get_embedding_service,
)
from agentic_kg.knowledge_graph.models import (
    Author,
    Paper,
    Problem,
    ProblemStatus,
    RelationType,
)
from agentic_kg.knowledge_graph.relations import (
    RelationError,
    RelationService,
    get_relation_service,
)
from agentic_kg.knowledge_graph.repository import (
    ConnectionError,
    DuplicateError,
    Neo4jRepository,
    NotFoundError,
    RepositoryError,
    get_repository,
    reset_repository,
)
from agentic_kg.knowledge_graph.schema import (
    SchemaManager,
    get_schema_info,
    initialize_schema,
)
from agentic_kg.knowledge_graph.search import (
    SearchResult,
    SearchService,
    find_similar_problems,
    get_search_service,
    hybrid_search,
    semantic_search,
)

__all__ = [
    # Models
    "Author",
    "Paper",
    "Problem",
    "ProblemStatus",
    "RelationType",
    # Repository
    "ConnectionError",
    "DuplicateError",
    "Neo4jRepository",
    "NotFoundError",
    "RepositoryError",
    "get_repository",
    "reset_repository",
    # Schema
    "SchemaManager",
    "get_schema_info",
    "initialize_schema",
    # Embeddings
    "EmbeddingError",
    "EmbeddingService",
    "generate_problem_embedding",
    "get_embedding_service",
    # Search
    "SearchResult",
    "SearchService",
    "find_similar_problems",
    "get_search_service",
    "hybrid_search",
    "semantic_search",
    # Relations
    "RelationError",
    "RelationService",
    "get_relation_service",
]
