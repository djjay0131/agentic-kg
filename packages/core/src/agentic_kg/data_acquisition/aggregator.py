"""
Multi-source paper aggregator for Data Acquisition.

Aggregates paper data from multiple sources (Semantic Scholar, arXiv, OpenAlex)
with deduplication and data merging.
"""
from __future__ import annotations


import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from agentic_kg.data_acquisition.arxiv import ArxivClient, get_arxiv_client
from agentic_kg.data_acquisition.exceptions import NotFoundError
from agentic_kg.data_acquisition.normalizer import (
    NormalizedPaper,
    PaperNormalizer,
    get_paper_normalizer,
    merge_normalized_papers,
)
from agentic_kg.data_acquisition.openalex import OpenAlexClient, get_openalex_client
from agentic_kg.data_acquisition.semantic_scholar import (
    SemanticScholarClient,
    get_semantic_scholar_client,
)

logger = logging.getLogger(__name__)

# Patterns for identifier detection
DOI_PATTERN = re.compile(r"^10\.\d{4,}/")
ARXIV_PATTERN = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$|^[a-z-]+/\d{7}$", re.IGNORECASE)
OPENALEX_PATTERN = re.compile(r"^W\d+$", re.IGNORECASE)
SEMANTIC_SCHOLAR_PATTERN = re.compile(r"^[a-f0-9]{40}$", re.IGNORECASE)


def detect_identifier_type(identifier: str) -> str | None:
    """
    Detect the type of paper identifier.

    Args:
        identifier: Paper identifier

    Returns:
        Identifier type ("doi", "arxiv", "openalex", "semantic_scholar") or None
    """
    # Clean up identifier
    identifier = identifier.strip()

    # Remove common prefixes
    if identifier.lower().startswith("doi:"):
        return "doi"
    if identifier.lower().startswith("arxiv:"):
        return "arxiv"
    if identifier.startswith("https://doi.org/"):
        return "doi"
    if identifier.startswith("https://arxiv.org/"):
        return "arxiv"
    if identifier.startswith("https://openalex.org/"):
        return "openalex"

    # Pattern matching
    if DOI_PATTERN.match(identifier):
        return "doi"
    if ARXIV_PATTERN.match(identifier):
        return "arxiv"
    if OPENALEX_PATTERN.match(identifier):
        return "openalex"
    if SEMANTIC_SCHOLAR_PATTERN.match(identifier):
        return "semantic_scholar"

    return None


@dataclass
class AggregatedResult:
    """Result from multi-source aggregation."""

    paper: NormalizedPaper
    sources: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


@dataclass
class SearchResult:
    """Result from multi-source search."""

    papers: list[NormalizedPaper]
    total_by_source: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


class PaperAggregator:
    """
    Aggregates paper data from multiple sources.

    Features:
    - Identifier-based lookup across sources
    - Multi-source search with deduplication
    - Automatic source selection based on identifier type
    - Data merging from multiple sources
    """

    def __init__(
        self,
        semantic_scholar_client: SemanticScholarClient | None = None,
        arxiv_client: ArxivClient | None = None,
        openalex_client: OpenAlexClient | None = None,
        normalizer: PaperNormalizer | None = None,
    ):
        """
        Initialize the aggregator.

        Args:
            semantic_scholar_client: Semantic Scholar client
            arxiv_client: arXiv client
            openalex_client: OpenAlex client
            normalizer: Paper normalizer
        """
        self._ss_client = semantic_scholar_client
        self._arxiv_client = arxiv_client
        self._openalex_client = openalex_client
        self._normalizer = normalizer or get_paper_normalizer()

    @property
    def semantic_scholar(self) -> SemanticScholarClient:
        """Get Semantic Scholar client (lazy init)."""
        if self._ss_client is None:
            self._ss_client = get_semantic_scholar_client()
        return self._ss_client

    @property
    def arxiv(self) -> ArxivClient:
        """Get arXiv client (lazy init)."""
        if self._arxiv_client is None:
            self._arxiv_client = get_arxiv_client()
        return self._arxiv_client

    @property
    def openalex(self) -> OpenAlexClient:
        """Get OpenAlex client (lazy init)."""
        if self._openalex_client is None:
            self._openalex_client = get_openalex_client()
        return self._openalex_client

    async def get_paper(
        self,
        identifier: str,
        sources: list[str] | None = None,
        merge: bool = True,
    ) -> AggregatedResult:
        """
        Get paper by identifier from one or more sources.

        Args:
            identifier: Paper identifier (DOI, arXiv ID, etc.)
            sources: Sources to query (None = auto-detect based on identifier)
            merge: Whether to merge results from multiple sources

        Returns:
            Aggregated result with paper and metadata

        Raises:
            NotFoundError: If paper not found in any source
        """
        # Auto-detect identifier type if sources not specified
        if sources is None:
            id_type = detect_identifier_type(identifier)
            if id_type == "doi":
                sources = ["semantic_scholar", "openalex"]
            elif id_type == "arxiv":
                sources = ["arxiv", "semantic_scholar"]
            elif id_type == "openalex":
                sources = ["openalex"]
            elif id_type == "semantic_scholar":
                sources = ["semantic_scholar"]
            else:
                # Try all sources for unknown identifiers
                sources = ["semantic_scholar", "openalex", "arxiv"]

        # Fetch from each source in parallel
        tasks = []
        source_names = []

        for source in sources:
            if source == "semantic_scholar":
                tasks.append(self._fetch_from_semantic_scholar(identifier))
                source_names.append("semantic_scholar")
            elif source == "arxiv":
                tasks.append(self._fetch_from_arxiv(identifier))
                source_names.append("arxiv")
            elif source == "openalex":
                tasks.append(self._fetch_from_openalex(identifier))
                source_names.append("openalex")

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect successful results and errors
        papers: list[NormalizedPaper] = []
        successful_sources: list[str] = []
        errors: dict[str, str] = {}

        for source, result in zip(source_names, results):
            if isinstance(result, Exception):
                if not isinstance(result, NotFoundError):
                    errors[source] = str(result)
                    logger.warning("Error fetching from %s: %s", source, str(result))
            elif result is not None:
                papers.append(result)
                successful_sources.append(source)

        if not papers:
            raise NotFoundError(
                resource_type="paper",
                identifier=identifier,
                source="aggregator",
            )

        # Merge or return first result
        if merge and len(papers) > 1:
            merged = merge_normalized_papers(papers)
            return AggregatedResult(
                paper=merged,
                sources=successful_sources,
                errors=errors,
            )
        else:
            return AggregatedResult(
                paper=papers[0],
                sources=successful_sources[:1],
                errors=errors,
            )

    async def _fetch_from_semantic_scholar(
        self, identifier: str
    ) -> NormalizedPaper | None:
        """Fetch paper from Semantic Scholar."""
        try:
            # Format identifier for Semantic Scholar
            id_type = detect_identifier_type(identifier)
            if id_type == "doi":
                formatted_id = f"DOI:{identifier}" if not identifier.upper().startswith("DOI:") else identifier
            elif id_type == "arxiv":
                formatted_id = f"ARXIV:{identifier}" if not identifier.upper().startswith("ARXIV:") else identifier
            else:
                formatted_id = identifier

            data = await self.semantic_scholar.get_paper(formatted_id)
            return self._normalizer.normalize(data, "semantic_scholar")
        except NotFoundError:
            return None

    async def _fetch_from_arxiv(self, identifier: str) -> NormalizedPaper | None:
        """Fetch paper from arXiv."""
        try:
            # Only works for arXiv IDs
            id_type = detect_identifier_type(identifier)
            if id_type != "arxiv":
                return None

            data = await self.arxiv.get_paper(identifier)
            return self._normalizer.normalize(data, "arxiv")
        except NotFoundError:
            return None

    async def _fetch_from_openalex(self, identifier: str) -> NormalizedPaper | None:
        """Fetch paper from OpenAlex."""
        try:
            data = await self.openalex.get_work(identifier)
            return self._normalizer.normalize(data, "openalex")
        except NotFoundError:
            return None

    async def search_papers(
        self,
        query: str,
        sources: list[str] | None = None,
        limit: int = 10,
        deduplicate: bool = True,
    ) -> SearchResult:
        """
        Search for papers across multiple sources.

        Args:
            query: Search query
            sources: Sources to search (None = all)
            limit: Maximum results per source
            deduplicate: Whether to deduplicate results by DOI

        Returns:
            Search result with papers and metadata
        """
        if sources is None:
            sources = ["semantic_scholar", "openalex", "arxiv"]

        # Search each source in parallel
        tasks = []
        source_names = []

        for source in sources:
            if source == "semantic_scholar":
                tasks.append(self._search_semantic_scholar(query, limit))
                source_names.append("semantic_scholar")
            elif source == "arxiv":
                tasks.append(self._search_arxiv(query, limit))
                source_names.append("arxiv")
            elif source == "openalex":
                tasks.append(self._search_openalex(query, limit))
                source_names.append("openalex")

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect results
        all_papers: list[NormalizedPaper] = []
        total_by_source: dict[str, int] = {}
        errors: dict[str, str] = {}

        for source, result in zip(source_names, results):
            if isinstance(result, Exception):
                errors[source] = str(result)
                logger.warning("Error searching %s: %s", source, str(result))
            elif result is not None:
                papers, total = result
                all_papers.extend(papers)
                total_by_source[source] = total

        # Deduplicate by DOI
        if deduplicate:
            all_papers = self._deduplicate_papers(all_papers)

        return SearchResult(
            papers=all_papers,
            total_by_source=total_by_source,
            errors=errors,
        )

    async def _search_semantic_scholar(
        self, query: str, limit: int
    ) -> tuple[list[NormalizedPaper], int]:
        """Search Semantic Scholar."""
        result = await self.semantic_scholar.search_papers(query, limit=limit)
        papers = [
            self._normalizer.normalize(p, "semantic_scholar")
            for p in result.get("data", [])
        ]
        return papers, result.get("total", len(papers))

    async def _search_arxiv(
        self, query: str, limit: int
    ) -> tuple[list[NormalizedPaper], int]:
        """Search arXiv."""
        result = await self.arxiv.search_papers(query, limit=limit)
        papers = [self._normalizer.normalize(p, "arxiv") for p in result.get("data", [])]
        return papers, result.get("total", len(papers))

    async def _search_openalex(
        self, query: str, limit: int
    ) -> tuple[list[NormalizedPaper], int]:
        """Search OpenAlex."""
        result = await self.openalex.search_works(query, per_page=limit)
        papers = [
            self._normalizer.normalize(p, "openalex") for p in result.get("data", [])
        ]
        return papers, result.get("total", len(papers))

    def _deduplicate_papers(
        self, papers: list[NormalizedPaper]
    ) -> list[NormalizedPaper]:
        """
        Deduplicate papers by DOI and merge data.

        Args:
            papers: List of papers to deduplicate

        Returns:
            Deduplicated list with merged data
        """
        # Group by DOI
        by_doi: dict[str, list[NormalizedPaper]] = {}
        no_doi: list[NormalizedPaper] = []

        for paper in papers:
            if paper.doi:
                if paper.doi not in by_doi:
                    by_doi[paper.doi] = []
                by_doi[paper.doi].append(paper)
            else:
                # Try to match by title similarity for papers without DOI
                no_doi.append(paper)

        # Merge papers with same DOI
        result: list[NormalizedPaper] = []
        for doi, doi_papers in by_doi.items():
            if len(doi_papers) == 1:
                result.append(doi_papers[0])
            else:
                merged = merge_normalized_papers(doi_papers)
                result.append(merged)

        # Add papers without DOI (could implement title matching here)
        result.extend(no_doi)

        return result

    async def get_paper_by_doi(self, doi: str, merge: bool = True) -> AggregatedResult:
        """
        Get paper by DOI from all sources.

        Args:
            doi: DOI (e.g., "10.1038/nature12373")
            merge: Whether to merge results

        Returns:
            Aggregated result
        """
        return await self.get_paper(
            doi, sources=["semantic_scholar", "openalex"], merge=merge
        )

    async def get_paper_by_arxiv(
        self, arxiv_id: str, merge: bool = True
    ) -> AggregatedResult:
        """
        Get paper by arXiv ID from all sources.

        Args:
            arxiv_id: arXiv ID (e.g., "2106.01345")
            merge: Whether to merge results

        Returns:
            Aggregated result
        """
        return await self.get_paper(
            arxiv_id, sources=["arxiv", "semantic_scholar"], merge=merge
        )


# Singleton instance
_aggregator: PaperAggregator | None = None


def get_paper_aggregator() -> PaperAggregator:
    """Get the paper aggregator singleton."""
    global _aggregator
    if _aggregator is None:
        _aggregator = PaperAggregator()
    return _aggregator


def reset_paper_aggregator() -> None:
    """Reset the aggregator singleton (useful for testing)."""
    global _aggregator
    _aggregator = None


__all__ = [
    "PaperAggregator",
    "AggregatedResult",
    "SearchResult",
    "detect_identifier_type",
    "get_paper_aggregator",
    "reset_paper_aggregator",
]
