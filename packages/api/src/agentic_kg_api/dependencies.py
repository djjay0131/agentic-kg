"""Shared API dependencies for dependency injection."""

import logging
from typing import Optional

from agentic_kg.knowledge_graph.repository import Neo4jRepository, get_repository
from agentic_kg.knowledge_graph.search import SearchService, get_search_service
from agentic_kg.knowledge_graph.relations import RelationService, get_relation_service
from agentic_kg.knowledge_graph.review_queue import (
    ReviewQueueService,
    get_review_queue_service,
    reset_review_queue_service,
)

logger = logging.getLogger(__name__)

_repository: Optional[Neo4jRepository] = None
_search_service: Optional[SearchService] = None
_relation_service: Optional[RelationService] = None
_review_queue_service: Optional[ReviewQueueService] = None


def get_repo() -> Neo4jRepository:
    """Get repository instance for API routes."""
    global _repository
    if _repository is None:
        _repository = get_repository()
    return _repository


def get_search() -> SearchService:
    """Get search service for API routes."""
    global _search_service
    if _search_service is None:
        _search_service = get_search_service()
    return _search_service


def get_relations() -> RelationService:
    """Get relation service for API routes."""
    global _relation_service
    if _relation_service is None:
        _relation_service = get_relation_service()
    return _relation_service


def get_review_queue() -> ReviewQueueService:
    """Get review queue service for API routes."""
    global _review_queue_service
    if _review_queue_service is None:
        repo = get_repo()
        _review_queue_service = get_review_queue_service(repo)
    return _review_queue_service


def reset_dependencies() -> None:
    """Reset all dependency singletons (for testing)."""
    global _repository, _search_service, _relation_service, _review_queue_service
    _repository = None
    _search_service = None
    _relation_service = None
    _review_queue_service = None
    reset_review_queue_service()
