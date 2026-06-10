"""Method API endpoints (E-4)."""

from __future__ import annotations

import logging
from typing import Optional

from agentic_kg.knowledge_graph.embeddings import generate_method_embedding
from agentic_kg.knowledge_graph.models import Method
from agentic_kg.knowledge_graph.repository import (
    Neo4jRepository,
    NotFoundError,
)
from fastapi import APIRouter, Depends, HTTPException, Query

from agentic_kg_api.dependencies import get_repo
from agentic_kg_api.schemas import (
    MethodCreateRequest,
    MethodCreateResponse,
    MethodDetail,
    MethodLinkRequest,
    MethodLinkResponse,
    MethodListResponse,
    MethodPapersResponse,
    MethodSearchResponse,
    MethodSearchResultItem,
    MethodSummary,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/methods", tags=["methods"])


def _method_to_summary(m: Method) -> MethodSummary:
    return MethodSummary(
        id=m.id,
        name=m.name,
        description=m.description,
        aliases=list(m.aliases),
        method_type=m.method_type,
        usage_count=m.usage_count,
    )


@router.get("", response_model=MethodListResponse)
def list_methods(
    name: Optional[str] = Query(
        default=None,
        description="Case-insensitive substring filter on method name",
    ),
    method_type: Optional[str] = Query(
        default=None, description="Exact-match filter on method_type"
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    repo: Neo4jRepository = Depends(get_repo),
) -> MethodListResponse:
    """List Methods with optional filters."""
    where_clauses = []
    params: dict = {"limit": limit, "offset": offset}
    if name:
        where_clauses.append("toLower(m.name) CONTAINS toLower($name)")
        params["name"] = name
    if method_type:
        where_clauses.append("m.method_type = $method_type")
        params["method_type"] = method_type

    where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    cypher = f"""
    MATCH (m:Method)
    {where}
    RETURN m
    ORDER BY m.usage_count DESC, m.name
    SKIP $offset LIMIT $limit
    """

    def _run(tx):
        result = tx.run(cypher, **params)
        return [dict(r["m"]) for r in result]

    with repo.session() as session:
        records = session.execute_read(_run)

    summaries = [_method_to_summary(repo._method_from_neo4j(r)) for r in records]
    return MethodListResponse(methods=summaries, total=len(summaries))


@router.get("/search", response_model=MethodSearchResponse)
def search_methods(
    q: str = Query(..., min_length=1, description="Free-text query"),
    top_k: int = Query(default=10, ge=1, le=100),
    min_score: Optional[float] = Query(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional cosine similarity floor",
    ),
    repo: Neo4jRepository = Depends(get_repo),
) -> MethodSearchResponse:
    """Vector similarity search over Method embeddings."""
    try:
        embedding = generate_method_embedding(q)
    except Exception as e:
        logger.warning(f"Failed to embed method query {q!r}: {e}")
        raise HTTPException(
            status_code=500, detail="Embedding service unavailable"
        )

    results = repo.search_methods_by_embedding(
        embedding=embedding, top_k=top_k, min_score=min_score
    )
    return MethodSearchResponse(
        query=q,
        results=[
            MethodSearchResultItem(
                method=_method_to_summary(method), score=score
            )
            for method, score in results
        ],
    )


@router.post("", response_model=MethodCreateResponse)
def create_method(
    request: MethodCreateRequest,
    repo: Neo4jRepository = Depends(get_repo),
) -> MethodCreateResponse:
    """Create a Method with embedding-based dedup. Pass threshold=1.01 to
    bypass dedup (operator escape valve, QA Q2 review)."""
    method, created = repo.create_or_merge_method(
        name=request.name,
        description=request.description,
        aliases=list(request.aliases),
        method_type=request.method_type,
        threshold=request.threshold,
    )
    return MethodCreateResponse(
        method=_method_to_summary(method),
        created=created,
    )


@router.get("/{method_id}", response_model=MethodDetail)
def get_method(
    method_id: str,
    repo: Neo4jRepository = Depends(get_repo),
) -> MethodDetail:
    """Fetch a Method detail."""
    try:
        method = repo.get_method(method_id)
    except NotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Method not found: {method_id}"
        )
    summary = _method_to_summary(method)
    return MethodDetail(**summary.model_dump())


@router.get("/{method_id}/papers", response_model=MethodPapersResponse)
def get_method_papers(
    method_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    repo: Neo4jRepository = Depends(get_repo),
) -> MethodPapersResponse:
    """Return Papers linked to this Method via APPLIES_METHOD."""
    try:
        repo.get_method(method_id)
    except NotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Method not found: {method_id}"
        )
    papers = repo.get_papers_for_method(method_id, limit=limit)
    return MethodPapersResponse(
        method_id=method_id,
        papers=papers,
        total=len(papers),
    )


@router.post("/{method_id}/link-paper", response_model=MethodLinkResponse)
def link_paper(
    method_id: str,
    request: MethodLinkRequest,
    repo: Neo4jRepository = Depends(get_repo),
) -> MethodLinkResponse:
    """Link a Paper to this Method (APPLIES_METHOD)."""
    try:
        created = repo.link_paper_to_method(
            paper_doi=request.entity_id, method_id=method_id,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return MethodLinkResponse(
        method_id=method_id,
        entity_id=request.entity_id,
        relationship="APPLIES_METHOD",
        created=created,
    )


@router.delete("/{method_id}")
def delete_method(
    method_id: str,
    repo: Neo4jRepository = Depends(get_repo),
) -> dict:
    """DETACH DELETE a Method. No force flag — no canonical to protect."""
    try:
        repo.delete_method(method_id)
    except NotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Method not found: {method_id}"
        )
    return {"deleted": True, "id": method_id}
