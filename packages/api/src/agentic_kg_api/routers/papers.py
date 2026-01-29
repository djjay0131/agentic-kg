"""Paper endpoints."""

import logging
from typing import Optional
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query

from agentic_kg.knowledge_graph.models import Paper
from agentic_kg.knowledge_graph.repository import Neo4jRepository, NotFoundError

from agentic_kg_api.dependencies import get_repo
from agentic_kg_api.schemas import (
    PaperDetail,
    PaperListResponse,
    PaperSummary,
    ProblemSummary,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/papers", tags=["papers"])


def _paper_to_summary(p: Paper) -> PaperSummary:
    """Convert a Paper model to a summary response."""
    return PaperSummary(
        doi=p.doi,
        title=p.title,
        authors=p.authors,
        year=p.year,
        venue=p.venue,
    )


def _paper_to_detail(p: Paper) -> PaperDetail:
    """Convert a Paper model to a detail response."""
    return PaperDetail(
        doi=p.doi,
        title=p.title,
        authors=p.authors,
        year=p.year,
        venue=p.venue,
        abstract=p.abstract,
        arxiv_id=p.arxiv_id,
        pdf_url=p.pdf_url,
        citation_count=p.citation_count,
    )


@router.get("", response_model=PaperListResponse)
def list_papers(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    repo: Neo4jRepository = Depends(get_repo),
) -> PaperListResponse:
    """List papers."""
    # Neo4jRepository doesn't have list_papers, use a direct query
    with repo.session() as session:
        result = session.run(
            "MATCH (p:Paper) RETURN p ORDER BY p.year DESC SKIP $offset LIMIT $limit",
            offset=offset,
            limit=limit,
        )
        records = [dict(record["p"]) for record in result]

    papers = []
    for record in records:
        papers.append(PaperSummary(
            doi=record.get("doi", ""),
            title=record.get("title", ""),
            authors=record.get("authors", []),
            year=record.get("year"),
            venue=record.get("venue"),
        ))

    return PaperListResponse(
        papers=papers,
        total=len(papers),
        limit=limit,
        offset=offset,
    )


@router.get("/{doi:path}", response_model=PaperDetail)
def get_paper(
    doi: str,
    repo: Neo4jRepository = Depends(get_repo),
) -> PaperDetail:
    """Get a paper by DOI."""
    decoded_doi = unquote(doi)
    try:
        paper = repo.get_paper(decoded_doi)
    except NotFoundError:
        raise HTTPException(status_code=404, detail=f"Paper not found: {decoded_doi}")
    return _paper_to_detail(paper)
