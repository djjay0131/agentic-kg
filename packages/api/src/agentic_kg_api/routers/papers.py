"""Paper endpoints."""

import logging
from urllib.parse import unquote

from agentic_kg.knowledge_graph.models import Paper
from agentic_kg.knowledge_graph.repository import Neo4jRepository, NotFoundError
from fastapi import APIRouter, Depends, HTTPException, Query

from agentic_kg_api.dependencies import get_repo
from agentic_kg_api.schemas import (
    CitationPaperEntry,
    PaperCitationCountsResponse,
    PaperCitationsResponse,
    PaperDetail,
    PaperListResponse,
    PaperReferencesResponse,
    PaperSummary,
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


# =============================================================================
# Citation graph endpoints (E-5)
#
# Registered BEFORE the catch-all /{doi:path} so FastAPI doesn't swallow
# /{doi}/references etc. as the doi path parameter.
# =============================================================================


def _row_to_citation_entry(row: dict) -> CitationPaperEntry:
    """Map a Paper-row dict (from get_references / get_citing_papers) to
    the API-side CitationPaperEntry schema."""
    return CitationPaperEntry(
        doi=row.get("doi"),
        title=row.get("title") or "(untitled)",
        year=row.get("year"),
        is_stub=bool(row.get("is_stub", False)),
        citation_count=int(row.get("citation_count", 0) or 0),
    )


@router.get("/{doi:path}/references", response_model=PaperReferencesResponse)
def get_paper_references(
    doi: str,
    limit: int = Query(default=50, ge=1, le=500),
    repo: Neo4jRepository = Depends(get_repo),
) -> PaperReferencesResponse:
    """Return papers cited by ``doi`` (out-edges via :CITES)."""
    decoded_doi = unquote(doi)
    try:
        repo.get_paper(decoded_doi)
    except NotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Paper not found: {decoded_doi}",
        )
    rows = repo.get_references(decoded_doi, limit=limit)
    entries = [_row_to_citation_entry(r) for r in rows]
    return PaperReferencesResponse(
        paper_doi=decoded_doi, references=entries, total=len(entries),
    )


@router.get("/{doi:path}/citations", response_model=PaperCitationsResponse)
def get_paper_citations(
    doi: str,
    limit: int = Query(default=50, ge=1, le=500),
    repo: Neo4jRepository = Depends(get_repo),
) -> PaperCitationsResponse:
    """Return papers that cite ``doi`` (in-edges via :CITES, scoped to
    the corpus)."""
    decoded_doi = unquote(doi)
    try:
        repo.get_paper(decoded_doi)
    except NotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Paper not found: {decoded_doi}",
        )
    rows = repo.get_citing_papers(decoded_doi, limit=limit)
    entries = [_row_to_citation_entry(r) for r in rows]
    return PaperCitationsResponse(
        paper_doi=decoded_doi, citations=entries, total=len(entries),
    )


@router.get(
    "/{doi:path}/citation-counts", response_model=PaperCitationCountsResponse,
)
def get_paper_citation_counts(
    doi: str,
    repo: Neo4jRepository = Depends(get_repo),
) -> PaperCitationCountsResponse:
    """Return denormalized citation_count / reference_count / is_stub."""
    decoded_doi = unquote(doi)
    try:
        paper = repo.get_paper(decoded_doi)
    except NotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Paper not found: {decoded_doi}",
        )
    return PaperCitationCountsResponse(
        paper_doi=paper.doi,
        citation_count=paper.citation_count,
        reference_count=paper.reference_count,
        is_stub=paper.is_stub,
    )


# =============================================================================
# Catch-all (registered last so the citation subpaths take priority)
# =============================================================================


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
