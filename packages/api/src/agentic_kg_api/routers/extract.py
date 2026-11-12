"""Extraction trigger endpoints."""

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

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


def _raise_on_pipeline_failure(result) -> None:
    """Surface a failed pipeline stage as a 5xx, never a silent empty success.

    A pipeline that found no problems is a valid 200; a pipeline whose
    extraction stage raised is not. The stage records the provider/parse error,
    so return it (bounded) instead of an empty ``problems: []`` with
    ``success: false``.
    """
    failures = [stage for stage in result.stages if not stage.success]
    if not failures:
        return
    detail = "; ".join(
        f"{stage.stage}: {stage.error or 'failed'}" for stage in failures
    )
    raise HTTPException(status_code=502, detail=f"extraction failed: {detail}"[:1000])


def _result_to_response(result) -> ExtractResponse:
    """Convert PaperProcessingResult to API response."""
    problems = []
    for p in result.get_problems():
        problems.append(ExtractedProblemResponse(
            statement=p.statement,
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
async def extract(req: Request, request: ExtractRequest) -> ExtractResponse:
    """Extract problems from a paper."""
    # Generate request ID for tracing
    request_id = str(uuid.uuid4())[:8]

    if not request.url and not request.text:
        raise HTTPException(
            status_code=400,
            detail="Must provide either 'url' or 'text'",
        )
    if request.text is not None and not request.text.strip():
        raise HTTPException(status_code=400, detail="'text' must not be empty")

    pipeline = get_pipeline()

    if request.url:
        logger.info(f"[{request_id}] Extract request: URL={request.url}, title={request.title}")
        result = await pipeline.process_pdf_url(
            url=request.url,
            paper_title=request.title,
            paper_doi=request.doi,
            authors=request.authors,
        )
        logger.info(
            f"[{request_id}] Extract complete: "
            f"success={result.success}, problems={result.problem_count}, "
            f"duration={result.total_duration_ms:.0f}ms"
        )
    else:
        result = await pipeline.process_text(
            text=request.text,
            paper_title=request.title or "Direct text input",
            paper_doi=request.doi,
            authors=request.authors,
        )

    _raise_on_pipeline_failure(result)
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
