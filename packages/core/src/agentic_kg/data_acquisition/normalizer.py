"""
Paper metadata normalization for Data Acquisition.

Normalizes paper metadata from Semantic Scholar, arXiv, and OpenAlex
into a unified schema compatible with the Knowledge Graph Paper model.
"""
from __future__ import annotations


import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from agentic_kg.data_acquisition.exceptions import NormalizationError

logger = logging.getLogger(__name__)


@dataclass
class NormalizedAuthor:
    """Normalized author representation."""

    name: str
    external_ids: dict[str, str] = field(default_factory=dict)
    affiliations: list[str] = field(default_factory=list)
    position: int | None = None  # Author position (1 = first author)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "external_ids": self.external_ids,
            "affiliations": self.affiliations,
            "position": self.position,
        }


@dataclass
class NormalizedPaper:
    """
    Normalized paper representation.

    Compatible with the Knowledge Graph Paper model.
    """

    # Required fields
    title: str
    source: str  # "semantic_scholar", "arxiv", "openalex"

    # Identifiers
    doi: str | None = None
    external_ids: dict[str, str] = field(default_factory=dict)

    # Content
    abstract: str | None = None
    year: int | None = None
    publication_date: str | None = None  # ISO format
    venue: str | None = None

    # Authors
    authors: list[NormalizedAuthor] = field(default_factory=list)

    # Metrics
    citation_count: int | None = None
    reference_count: int | None = None

    # Classification
    fields_of_study: list[str] = field(default_factory=list)
    publication_types: list[str] = field(default_factory=list)

    # Access
    is_open_access: bool = False
    pdf_url: str | None = None
    abstract_url: str | None = None

    # Source-specific metadata (for reference)
    raw_data: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for Knowledge Graph."""
        return {
            "title": self.title,
            "doi": self.doi,
            "external_ids": self.external_ids,
            "abstract": self.abstract,
            "year": self.year,
            "publication_date": self.publication_date,
            "venue": self.venue,
            "authors": [a.to_dict() for a in self.authors],
            "citation_count": self.citation_count,
            "reference_count": self.reference_count,
            "fields_of_study": self.fields_of_study,
            "publication_types": self.publication_types,
            "is_open_access": self.is_open_access,
            "pdf_url": self.pdf_url,
            "abstract_url": self.abstract_url,
            "source": self.source,
        }


class PaperNormalizer:
    """
    Normalizes paper metadata from different sources.

    Handles:
    - Field mapping from source-specific schemas
    - Data type conversion
    - Missing field handling
    - Author normalization
    """

    def normalize(
        self,
        data: dict[str, Any],
        source: str,
        keep_raw: bool = False,
    ) -> NormalizedPaper:
        """
        Normalize paper data from any supported source.

        Args:
            data: Raw paper data from API
            source: Source identifier ("semantic_scholar", "arxiv", "openalex")
            keep_raw: Whether to include raw data in result

        Returns:
            Normalized paper

        Raises:
            NormalizationError: If normalization fails
        """
        try:
            if source == "semantic_scholar":
                paper = self.normalize_semantic_scholar(data)
            elif source == "arxiv":
                paper = self.normalize_arxiv(data)
            elif source == "openalex":
                paper = self.normalize_openalex(data)
            else:
                raise NormalizationError(
                    f"Unknown source: {source}",
                    source=source,
                    raw_data=data,
                )

            if keep_raw:
                paper.raw_data = data

            return paper

        except NormalizationError:
            raise
        except Exception as e:
            raise NormalizationError(
                f"Failed to normalize: {str(e)}",
                source=source,
                raw_data=data,
            ) from e

    def normalize_semantic_scholar(self, data: dict[str, Any]) -> NormalizedPaper:
        """
        Normalize Semantic Scholar paper data.

        Expected fields:
        - paperId, externalIds, title, abstract, year, venue
        - authors, citationCount, referenceCount
        - fieldsOfStudy, publicationTypes
        - isOpenAccess, openAccessPdf
        """
        # Extract identifiers
        external_ids: dict[str, str] = {}
        ids = data.get("externalIds") or {}

        if data.get("paperId"):
            external_ids["semantic_scholar"] = data["paperId"]
        if ids.get("DOI"):
            external_ids["doi"] = ids["DOI"]
        if ids.get("ArXiv"):
            external_ids["arxiv"] = ids["ArXiv"]
        if ids.get("MAG"):
            external_ids["mag"] = str(ids["MAG"])
        if ids.get("PubMed"):
            external_ids["pubmed"] = str(ids["PubMed"])

        # Extract DOI
        doi = ids.get("DOI")

        # Extract authors
        authors = []
        for i, author_data in enumerate(data.get("authors") or []):
            author = NormalizedAuthor(
                name=author_data.get("name", "Unknown"),
                position=i + 1,
            )
            if author_data.get("authorId"):
                author.external_ids["semantic_scholar"] = author_data["authorId"]
            authors.append(author)

        # Extract PDF URL
        pdf_url = None
        oa_pdf = data.get("openAccessPdf")
        if oa_pdf and isinstance(oa_pdf, dict):
            pdf_url = oa_pdf.get("url")

        return NormalizedPaper(
            title=data.get("title", ""),
            source="semantic_scholar",
            doi=doi,
            external_ids=external_ids,
            abstract=data.get("abstract"),
            year=data.get("year"),
            publication_date=data.get("publicationDate"),
            venue=data.get("venue"),
            authors=authors,
            citation_count=data.get("citationCount"),
            reference_count=data.get("referenceCount"),
            fields_of_study=data.get("fieldsOfStudy") or [],
            publication_types=data.get("publicationTypes") or [],
            is_open_access=data.get("isOpenAccess", False),
            pdf_url=pdf_url,
        )

    def normalize_arxiv(self, data: dict[str, Any]) -> NormalizedPaper:
        """
        Normalize arXiv paper data.

        Expected fields:
        - id, title, summary (abstract), authors
        - published, updated, categories, primary_category
        - doi, comment, journal_ref
        - pdf_url, abs_url
        """
        # Extract identifiers
        arxiv_id = data.get("id", "")
        external_ids: dict[str, str] = {"arxiv": arxiv_id}

        doi = data.get("doi")
        if doi:
            external_ids["doi"] = doi

        # Parse publication year from date
        year = None
        published = data.get("published", "")
        if published:
            try:
                # Format: 2021-06-02T17:59:59Z
                dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                year = dt.year
            except (ValueError, TypeError):
                # Try extracting year from ID (e.g., "2106.01345")
                match = re.match(r"(\d{2})(\d{2})\.", arxiv_id)
                if match:
                    century = 20 if int(match.group(1)) < 50 else 19
                    year = century * 100 + int(match.group(1))

        # Extract authors
        authors = []
        for i, author_data in enumerate(data.get("authors") or []):
            affiliations = []
            if author_data.get("affiliation"):
                affiliations = [author_data["affiliation"]]

            author = NormalizedAuthor(
                name=author_data.get("name", "Unknown"),
                affiliations=affiliations,
                position=i + 1,
            )
            authors.append(author)

        # Map categories to fields of study
        categories = data.get("categories") or []
        primary_category = data.get("primary_category")
        if primary_category and primary_category not in categories:
            categories = [primary_category] + categories

        # Determine venue from journal_ref if available
        venue = data.get("journal_ref")

        return NormalizedPaper(
            title=data.get("title", ""),
            source="arxiv",
            doi=doi,
            external_ids=external_ids,
            abstract=data.get("summary"),
            year=year,
            publication_date=published[:10] if published else None,  # Just the date part
            venue=venue,
            authors=authors,
            fields_of_study=categories,
            publication_types=["preprint"],
            is_open_access=True,  # arXiv is always open access
            pdf_url=data.get("pdf_url"),
            abstract_url=data.get("abs_url"),
        )

    def normalize_openalex(self, data: dict[str, Any]) -> NormalizedPaper:
        """
        Normalize OpenAlex work data.

        Expected fields:
        - id, doi, ids, title, abstract (reconstructed)
        - publication_year, publication_date
        - authorships, cited_by_count, referenced_works_count
        - concepts, topics, type
        - open_access, primary_location, best_oa_location
        """
        # Extract identifiers
        external_ids: dict[str, str] = {}

        openalex_id = data.get("id", "")
        if openalex_id:
            # Extract just the ID part (e.g., "W2741809807" from URL)
            if openalex_id.startswith("https://openalex.org/"):
                openalex_id = openalex_id.replace("https://openalex.org/", "")
            external_ids["openalex"] = openalex_id

        doi = data.get("doi")
        if doi:
            # Remove URL prefix if present
            if doi.startswith("https://doi.org/"):
                doi = doi.replace("https://doi.org/", "")
            external_ids["doi"] = doi

        # Extract other IDs
        ids = data.get("ids") or {}
        if ids.get("pmid"):
            external_ids["pubmed"] = str(ids["pmid"])
        if ids.get("mag"):
            external_ids["mag"] = str(ids["mag"])

        # Extract authors from authorships
        authors = []
        for authorship in data.get("authorships") or []:
            author_info = authorship.get("author") or {}

            # Extract affiliations
            affiliations = []
            for inst in authorship.get("institutions") or []:
                if inst.get("display_name"):
                    affiliations.append(inst["display_name"])

            author = NormalizedAuthor(
                name=author_info.get("display_name", "Unknown"),
                affiliations=affiliations,
                position=authorship.get("author_position"),
            )

            # Add author IDs
            author_id = author_info.get("id", "")
            if author_id:
                if author_id.startswith("https://openalex.org/"):
                    author_id = author_id.replace("https://openalex.org/", "")
                author.external_ids["openalex"] = author_id

            if author_info.get("orcid"):
                orcid = author_info["orcid"]
                if orcid.startswith("https://orcid.org/"):
                    orcid = orcid.replace("https://orcid.org/", "")
                author.external_ids["orcid"] = orcid

            authors.append(author)

        # Extract fields of study from concepts/topics
        fields_of_study = []
        for concept in data.get("concepts") or []:
            if concept.get("display_name"):
                fields_of_study.append(concept["display_name"])

        # Extract venue from primary location
        venue = None
        primary_location = data.get("primary_location") or {}
        source = primary_location.get("source") or {}
        if source.get("display_name"):
            venue = source["display_name"]

        # Extract open access info
        oa_info = data.get("open_access") or {}
        is_open_access = oa_info.get("is_oa", False)

        # Extract PDF URL
        pdf_url = None
        if oa_info.get("oa_url"):
            pdf_url = oa_info["oa_url"]
        elif data.get("best_oa_location"):
            best_oa = data["best_oa_location"]
            if best_oa.get("pdf_url"):
                pdf_url = best_oa["pdf_url"]

        # Publication type
        pub_type = data.get("type")
        publication_types = [pub_type] if pub_type else []

        return NormalizedPaper(
            title=data.get("title") or data.get("display_name", ""),
            source="openalex",
            doi=doi,
            external_ids=external_ids,
            abstract=data.get("abstract"),  # Already reconstructed by client
            year=data.get("publication_year"),
            publication_date=data.get("publication_date"),
            venue=venue,
            authors=authors,
            citation_count=data.get("cited_by_count"),
            reference_count=data.get("referenced_works_count"),
            fields_of_study=fields_of_study,
            publication_types=publication_types,
            is_open_access=is_open_access,
            pdf_url=pdf_url,
        )


def merge_normalized_papers(papers: list[NormalizedPaper]) -> NormalizedPaper:
    """
    Merge multiple normalized papers (from different sources) into one.

    Prioritizes data quality:
    - DOI: First available
    - Abstract: Longest available
    - Authors: From source with most author details
    - Metrics: Highest counts (assumes more recent data)

    Args:
        papers: List of normalized papers to merge

    Returns:
        Merged paper with best data from all sources
    """
    if not papers:
        raise ValueError("Cannot merge empty paper list")

    if len(papers) == 1:
        return papers[0]

    # Start with first paper as base
    merged = papers[0]

    # Merge external IDs from all sources
    all_ids: dict[str, str] = {}
    for paper in papers:
        all_ids.update(paper.external_ids)
    merged.external_ids = all_ids

    # DOI: prefer first available
    if not merged.doi:
        for paper in papers:
            if paper.doi:
                merged.doi = paper.doi
                break

    # Title: prefer longest (usually most complete)
    for paper in papers:
        if paper.title and len(paper.title) > len(merged.title or ""):
            merged.title = paper.title

    # Abstract: prefer longest
    for paper in papers:
        if paper.abstract and len(paper.abstract) > len(merged.abstract or ""):
            merged.abstract = paper.abstract

    # Year: prefer most recent data
    for paper in papers:
        if paper.year and (not merged.year or paper.year > merged.year):
            merged.year = paper.year

    # Publication date: prefer most specific
    for paper in papers:
        if paper.publication_date and (
            not merged.publication_date
            or len(paper.publication_date) > len(merged.publication_date)
        ):
            merged.publication_date = paper.publication_date

    # Venue: prefer non-empty
    if not merged.venue:
        for paper in papers:
            if paper.venue:
                merged.venue = paper.venue
                break

    # Authors: use source with most author details (affiliations, IDs)
    best_author_score = sum(
        len(a.affiliations) + len(a.external_ids) for a in merged.authors
    )
    for paper in papers:
        score = sum(len(a.affiliations) + len(a.external_ids) for a in paper.authors)
        if score > best_author_score:
            merged.authors = paper.authors
            best_author_score = score

    # Metrics: prefer higher counts (usually more recent/complete)
    for paper in papers:
        if paper.citation_count and (
            not merged.citation_count or paper.citation_count > merged.citation_count
        ):
            merged.citation_count = paper.citation_count
        if paper.reference_count and (
            not merged.reference_count or paper.reference_count > merged.reference_count
        ):
            merged.reference_count = paper.reference_count

    # Fields of study: merge unique values
    all_fields = set(merged.fields_of_study)
    for paper in papers:
        all_fields.update(paper.fields_of_study)
    merged.fields_of_study = list(all_fields)

    # Publication types: merge unique values
    all_types = set(merged.publication_types)
    for paper in papers:
        all_types.update(paper.publication_types)
    merged.publication_types = list(all_types)

    # Open access: true if any source says true
    merged.is_open_access = any(p.is_open_access for p in papers)

    # PDF URL: prefer first available
    if not merged.pdf_url:
        for paper in papers:
            if paper.pdf_url:
                merged.pdf_url = paper.pdf_url
                break

    # Source: indicate merged
    merged.source = "merged"

    return merged


# Singleton instance
_normalizer: PaperNormalizer | None = None


def get_paper_normalizer() -> PaperNormalizer:
    """Get the paper normalizer singleton."""
    global _normalizer
    if _normalizer is None:
        _normalizer = PaperNormalizer()
    return _normalizer


__all__ = [
    "NormalizedAuthor",
    "NormalizedPaper",
    "PaperNormalizer",
    "merge_normalized_papers",
    "get_paper_normalizer",
]
