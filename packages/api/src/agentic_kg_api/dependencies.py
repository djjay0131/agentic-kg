"""Shared API dependencies for dependency injection."""

import logging
from typing import Optional

from agentic_kg.knowledge_graph.pipeline_runs import PipelineRunRepository
from agentic_kg.knowledge_graph.relations import RelationService, get_relation_service
from agentic_kg.knowledge_graph.repository import Neo4jRepository, get_repository
from agentic_kg.knowledge_graph.review_queue import (
    ReviewQueueService,
    get_review_queue_service,
    reset_review_queue_service,
)
from agentic_kg.knowledge_graph.search import SearchService, get_search_service
from fastapi import Depends, HTTPException

logger = logging.getLogger(__name__)

_repository: Optional[Neo4jRepository] = None
_search_service: Optional[SearchService] = None
_relation_service: Optional[RelationService] = None
_review_queue_service: Optional[ReviewQueueService] = None
#: One read-only canonical reader per worker process. The Neo4j driver it holds
#: is lazy and pool-backed, so caching it avoids opening a fresh driver on every
#: request; it is released when the worker exits (FastAPI lifespan calls
#: ``reset_dependencies``).
_canonical_reader: Optional[object] = None


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


def get_pipeline_runs(
    repo: Neo4jRepository = Depends(get_repo),
) -> PipelineRunRepository:
    """A PipelineRunRepository bound to the request's Neo4j repository.

    Deliberately not a module singleton: the object only holds a reference to
    the repository, so building one per request is free and it always reflects
    a dependency override in tests.
    """
    return PipelineRunRepository(repo)


def reset_dependencies() -> None:
    """Reset all dependency singletons (for testing)."""
    global _repository, _search_service, _relation_service, _review_queue_service
    global _canonical_reader
    _repository = None
    _search_service = None
    _relation_service = None
    _review_queue_service = None
    _canonical_reader = None
    reset_review_queue_service()


def require_canonical_enabled() -> None:
    """Gate the read-only canonical router. Disabled => 404, before any I/O."""
    from agentic_kg_api.config import get_api_config

    if not get_api_config().canonical_api_enabled:
        raise HTTPException(
            status_code=404,
            detail=(
                "the canonical projection API is disabled; set CANONICAL_API_ENABLED=1 to expose it"
            ),
        )


def get_canonical_reader(_: None = Depends(require_canonical_enabled)):
    """A read-only canonical reader.

    The reader is structurally not a ``GraphMutationStore`` (no ``apply``, no
    writer primitive, no store reference), so application code that receives it
    has no canonical write surface. Building one requires the opt-in
    ``migration`` extra; a flag-on deployment without it gets 503, not a 500.
    """
    from agentic_kg.migration.canonical_read import (
        canonical_read_target_from_env,
        open_canonical_reader,
    )

    global _canonical_reader
    if _canonical_reader is not None:
        return _canonical_reader
    try:
        _canonical_reader = open_canonical_reader(canonical_read_target_from_env())
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 - reported as unavailable, not a crash
        logger.error("failed to open the canonical reader: %s", e)
        raise HTTPException(
            status_code=503,
            detail=f"canonical reader unavailable: {type(e).__name__}",
        ) from e
    return _canonical_reader
