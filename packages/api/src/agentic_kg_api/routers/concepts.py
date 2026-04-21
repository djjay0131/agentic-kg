"""ResearchConcept API endpoints (E-2)."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from agentic_kg.knowledge_graph.embeddings import generate_research_concept_embedding
from agentic_kg.knowledge_graph.models import ResearchConcept
from agentic_kg.knowledge_graph.repository import (
    Neo4jRepository,
    NotFoundError,
)

from agentic_kg_api.dependencies import get_repo
from agentic_kg_api.schemas import (
    ConceptCreateRequest,
    ConceptCreateResponse,
    ConceptDetail,
    ConceptLinkRequest,
    ConceptLinkResponse,
    ConceptListResponse,
    ConceptPapersResponse,
    ConceptProblemsResponse,
    ConceptSearchResponse,
    ConceptSearchResultItem,
    ConceptSummary,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/concepts", tags=["concepts"])


# =============================================================================
# Helpers
# =============================================================================


def _concept_to_summary(concept: ResearchConcept) -> ConceptSummary:
    return ConceptSummary(
        id=concept.id,
        name=concept.name,
        description=concept.description,
        aliases=list(concept.aliases),
        mention_count=concept.mention_count,
        paper_count=concept.paper_count,
    )


# =============================================================================
# Endpoints
# =============================================================================


@router.get("", response_model=ConceptListResponse)
def list_concepts(
    name: Optional[str] = Query(
        default=None,
        description="Case-insensitive substring filter on concept name",
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    repo: Neo4jRepository = Depends(get_repo),
) -> ConceptListResponse:
    """List ResearchConcepts, optionally filtered by name substring."""
    if name:
        cypher = """
        MATCH (rc:ResearchConcept)
        WHERE toLower(rc.name) CONTAINS toLower($name)
        RETURN rc
        ORDER BY rc.mention_count DESC, rc.name
        SKIP $offset LIMIT $limit
        """
    else:
        cypher = """
        MATCH (rc:ResearchConcept)
        RETURN rc
        ORDER BY rc.mention_count DESC, rc.name
        SKIP $offset LIMIT $limit
        """

    def _run(tx):
        params = {"limit": limit, "offset": offset}
        if name:
            params["name"] = name
        result = tx.run(cypher, **params)
        return [dict(r["rc"]) for r in result]

    with repo.session() as session:
        records = session.execute_read(_run)

    summaries = [
        _concept_to_summary(repo._research_concept_from_neo4j(r))
        for r in records
    ]
    return ConceptListResponse(concepts=summaries, total=len(summaries))


@router.get("/search", response_model=ConceptSearchResponse)
def search_concepts(
    q: str = Query(..., min_length=1, description="Free-text query"),
    top_k: int = Query(default=10, ge=1, le=100),
    min_score: Optional[float] = Query(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional cosine similarity floor",
    ),
    repo: Neo4jRepository = Depends(get_repo),
) -> ConceptSearchResponse:
    """Vector similarity search over ResearchConcept embeddings."""
    try:
        embedding = generate_research_concept_embedding(q)
    except Exception as e:
        logger.warning(f"Failed to embed concept query {q!r}: {e}")
        raise HTTPException(
            status_code=500, detail="Embedding service unavailable"
        )

    results = repo.search_research_concepts_by_embedding(
        embedding=embedding, top_k=top_k, min_score=min_score
    )
    return ConceptSearchResponse(
        query=q,
        results=[
            ConceptSearchResultItem(
                concept=_concept_to_summary(concept), score=score
            )
            for concept, score in results
        ],
    )


@router.post("", response_model=ConceptCreateResponse)
def create_concept(
    request: ConceptCreateRequest,
    repo: Neo4jRepository = Depends(get_repo),
) -> ConceptCreateResponse:
    """
    Create a ResearchConcept with embedding-based dedup.

    If an existing concept scores above the dedup threshold, the
    incoming name and aliases are merged into it and the existing
    concept is returned (``created=false``). Otherwise a new node is
    inserted (``created=true``).
    """
    concept, created = repo.create_or_merge_research_concept(
        name=request.name,
        description=request.description,
        aliases=list(request.aliases),
        threshold=request.threshold,
    )
    return ConceptCreateResponse(
        concept=_concept_to_summary(concept),
        created=created,
    )


@router.get("/{concept_id}", response_model=ConceptDetail)
def get_concept(
    concept_id: str,
    repo: Neo4jRepository = Depends(get_repo),
) -> ConceptDetail:
    """Fetch a ResearchConcept detail."""
    try:
        concept = repo.get_research_concept(concept_id)
    except NotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Concept not found: {concept_id}"
        )
    summary = _concept_to_summary(concept)
    return ConceptDetail(**summary.model_dump())


@router.get("/{concept_id}/problems", response_model=ConceptProblemsResponse)
def get_concept_problems(
    concept_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    repo: Neo4jRepository = Depends(get_repo),
) -> ConceptProblemsResponse:
    """Return ProblemConcepts linked to this concept via INVOLVES_CONCEPT."""
    try:
        repo.get_research_concept(concept_id)
    except NotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Concept not found: {concept_id}"
        )
    problems = repo.get_problems_for_concept(concept_id, limit=limit)
    return ConceptProblemsResponse(
        concept_id=concept_id,
        problems=problems,
        total=len(problems),
    )


@router.get("/{concept_id}/papers", response_model=ConceptPapersResponse)
def get_concept_papers(
    concept_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    repo: Neo4jRepository = Depends(get_repo),
) -> ConceptPapersResponse:
    """Return Papers linked to this concept via DISCUSSES."""
    try:
        repo.get_research_concept(concept_id)
    except NotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Concept not found: {concept_id}"
        )
    papers = repo.get_papers_for_concept(concept_id, limit=limit)
    return ConceptPapersResponse(
        concept_id=concept_id,
        papers=papers,
        total=len(papers),
    )


@router.post(
    "/{concept_id}/link-problem", response_model=ConceptLinkResponse
)
def link_problem(
    concept_id: str,
    request: ConceptLinkRequest,
    repo: Neo4jRepository = Depends(get_repo),
) -> ConceptLinkResponse:
    """Link a ProblemConcept to this ResearchConcept (INVOLVES_CONCEPT)."""
    try:
        created = repo.link_problem_to_concept(
            problem_concept_id=request.entity_id,
            research_concept_id=concept_id,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return ConceptLinkResponse(
        concept_id=concept_id,
        entity_id=request.entity_id,
        relationship="INVOLVES_CONCEPT",
        created=created,
    )


@router.post("/{concept_id}/link-paper", response_model=ConceptLinkResponse)
def link_paper(
    concept_id: str,
    request: ConceptLinkRequest,
    repo: Neo4jRepository = Depends(get_repo),
) -> ConceptLinkResponse:
    """Link a Paper to this ResearchConcept (DISCUSSES)."""
    try:
        created = repo.link_paper_to_concept(
            paper_doi=request.entity_id,
            research_concept_id=concept_id,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return ConceptLinkResponse(
        concept_id=concept_id,
        entity_id=request.entity_id,
        relationship="DISCUSSES",
        created=created,
    )
