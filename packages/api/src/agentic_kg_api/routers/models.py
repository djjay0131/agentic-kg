"""Model API endpoints (E-3)."""

from __future__ import annotations

import logging
from typing import Optional

from agentic_kg.knowledge_graph.embeddings import generate_model_embedding
from agentic_kg.knowledge_graph.models import Model
from agentic_kg.knowledge_graph.repository import (
    Neo4jRepository,
    NotFoundError,
)
from fastapi import APIRouter, Depends, HTTPException, Query

from agentic_kg_api.dependencies import get_repo
from agentic_kg_api.schemas import (
    ModelCreateRequest,
    ModelCreateResponse,
    ModelDetail,
    ModelLinkRequest,
    ModelLinkResponse,
    ModelListResponse,
    ModelPapersResponse,
    ModelSearchResponse,
    ModelSearchResultItem,
    ModelSummary,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/models", tags=["models"])


def _model_to_summary(m: Model) -> ModelSummary:
    return ModelSummary(
        id=m.id,
        name=m.name,
        description=m.description,
        aliases=list(m.aliases),
        architecture=m.architecture,
        model_type=m.model_type,
        year_introduced=m.year_introduced,
        introducing_paper_doi=m.introducing_paper_doi,
        is_canonical=m.is_canonical,
        usage_count=m.usage_count,
    )


@router.get("", response_model=ModelListResponse)
def list_models(
    name: Optional[str] = Query(
        default=None,
        description="Case-insensitive substring filter on model name",
    ),
    architecture: Optional[str] = Query(
        default=None, description="Exact-match filter on architecture"
    ),
    is_canonical: Optional[bool] = Query(
        default=None, description="Filter by canonical flag"
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    repo: Neo4jRepository = Depends(get_repo),
) -> ModelListResponse:
    """List Models with optional filters."""
    where_clauses = []
    params: dict = {"limit": limit, "offset": offset}
    if name:
        where_clauses.append("toLower(m.name) CONTAINS toLower($name)")
        params["name"] = name
    if architecture:
        where_clauses.append("m.architecture = $architecture")
        params["architecture"] = architecture
    if is_canonical is not None:
        where_clauses.append("m.is_canonical = $is_canonical")
        params["is_canonical"] = is_canonical

    where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    cypher = f"""
    MATCH (m:Model)
    {where}
    RETURN m
    ORDER BY m.is_canonical DESC, m.usage_count DESC, m.name
    SKIP $offset LIMIT $limit
    """

    def _run(tx):
        result = tx.run(cypher, **params)
        return [dict(r["m"]) for r in result]

    with repo.session() as session:
        records = session.execute_read(_run)

    summaries = [_model_to_summary(repo._model_from_neo4j(r)) for r in records]
    return ModelListResponse(models=summaries, total=len(summaries))


@router.get("/search", response_model=ModelSearchResponse)
def search_models(
    q: str = Query(..., min_length=1, description="Free-text query"),
    top_k: int = Query(default=10, ge=1, le=100),
    min_score: Optional[float] = Query(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional cosine similarity floor",
    ),
    repo: Neo4jRepository = Depends(get_repo),
) -> ModelSearchResponse:
    """Vector similarity search over Model embeddings."""
    try:
        embedding = generate_model_embedding(q)
    except Exception as e:
        logger.warning(f"Failed to embed model query {q!r}: {e}")
        raise HTTPException(
            status_code=500, detail="Embedding service unavailable"
        )

    results = repo.search_models_by_embedding(
        embedding=embedding, top_k=top_k, min_score=min_score
    )
    return ModelSearchResponse(
        query=q,
        results=[
            ModelSearchResultItem(
                model=_model_to_summary(model), score=score
            )
            for model, score in results
        ],
    )


@router.post("", response_model=ModelCreateResponse)
def create_model(
    request: ModelCreateRequest,
    repo: Neo4jRepository = Depends(get_repo),
) -> ModelCreateResponse:
    """Create a Model with embedding-based dedup + canonical protection."""
    model, created = repo.create_or_merge_model(
        name=request.name,
        description=request.description,
        aliases=list(request.aliases),
        architecture=request.architecture,
        model_type=request.model_type,
        year_introduced=request.year_introduced,
        introducing_paper_doi=request.introducing_paper_doi,
        is_canonical=request.is_canonical,
        threshold=request.threshold,
    )
    return ModelCreateResponse(
        model=_model_to_summary(model),
        created=created,
    )


@router.get("/{model_id}", response_model=ModelDetail)
def get_model(
    model_id: str,
    repo: Neo4jRepository = Depends(get_repo),
) -> ModelDetail:
    """Fetch a Model detail."""
    try:
        model = repo.get_model(model_id)
    except NotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Model not found: {model_id}"
        )
    summary = _model_to_summary(model)
    return ModelDetail(**summary.model_dump())


@router.get("/{model_id}/papers", response_model=ModelPapersResponse)
def get_model_papers(
    model_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    repo: Neo4jRepository = Depends(get_repo),
) -> ModelPapersResponse:
    """Return Papers linked to this Model via USES_MODEL."""
    try:
        repo.get_model(model_id)
    except NotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Model not found: {model_id}"
        )
    papers = repo.get_papers_for_model(model_id, limit=limit)
    return ModelPapersResponse(
        model_id=model_id,
        papers=papers,
        total=len(papers),
    )


@router.post("/{model_id}/link-paper", response_model=ModelLinkResponse)
def link_paper(
    model_id: str,
    request: ModelLinkRequest,
    repo: Neo4jRepository = Depends(get_repo),
) -> ModelLinkResponse:
    """Link a Paper to this Model (USES_MODEL)."""
    try:
        created = repo.link_paper_to_model(
            paper_doi=request.entity_id, model_id=model_id,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return ModelLinkResponse(
        model_id=model_id,
        entity_id=request.entity_id,
        relationship="USES_MODEL",
        created=created,
    )


@router.delete("/{model_id}")
def delete_model(
    model_id: str,
    force: bool = Query(
        default=False,
        description=(
            "Required to delete a canonical Model. DETACH DELETE semantics: "
            "the node and all inbound USES_MODEL edges are removed."
        ),
    ),
    repo: Neo4jRepository = Depends(get_repo),
) -> dict:
    """DETACH DELETE a Model. Refuses canonical without ``force=true``."""
    try:
        repo.delete_model(model_id, force=force)
    except NotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Model not found: {model_id}"
        )
    except ValueError as e:
        # Canonical without force.
        raise HTTPException(status_code=409, detail=str(e))
    return {"deleted": True, "id": model_id}
