"""Extraction trigger endpoints."""

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException

from agentic_kg.extraction.pipeline import PaperProcessingPipeline, PipelineConfig, get_pipeline

from agentic_kg_api.schemas import (
    BatchExtractRequest,
    BatchExtractResponse,
    ExtractedProblemResponse,
    ExtractRequest,
    ExtractResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/extract", tags=["extraction"])


def _result_to_response(result) -> ExtractResponse:
    """Convert PaperProcessingResult to API response."""
    problems = []
    for p in result.get_problems():
        problems.append(ExtractedProblemResponse(
            statement=p.statement,
            domain=p.domain,
            confidence=p.confidence,
            quoted_text=p.quoted_text[:500],
        ))

    stages = [
        {
            "stage": s.stage,
            "success": s.success,
            "duration_ms": round(s.duration_ms, 1),
            "error": s.error,
        }
        for s in result.stages
    ]

    return ExtractResponse(
        success=result.success,
        paper_title=result.paper_title,
        problems_extracted=result.problem_count,
        relations_found=result.relation_count,
        duration_ms=round(result.total_duration_ms, 1),
        problems=problems,
        stages=stages,
    )


@router.post("", response_model=ExtractResponse)
async def extract(request: ExtractRequest) -> ExtractResponse:
    """Extract problems from a paper."""
    if not request.url and not request.text:
        raise HTTPException(
            status_code=400,
            detail="Must provide either 'url' or 'text'",
        )

    pipeline = get_pipeline()

    if request.url:
        result = await pipeline.process_pdf_url(
            url=request.url,
            paper_title=request.title,
            paper_doi=request.doi,
            authors=request.authors,
        )
    else:
        result = await pipeline.process_text(
            text=request.text,
            paper_title=request.title or "Direct text input",
            paper_doi=request.doi,
            authors=request.authors,
        )

    return _result_to_response(result)


@router.post("/batch", response_model=BatchExtractResponse)
async def extract_batch(request: BatchExtractRequest) -> BatchExtractResponse:
    """Extract problems from multiple papers."""
    pipeline = get_pipeline()
    results = []

    for paper in request.papers:
        try:
            if paper.url:
                result = await pipeline.process_pdf_url(
                    url=paper.url,
                    paper_title=paper.title,
                    paper_doi=paper.doi,
                    authors=paper.authors,
                )
            elif paper.text:
                result = await pipeline.process_text(
                    text=paper.text,
                    paper_title=paper.title or "Direct text input",
                    paper_doi=paper.doi,
                    authors=paper.authors,
                )
            else:
                continue
            results.append(_result_to_response(result))
        except Exception as e:
            logger.error(f"Batch extraction error: {e}")
            results.append(ExtractResponse(
                success=False,
                paper_title=paper.title,
                stages=[{"stage": "error", "success": False, "duration_ms": 0, "error": str(e)}],
            ))

    succeeded = sum(1 for r in results if r.success)
    total_problems = sum(r.problems_extracted for r in results)

    return BatchExtractResponse(
        total=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
        total_problems=total_problems,
        results=results,
    )
