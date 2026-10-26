"""Shared API dependencies for dependency injection."""

import hmac
import logging
import os
from typing import Optional

from agentic_kg.knowledge_graph.pipeline_runs import PipelineRunRepository
from agentic_kg.knowledge_graph.relations import RelationService, get_relation_service
from agentic_kg.knowledge_graph.repository import Neo4jRepository, get_repository
from agentic_kg.knowledge_graph.review_items import (
    CandidateReviewService,
    ReviewItemRepository,
)
from agentic_kg.knowledge_graph.review_queue import (
    ReviewQueueService,
    get_review_queue_service,
    reset_review_queue_service,
)
from agentic_kg.knowledge_graph.search import SearchService, get_search_service
from fastapi import Depends, Header, HTTPException

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


def get_review_items(
    repo: Neo4jRepository = Depends(get_repo),
) -> CandidateReviewService:
    """The nightly review-queue service for the canonical namespace.

    Per-request (the object only holds a repository reference), so a test's
    dependency override is always the one the router uses. The default commit
    path is KGCS; the API test overrides the service with a fake committer.
    """
    namespace = os.getenv("KGCS_CANONICAL_NAMESPACE", "canon")
    return CandidateReviewService(
        ReviewItemRepository(repo, namespace=namespace)
    )


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


def require_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> None:
    """Gate a router behind the shared API key (ADR-0007, spec C5).

    Compares in constant time. Missing and wrong keys are deliberately
    indistinguishable: both get the same 401 body.

    If no usable key is installed the dependency **fails closed** with 503,
    never open. A key is unusable when ``API_KEY`` is unset/empty or still the
    public Terraform placeholder seed. Only routers that opt in with
    ``dependencies=[Depends(require_api_key)]`` are affected; public endpoints
    stay public.
    """
    from agentic_kg_api.config import API_KEY_PLACEHOLDER, get_api_config

    expected = get_api_config().api_key
    if not expected or expected == API_KEY_PLACEHOLDER:
        raise HTTPException(status_code=503, detail="auth not configured")

    supplied = x_api_key or ""
    if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="invalid or missing API key")


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
