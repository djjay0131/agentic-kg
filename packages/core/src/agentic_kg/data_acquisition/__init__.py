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

# Clients
from agentic_kg.data_acquisition.semantic_scholar import (
    NotFoundError,
    RateLimitError,
    SemanticScholarClient,
    SemanticScholarError,
    get_semantic_scholar_client,
    reset_semantic_scholar_client,
)

from agentic_kg.data_acquisition.arxiv import (
    ArxivClient,
    ArxivError,
    ArxivNotFoundError,
    ArxivRateLimitError,
    get_arxiv_client,
    normalize_arxiv_id,
    parse_arxiv_id,
    reset_arxiv_client,
)

from agentic_kg.data_acquisition.openalex import (
    OpenAlexClient,
    OpenAlexError,
    OpenAlexNotFoundError,
    OpenAlexRateLimitError,
    get_openalex_client,
    reset_openalex_client,
)

# Unified Acquisition Layer
from agentic_kg.data_acquisition.acquisition import (
    IdentifierType,
    PaperAcquisitionLayer,
    clean_identifier,
    detect_identifier_type,
    get_acquisition_layer,
    reset_acquisition_layer,
)

# Caching
from agentic_kg.data_acquisition.cache import (
    PaperCache,
    get_paper_cache,
    reset_paper_cache,
)

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
    # Semantic Scholar Client
    "NotFoundError",
    "RateLimitError",
    "SemanticScholarClient",
    "SemanticScholarError",
    "get_semantic_scholar_client",
    "reset_semantic_scholar_client",
    # arXiv Client
    "ArxivClient",
    "ArxivError",
    "ArxivNotFoundError",
    "ArxivRateLimitError",
    "get_arxiv_client",
    "normalize_arxiv_id",
    "parse_arxiv_id",
    "reset_arxiv_client",
    # OpenAlex Client
    "OpenAlexClient",
    "OpenAlexError",
    "OpenAlexNotFoundError",
    "OpenAlexRateLimitError",
    "get_openalex_client",
    "reset_openalex_client",
    # Unified Acquisition Layer
    "IdentifierType",
    "PaperAcquisitionLayer",
    "clean_identifier",
    "detect_identifier_type",
    "get_acquisition_layer",
    "reset_acquisition_layer",
    # Caching
    "PaperCache",
    "get_paper_cache",
    "reset_paper_cache",
]
