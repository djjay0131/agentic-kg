"""E-5 Citation Graph orchestration.

The ``populate_citations`` helper turns a Paper's Semantic Scholar
reference list into ``CITES`` edges and stub Paper nodes. Designed to
be callable from:

- ``PaperImporter.import_paper`` (production ingestion)
- A standalone CLI / API endpoint (operator-driven backfill, if we ever
  add one)
- Tests against testcontainers Neo4j

All failures are logged WARN and absorbed — citation graph enrichment
must never block the main ingestion flow. The Paper itself has already
been created when this helper runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from agentic_kg.knowledge_graph.repository import NotFoundError

if TYPE_CHECKING:
    from agentic_kg.data_acquisition.semantic_scholar import SemanticScholarClient
    from agentic_kg.knowledge_graph.repository import Neo4jRepository

logger = logging.getLogger(__name__)


@dataclass
class CitationPopulationResult:
    """Counts returned by populate_citations."""

    stubs_created: int = 0
    edges_created: int = 0
    skipped_no_doi: int = 0
    skipped_no_s2_id: bool = False
    fetch_failed: bool = False
    errors: list[str] = field(default_factory=list)


def _extract_doi(ext_ids: Optional[dict[str, Any]]) -> Optional[str]:
    """Pull a DOI string out of the Semantic Scholar externalIds dict.

    The API exposes the key as ``"DOI"`` (capitalized). Tolerant of
    missing-key / None cases.
    """
    if not ext_ids:
        return None
    doi = ext_ids.get("DOI")
    return doi if isinstance(doi, str) and doi else None


async def populate_citations(
    *,
    repo: "Neo4jRepository",
    s2_client: "SemanticScholarClient",
    paper_doi: str,
    paper_s2_id: Optional[str] = None,
    limit: int = 200,
) -> CitationPopulationResult:
    """Fetch ``paper_doi``'s references from Semantic Scholar and create
    stubs + ``CITES`` edges.

    Args:
        repo: Neo4j repository.
        s2_client: Initialized Semantic Scholar client.
        paper_doi: Source paper DOI (must already exist as a Paper node).
        paper_s2_id: Optional Semantic Scholar paper id (preferred — avoids
            a redundant lookup). If not provided, the helper resolves the
            id via ``s2_client.get_paper_by_doi``.
        limit: Max references to process per call (Semantic Scholar's
            pagination cap is 1000).

    Returns:
        ``CitationPopulationResult`` carrying counts and failure flags.
        Never raises — every failure is logged WARN and reported in the
        result.
    """
    result = CitationPopulationResult()

    # 1. Resolve the Semantic Scholar paper id if not provided.
    if not paper_s2_id:
        try:
            s2_response = await s2_client.get_paper_by_doi(paper_doi)
            paper_s2_id = (s2_response or {}).get("paperId")
        except Exception as e:
            logger.warning(
                "Citation populate: failed to look up s2 id for %s: %s",
                paper_doi, e,
            )
            result.errors.append(f"s2_id_lookup_failed: {e}")
            result.skipped_no_s2_id = True
            return result

    if not paper_s2_id:
        logger.info(
            "Citation populate: no Semantic Scholar id for %s; skipping",
            paper_doi,
        )
        result.skipped_no_s2_id = True
        return result

    # 2. Fetch the reference list.
    try:
        response = await s2_client.get_paper_references(
            paper_s2_id, limit=limit,
        )
    except Exception as e:
        logger.warning(
            "Citation populate: get_paper_references failed for %s: %s",
            paper_doi, e,
        )
        result.errors.append(f"fetch_failed: {e}")
        result.fetch_failed = True
        return result

    # 3. For each reference, create-or-promote a stub + link CITES.
    references = (response or {}).get("data") or []
    for ref in references:
        cited = (ref or {}).get("citedPaper") or {}
        ref_doi = _extract_doi(cited.get("externalIds"))

        if not ref_doi:
            # Spec Q4: no-DOI references are dropped (no reliable identifier).
            result.skipped_no_doi += 1
            continue

        title = cited.get("title") or "(untitled)"
        year = cited.get("year")

        try:
            _, created = repo.create_or_promote_paper_stub(
                doi=ref_doi, title=title, year=year,
            )
            if created:
                result.stubs_created += 1
        except Exception as e:
            logger.warning(
                "Citation populate: stub create failed for %s: %s",
                ref_doi, e,
            )
            result.errors.append(f"stub_failed[{ref_doi}]: {e}")
            continue

        try:
            edge_created = repo.link_paper_cites_paper(
                source_doi=paper_doi, target_doi=ref_doi,
            )
            if edge_created:
                result.edges_created += 1
        except NotFoundError as e:
            logger.warning(
                "Citation populate: CITES link failed for %s -> %s: %s",
                paper_doi, ref_doi, e,
            )
            result.errors.append(f"link_failed[{ref_doi}]: {e}")

    logger.info(
        "Citation populate complete for %s: stubs=%d, edges=%d, "
        "skipped_no_doi=%d, errors=%d",
        paper_doi,
        result.stubs_created,
        result.edges_created,
        result.skipped_no_doi,
        len(result.errors),
    )
    return result
