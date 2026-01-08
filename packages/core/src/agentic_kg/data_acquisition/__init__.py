"""
Data Acquisition module for Agentic KG.

Provides unified paper acquisition from multiple sources:
- Semantic Scholar API for metadata, citations, and SPECTER2 embeddings
- arXiv for open access papers and preprints
- OpenAlex for open access discovery and metadata
- Paywall integration for restricted papers (optional)

Example usage:
    from agentic_kg.data_acquisition import PaperAcquisitionLayer

    acquisition = PaperAcquisitionLayer()

    # Get paper by DOI
    metadata = await acquisition.get_paper_metadata("10.1038/nature12373")

    # Get paper by arXiv ID
    metadata = await acquisition.get_paper_metadata("2301.12345")

    # Download PDF
    pdf_path = await acquisition.get_pdf_path("10.1038/nature12373")
"""

from agentic_kg.data_acquisition.models import (
    AuthorRef,
    Citation,
    DownloadResult,
    DownloadStatus,
    PaperMetadata,
    SourceType,
    is_valid_arxiv_id,
    is_valid_doi,
)

# Clients will be added in Tasks 3-5
# from agentic_kg.data_acquisition.semantic_scholar import SemanticScholarClient
# from agentic_kg.data_acquisition.arxiv import ArxivClient
# from agentic_kg.data_acquisition.openalex import OpenAlexClient

# Acquisition layer will be added in Task 6
# from agentic_kg.data_acquisition.acquisition import PaperAcquisitionLayer

__all__ = [
    # Models
    "AuthorRef",
    "Citation",
    "DownloadResult",
    "DownloadStatus",
    "PaperMetadata",
    "SourceType",
    # Validators
    "is_valid_arxiv_id",
    "is_valid_doi",
    # Clients (Tasks 3-5)
    # "SemanticScholarClient",
    # "ArxivClient",
    # "OpenAlexClient",
    # Acquisition layer (Task 6)
    # "PaperAcquisitionLayer",
]
