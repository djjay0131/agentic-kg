"""Tests for data acquisition models."""

import pytest
from datetime import datetime

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


class TestDOIValidation:
    """Tests for DOI validation."""

    def test_valid_doi_simple(self):
        assert is_valid_doi("10.1038/nature12373") is True

    def test_valid_doi_with_slash(self):
        assert is_valid_doi("10.1145/3292500.3330649") is True

    def test_valid_doi_with_special_chars(self):
        assert is_valid_doi("10.1007/978-3-030-58452-8_24") is True

    def test_invalid_doi_no_prefix(self):
        assert is_valid_doi("1038/nature12373") is False

    def test_invalid_doi_wrong_format(self):
        assert is_valid_doi("doi:10.1038/nature12373") is False

    def test_invalid_doi_empty(self):
        assert is_valid_doi("") is False


class TestArxivIDValidation:
    """Tests for arXiv ID validation."""

    def test_valid_new_format(self):
        assert is_valid_arxiv_id("2301.12345") is True

    def test_valid_new_format_with_version(self):
        assert is_valid_arxiv_id("2301.12345v2") is True

    def test_valid_new_format_five_digits(self):
        assert is_valid_arxiv_id("2301.00001") is True

    def test_valid_old_format(self):
        assert is_valid_arxiv_id("cs.AI/0501001") is True

    def test_valid_old_format_with_version(self):
        assert is_valid_arxiv_id("hep-th/9901001v3") is True

    def test_invalid_arxiv_wrong_format(self):
        assert is_valid_arxiv_id("arxiv:2301.12345") is False

    def test_invalid_arxiv_empty(self):
        assert is_valid_arxiv_id("") is False


class TestAuthorRef:
    """Tests for AuthorRef model."""

    def test_create_minimal(self):
        author = AuthorRef(name="John Doe")
        assert author.name == "John Doe"
        assert author.author_id is None
        assert author.orcid is None
        assert author.affiliations == []

    def test_create_full(self):
        author = AuthorRef(
            name="Jane Smith",
            author_id="12345",
            orcid="0000-0002-1234-5678",
            affiliations=["MIT", "Stanford"],
        )
        assert author.name == "Jane Smith"
        assert author.author_id == "12345"
        assert author.affiliations == ["MIT", "Stanford"]

    def test_json_safe(self):
        author = AuthorRef(name="Test Author")
        data = author.model_dump_json_safe()
        assert data["name"] == "Test Author"
        assert isinstance(data, dict)


class TestCitation:
    """Tests for Citation model."""

    def test_create_minimal(self):
        citation = Citation(paper_id="abc123")
        assert citation.paper_id == "abc123"
        assert citation.doi is None
        assert citation.is_influential is False

    def test_create_full(self):
        citation = Citation(
            paper_id="abc123",
            doi="10.1038/nature12373",
            title="Test Paper",
            year=2023,
            is_influential=True,
        )
        assert citation.doi == "10.1038/nature12373"
        assert citation.is_influential is True

    def test_invalid_doi_cleaned(self):
        citation = Citation(paper_id="abc123", doi="invalid-doi")
        assert citation.doi is None  # Invalid DOI should be cleaned


class TestPaperMetadata:
    """Tests for PaperMetadata model."""

    def test_create_minimal(self):
        paper = PaperMetadata(paper_id="test123", title="Test Paper")
        assert paper.paper_id == "test123"
        assert paper.title == "Test Paper"
        assert paper.source == SourceType.UNKNOWN
        assert paper.authors == []

    def test_create_full(self):
        paper = PaperMetadata(
            paper_id="test123",
            doi="10.1038/nature12373",
            arxiv_id="2301.12345",
            title="Attention Is All You Need",
            abstract="We propose a new architecture...",
            authors=[AuthorRef(name="Vaswani et al.")],
            year=2017,
            venue="NeurIPS",
            source=SourceType.SEMANTIC_SCHOLAR,
            citation_count=50000,
            is_open_access=True,
        )
        assert paper.doi == "10.1038/nature12373"
        assert paper.arxiv_id == "2301.12345"
        assert paper.citation_count == 50000

    def test_doi_cleaning_https_prefix(self):
        paper = PaperMetadata(
            paper_id="test",
            title="Test",
            doi="https://doi.org/10.1038/nature12373",
        )
        assert paper.doi == "10.1038/nature12373"

    def test_doi_cleaning_http_prefix(self):
        paper = PaperMetadata(
            paper_id="test",
            title="Test",
            doi="http://doi.org/10.1038/nature12373",
        )
        assert paper.doi == "10.1038/nature12373"

    def test_doi_cleaning_doi_prefix(self):
        paper = PaperMetadata(
            paper_id="test",
            title="Test",
            doi="doi:10.1038/nature12373",
        )
        assert paper.doi == "10.1038/nature12373"

    def test_invalid_doi_cleaned(self):
        paper = PaperMetadata(
            paper_id="test",
            title="Test",
            doi="not-a-valid-doi",
        )
        assert paper.doi is None

    def test_arxiv_id_cleaning(self):
        paper = PaperMetadata(
            paper_id="test",
            title="Test",
            arxiv_id="arXiv:2301.12345",
        )
        assert paper.arxiv_id == "2301.12345"

    def test_invalid_arxiv_cleaned(self):
        paper = PaperMetadata(
            paper_id="test",
            title="Test",
            arxiv_id="not-valid",
        )
        assert paper.arxiv_id is None

    def test_has_pdf_true(self):
        paper = PaperMetadata(
            paper_id="test",
            title="Test",
            pdf_url="https://arxiv.org/pdf/2301.12345.pdf",
        )
        assert paper.has_pdf() is True

    def test_has_pdf_false(self):
        paper = PaperMetadata(paper_id="test", title="Test")
        assert paper.has_pdf() is False

    def test_get_best_identifier_doi(self):
        paper = PaperMetadata(
            paper_id="test",
            title="Test",
            doi="10.1038/nature12373",
            arxiv_id="2301.12345",
        )
        assert paper.get_best_identifier() == "doi:10.1038/nature12373"

    def test_get_best_identifier_arxiv(self):
        paper = PaperMetadata(
            paper_id="test",
            title="Test",
            arxiv_id="2301.12345",
        )
        assert paper.get_best_identifier() == "arxiv:2301.12345"

    def test_get_best_identifier_s2(self):
        paper = PaperMetadata(
            paper_id="test",
            title="Test",
            s2_id="abc123",
        )
        assert paper.get_best_identifier() == "s2:abc123"

    def test_get_best_identifier_fallback(self):
        paper = PaperMetadata(paper_id="test", title="Test")
        assert paper.get_best_identifier() == "test"

    def test_json_safe(self):
        paper = PaperMetadata(
            paper_id="test",
            title="Test",
            retrieved_at=datetime(2024, 1, 15, 12, 0, 0),
        )
        data = paper.model_dump_json_safe()
        assert data["retrieved_at"] == "2024-01-15T12:00:00"


class TestDownloadResult:
    """Tests for DownloadResult model."""

    def test_create_success(self):
        result = DownloadResult(
            identifier="10.1038/nature12373",
            status=DownloadStatus.COMPLETED,
            source=SourceType.ARXIV,
            file_path="/cache/papers/abc123.pdf",
            file_size=1024000,
            content_hash="sha256:abc123...",
        )
        assert result.is_success() is True

    def test_create_failed(self):
        result = DownloadResult(
            identifier="10.1038/nature12373",
            status=DownloadStatus.FAILED,
            source=SourceType.ARXIV,
            error_message="Connection timeout",
        )
        assert result.is_success() is False
        assert result.error_message == "Connection timeout"

    def test_create_pending(self):
        result = DownloadResult(
            identifier="10.1038/nature12373",
            status=DownloadStatus.PENDING,
            source=SourceType.PAYWALL,
        )
        assert result.is_success() is False


class TestSourceType:
    """Tests for SourceType enum."""

    def test_all_sources(self):
        assert SourceType.SEMANTIC_SCHOLAR == "semantic_scholar"
        assert SourceType.ARXIV == "arxiv"
        assert SourceType.OPENALEX == "openalex"
        assert SourceType.PAYWALL == "paywall"
        assert SourceType.CACHE == "cache"
        assert SourceType.UNKNOWN == "unknown"


class TestDownloadStatus:
    """Tests for DownloadStatus enum."""

    def test_all_statuses(self):
        assert DownloadStatus.PENDING == "pending"
        assert DownloadStatus.IN_PROGRESS == "in_progress"
        assert DownloadStatus.COMPLETED == "completed"
        assert DownloadStatus.FAILED == "failed"
        assert DownloadStatus.NOT_AVAILABLE == "not_available"
