"""
Knowledge Graph importer for Data Acquisition.

Imports papers from external sources into the Knowledge Graph.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agentic_kg.data_acquisition.aggregator import (
    AggregatedResult,
    PaperAggregator,
    get_paper_aggregator,
)
from agentic_kg.data_acquisition.exceptions import NotFoundError
from agentic_kg.data_acquisition.normalizer import NormalizedPaper
from agentic_kg.knowledge_graph.models import Author, Paper
from agentic_kg.knowledge_graph.repository import (
    DuplicateError,
    Neo4jRepository,
    get_repository,
)

logger = logging.getLogger(__name__)


@dataclass
class ImportResult:
    """Result of a paper import operation."""

    paper: Paper | None = None
    created: bool = False
    updated: bool = False
    skipped: bool = False
    error: str | None = None
    sources: list[str] = field(default_factory=list)


@dataclass
class BatchImportResult:
    """Result of a batch import operation."""

    total: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    results: list[ImportResult] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total": self.total,
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "failed": self.failed,
            "errors": self.errors,
        }


def normalized_to_kg_paper(normalized: NormalizedPaper) -> Paper:
    """
    Convert a normalized paper to a Knowledge Graph Paper model.

    Args:
        normalized: Normalized paper from data acquisition

    Returns:
        Knowledge Graph Paper model
    """
    # DOI is required for KG Paper
    if not normalized.doi:
        raise ValueError("Paper must have a DOI for Knowledge Graph import")

    # Build author names list
    author_names = [a.name for a in normalized.authors]

    # Get external IDs
    arxiv_id = normalized.external_ids.get("arxiv")
    openalex_id = normalized.external_ids.get("openalex")
    semantic_scholar_id = normalized.external_ids.get("semantic_scholar")

    return Paper(
        doi=normalized.doi,
        title=normalized.title,
        authors=author_names,
        venue=normalized.venue,
        year=normalized.year or datetime.now().year,
        abstract=normalized.abstract,
        arxiv_id=arxiv_id,
        openalex_id=openalex_id,
        semantic_scholar_id=semantic_scholar_id,
        pdf_url=normalized.pdf_url,
    )


def normalized_to_kg_author(
    normalized_author: Any,
    position: int,
) -> Author:
    """
    Convert a normalized author to a Knowledge Graph Author model.

    Args:
        normalized_author: Normalized author from data acquisition
        position: Author position (1-indexed)

    Returns:
        Knowledge Graph Author model
    """
    return Author(
        name=normalized_author.name,
        affiliations=normalized_author.affiliations,
        orcid=normalized_author.external_ids.get("orcid"),
        semantic_scholar_id=normalized_author.external_ids.get("semantic_scholar"),
    )


class PaperImporter:
    """
    Imports papers from external sources into the Knowledge Graph.

    Features:
    - Single paper import by identifier
    - Batch import from identifier list
    - Author bibliography import
    - Duplicate handling (skip or update)
    - Author entity creation and linking
    """

    def __init__(
        self,
        aggregator: PaperAggregator | None = None,
        repository: Neo4jRepository | None = None,
    ):
        """
        Initialize the importer.

        Args:
            aggregator: Paper aggregator for fetching data
            repository: Knowledge Graph repository for storage
        """
        self._aggregator = aggregator
        self._repository = repository

    @property
    def aggregator(self) -> PaperAggregator:
        """Get paper aggregator (lazy init)."""
        if self._aggregator is None:
            self._aggregator = get_paper_aggregator()
        return self._aggregator

    @property
    def repository(self) -> Neo4jRepository:
        """Get Knowledge Graph repository (lazy init)."""
        if self._repository is None:
            self._repository = get_repository()
        return self._repository

    async def import_paper(
        self,
        identifier: str,
        sources: list[str] | None = None,
        update_existing: bool = False,
        create_authors: bool = True,
    ) -> ImportResult:
        """
        Import a paper by identifier.

        Args:
            identifier: Paper identifier (DOI, arXiv ID, etc.)
            sources: Sources to fetch from (None = auto-detect)
            update_existing: Whether to update existing papers
            create_authors: Whether to create Author entities

        Returns:
            Import result
        """
        try:
            # Fetch paper from external sources
            aggregated = await self.aggregator.get_paper(
                identifier, sources=sources, merge=True
            )
            normalized = aggregated.paper

            # DOI is required for KG import
            if not normalized.doi:
                return ImportResult(
                    error="Paper has no DOI, cannot import to Knowledge Graph",
                    sources=aggregated.sources,
                )

            # Check if paper already exists
            existing = self.repository.get_paper(normalized.doi)

            if existing:
                if update_existing:
                    # Update existing paper
                    kg_paper = normalized_to_kg_paper(normalized)
                    updated = self.repository.update_paper(kg_paper)
                    return ImportResult(
                        paper=updated,
                        updated=True,
                        sources=aggregated.sources,
                    )
                else:
                    return ImportResult(
                        paper=existing,
                        skipped=True,
                        sources=aggregated.sources,
                    )

            # Create new paper
            kg_paper = normalized_to_kg_paper(normalized)
            created_paper = self.repository.create_paper(kg_paper)

            # Create authors and link to paper
            if create_authors and normalized.authors:
                await self._create_and_link_authors(
                    created_paper.doi, normalized.authors
                )

            return ImportResult(
                paper=created_paper,
                created=True,
                sources=aggregated.sources,
            )

        except NotFoundError as e:
            return ImportResult(error=f"Not found: {str(e)}")
        except DuplicateError as e:
            return ImportResult(error=f"Duplicate: {str(e)}", skipped=True)
        except Exception as e:
            logger.exception("Error importing paper %s", identifier)
            return ImportResult(error=str(e))

    async def _create_and_link_authors(
        self,
        paper_doi: str,
        authors: list[Any],
    ) -> None:
        """
        Create author entities and link to paper.

        Args:
            paper_doi: Paper DOI
            authors: List of normalized authors
        """
        for i, normalized_author in enumerate(authors):
            try:
                # Check if author exists by Semantic Scholar ID or ORCID
                existing_author = None
                ss_id = normalized_author.external_ids.get("semantic_scholar")
                orcid = normalized_author.external_ids.get("orcid")

                if ss_id:
                    existing_author = self._find_author_by_external_id(
                        "semantic_scholar_id", ss_id
                    )
                if not existing_author and orcid:
                    existing_author = self._find_author_by_external_id("orcid", orcid)

                if existing_author:
                    author_id = existing_author.id
                else:
                    # Create new author
                    kg_author = normalized_to_kg_author(normalized_author, i + 1)
                    created = self.repository.create_author(kg_author)
                    author_id = created.id

                # Link author to paper
                self.repository.link_paper_to_author(paper_doi, author_id, i + 1)

            except Exception as e:
                logger.warning(
                    "Failed to create/link author %s for paper %s: %s",
                    normalized_author.name,
                    paper_doi,
                    str(e),
                )

    def _find_author_by_external_id(
        self, field: str, value: str
    ) -> Author | None:
        """Find author by external ID field."""
        try:
            # This would require a custom query in the repository
            # For now, return None to always create new authors
            return None
        except Exception:
            return None

    async def batch_import(
        self,
        identifiers: list[str],
        sources: list[str] | None = None,
        update_existing: bool = False,
        create_authors: bool = True,
        progress_callback: Any | None = None,
    ) -> BatchImportResult:
        """
        Import multiple papers by identifier.

        Args:
            identifiers: List of paper identifiers
            sources: Sources to fetch from
            update_existing: Whether to update existing papers
            create_authors: Whether to create Author entities
            progress_callback: Optional callback for progress updates

        Returns:
            Batch import result
        """
        result = BatchImportResult(total=len(identifiers))

        for i, identifier in enumerate(identifiers):
            import_result = await self.import_paper(
                identifier,
                sources=sources,
                update_existing=update_existing,
                create_authors=create_authors,
            )

            result.results.append(import_result)

            if import_result.created:
                result.created += 1
            elif import_result.updated:
                result.updated += 1
            elif import_result.skipped:
                result.skipped += 1
            else:
                result.failed += 1
                if import_result.error:
                    result.errors[identifier] = import_result.error

            # Progress callback
            if progress_callback:
                progress_callback(i + 1, len(identifiers), import_result)

        return result

    async def import_author_papers(
        self,
        author_id: str,
        source: str = "semantic_scholar",
        limit: int = 100,
        update_existing: bool = False,
    ) -> BatchImportResult:
        """
        Import all papers by an author.

        Args:
            author_id: Author ID (source-specific)
            source: Source to fetch from
            limit: Maximum papers to import
            update_existing: Whether to update existing papers

        Returns:
            Batch import result
        """
        try:
            if source == "semantic_scholar":
                papers_data = await self.aggregator.semantic_scholar.get_author_papers(
                    author_id, limit=limit
                )
                papers = papers_data.get("data", [])

                # Extract DOIs and import
                identifiers = []
                for paper in papers:
                    external_ids = paper.get("externalIds", {})
                    if external_ids.get("DOI"):
                        identifiers.append(external_ids["DOI"])

                return await self.batch_import(
                    identifiers,
                    sources=["semantic_scholar"],
                    update_existing=update_existing,
                )

            elif source == "openalex":
                papers_data = await self.aggregator.openalex.get_author_works(
                    author_id, per_page=min(limit, 200)
                )
                papers = papers_data.get("data", [])

                # Extract DOIs and import
                identifiers = []
                for paper in papers:
                    doi = paper.get("doi")
                    if doi:
                        # Clean DOI URL
                        if doi.startswith("https://doi.org/"):
                            doi = doi.replace("https://doi.org/", "")
                        identifiers.append(doi)

                return await self.batch_import(
                    identifiers,
                    sources=["openalex"],
                    update_existing=update_existing,
                )

            else:
                return BatchImportResult(
                    errors={"source": f"Unsupported source: {source}"}
                )

        except Exception as e:
            logger.exception("Error importing author papers for %s", author_id)
            return BatchImportResult(errors={"import": str(e)})


# Singleton instance
_importer: PaperImporter | None = None


def get_paper_importer() -> PaperImporter:
    """Get the paper importer singleton."""
    global _importer
    if _importer is None:
        _importer = PaperImporter()
    return _importer


def reset_paper_importer() -> None:
    """Reset the importer singleton (useful for testing)."""
    global _importer
    _importer = None


__all__ = [
    "ImportResult",
    "BatchImportResult",
    "PaperImporter",
    "normalized_to_kg_paper",
    "normalized_to_kg_author",
    "get_paper_importer",
    "reset_paper_importer",
]
