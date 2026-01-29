"""Search endpoint."""

import logging

from fastapi import APIRouter, Depends

from agentic_kg.knowledge_graph.models import ProblemStatus
from agentic_kg.knowledge_graph.search import SearchService

from agentic_kg_api.dependencies import get_search
from agentic_kg_api.schemas import (
    ProblemSummary,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/search", tags=["search"])


@router.post("", response_model=SearchResponse)
def search_problems(
    request: SearchRequest,
    search_service: SearchService = Depends(get_search),
) -> SearchResponse:
    """Hybrid search over problems."""
    status = None
    if request.status:
        try:
            status = ProblemStatus(request.status)
        except ValueError:
            pass

    results = search_service.hybrid_search(
        query=request.query,
        domain=request.domain,
        status=status,
        top_k=request.top_k,
        semantic_weight=request.semantic_weight,
    )

    items = []
    for r in results:
        confidence = None
        if r.problem.extraction_metadata:
            confidence = r.problem.extraction_metadata.confidence_score

        items.append(SearchResultItem(
            problem=ProblemSummary(
                id=r.problem.id,
                statement=r.problem.statement,
                domain=r.problem.domain,
                status=r.problem.status.value if isinstance(r.problem.status, ProblemStatus) else str(r.problem.status),
                confidence=confidence,
                created_at=r.problem.created_at,
            ),
            score=r.score,
            match_type=r.match_type,
        ))

    return SearchResponse(
        results=items,
        query=request.query,
        total=len(items),
    )
