"""
Unit tests for PDF text extraction.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from agentic_kg.extraction.pdf_extractor import (
    ExtractedPage,
    ExtractedText,
    PDFExtractionError,
    PDFExtractor,
    get_pdf_extractor,
    reset_pdf_extractor,
)


class TestExtractedPage:
    """Tests for ExtractedPage dataclass."""

    def test_create_page_with_text(self):
        """Test creating a page with text content."""
        page = ExtractedPage(page_number=1, text="Hello world")

        assert page.page_number == 1
        assert page.text == "Hello world"
        assert page.char_count == 11
        assert page.word_count == 2

    def test_create_page_empty_text(self):
        """Test creating a page with empty text."""
        page = ExtractedPage(page_number=1, text="")

        assert page.char_count == 0
        assert page.word_count == 0

    def test_create_page_with_counts(self):
        """Test creating a page with explicit counts."""
        page = ExtractedPage(
            page_number=1, text="Test", char_count=100, word_count=20
        )

        # Should use explicit values
        assert page.char_count == 100
        assert page.word_count == 20


class TestExtractedText:
    """Tests for ExtractedText dataclass."""

    def test_create_empty_document(self):
        """Test creating an empty extracted document."""
        doc = ExtractedText()

        assert doc.pages == []
        assert doc.total_pages == 0
        assert doc.is_scanned is False
        assert doc.full_text == ""
        assert doc.total_chars == 0
        assert doc.total_words == 0

    def test_full_text_property(self):
        """Test full_text combines all pages."""
        pages = [
            ExtractedPage(page_number=1, text="First page content."),
            ExtractedPage(page_number=2, text="Second page content."),
            ExtractedPage(page_number=3, text="Third page content."),
        ]
        doc = ExtractedText(pages=pages, total_pages=3)

        full_text = doc.full_text

        assert "First page content." in full_text
        assert "Second page content." in full_text
        assert "Third page content." in full_text
        assert full_text.count("\n\n") == 2  # Separator between pages

    def test_full_text_skips_empty_pages(self):
        """Test that empty pages are skipped in full_text."""
        pages = [
            ExtractedPage(page_number=1, text="Content"),
            ExtractedPage(page_number=2, text="   "),  # Whitespace only
            ExtractedPage(page_number=3, text="More content"),
        ]
        doc = ExtractedText(pages=pages, total_pages=3)

        full_text = doc.full_text

        assert "Content" in full_text
        assert "More content" in full_text
        assert full_text.count("\n\n") == 1  # Only one separator

    def test_total_chars_property(self):
        """Test total_chars sums all pages."""
        pages = [
            ExtractedPage(page_number=1, text="Hello"),  # 5 chars
            ExtractedPage(page_number=2, text="World"),  # 5 chars
        ]
        doc = ExtractedText(pages=pages)

        assert doc.total_chars == 10

    def test_total_words_property(self):
        """Test total_words sums all pages."""
        pages = [
            ExtractedPage(page_number=1, text="Hello world"),  # 2 words
            ExtractedPage(page_number=2, text="Goodbye cruel world"),  # 3 words
        ]
        doc = ExtractedText(pages=pages)

        assert doc.total_words == 5

    def test_metadata_stored(self):
        """Test that metadata is stored."""
        doc = ExtractedText(
            metadata={"title": "Test Paper", "author": "Test Author"}
        )

        assert doc.metadata["title"] == "Test Paper"
        assert doc.metadata["author"] == "Test Author"


class TestPDFExtractor:
    """Tests for PDFExtractor class."""

    @pytest.fixture
    def extractor(self):
        """Create a PDF extractor instance."""
        return PDFExtractor()

    def test_initialization_defaults(self, extractor):
        """Test extractor initializes with defaults."""
        assert extractor.remove_headers_footers is True
        assert extractor.dehyphenate is True
        assert extractor.normalize_unicode is True
        assert extractor.min_line_length == 3

    def test_initialization_custom(self):
        """Test extractor with custom settings."""
        extractor = PDFExtractor(
            remove_headers_footers=False,
            dehyphenate=False,
            normalize_unicode=False,
            min_line_length=5,
        )

        assert extractor.remove_headers_footers is False
        assert extractor.dehyphenate is False
        assert extractor.normalize_unicode is False
        assert extractor.min_line_length == 5

    def test_clean_text_empty(self, extractor):
        """Test cleaning empty text."""
        result = extractor._clean_text("")
        assert result == ""

        result = extractor._clean_text(None)
        assert result == ""

    def test_clean_text_normalizes_whitespace(self, extractor):
        """Test that whitespace is normalized."""
        text = "Hello    world\n\n\n\n\nNew paragraph"
        result = extractor._clean_text(text)

        assert "    " not in result  # Multiple spaces removed
        assert "\n\n\n" not in result  # Multiple newlines reduced

    def test_dehyphenate_rejoins_words(self, extractor):
        """Test dehyphenation rejoins hyphenated words."""
        text = "This is a hyphen-\nated word."
        result = extractor._dehyphenate(text)

        assert "hyphenated" in result
        assert "hyphen-\n" not in result

    def test_dehyphenate_preserves_compound_words(self, extractor):
        """Test that compound words with hyphens are preserved."""
        text = "This is a well-known fact.\nNew line here."
        result = extractor._dehyphenate(text)

        assert "well-known" in result

    def test_remove_headers_footers_page_numbers(self, extractor):
        """Test removal of page number headers/footers."""
        lines = ["Content line", "  42  ", "More content", "- 5 -"]
        result = extractor._remove_headers_footers(lines)

        assert "Content line" in result
        assert "More content" in result
        assert "  42  " not in result
        assert "- 5 -" not in result

    def test_remove_headers_footers_arxiv(self, extractor):
        """Test removal of arXiv headers."""
        lines = ["arXiv:2106.01234v1 [cs.CL] 1 Jun 2021", "Abstract text"]
        result = extractor._remove_headers_footers(lines)

        assert "Abstract text" in result
        assert len(result) == 1

    def test_remove_headers_footers_conference(self, extractor):
        """Test removal of conference headers."""
        lines = ["Proceedings of the 2023 EMNLP Conference", "Paper content"]
        result = extractor._remove_headers_footers(lines)

        assert "Paper content" in result
        assert len(result) == 1

    def test_extract_from_file_not_found(self, extractor):
        """Test error when file doesn't exist."""
        with pytest.raises(PDFExtractionError) as exc_info:
            extractor.extract_from_file("/nonexistent/path.pdf")

        assert "not found" in str(exc_info.value).lower()
        assert exc_info.value.pdf_path == "/nonexistent/path.pdf"

    def test_extract_from_file_not_pdf(self, extractor, tmp_path):
        """Test error when file is not a PDF."""
        # Create a non-PDF file
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("Not a PDF")

        with pytest.raises(PDFExtractionError) as exc_info:
            extractor.extract_from_file(txt_file)

        assert "not a pdf" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_extract_from_url_timeout(self, extractor):
        """Test handling of URL timeout."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                side_effect=httpx.TimeoutException("Timeout")
            )
            mock_client.return_value.__aexit__ = AsyncMock()

            with pytest.raises(PDFExtractionError) as exc_info:
                await extractor.extract_from_url("https://example.com/paper.pdf")

            assert "timeout" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_extract_from_url_http_error(self, extractor):
        """Test handling of HTTP errors."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Not Found", request=MagicMock(), response=mock_response
            )

            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_client.return_value.__aexit__ = AsyncMock()

            with pytest.raises(PDFExtractionError) as exc_info:
                await extractor.extract_from_url("https://example.com/paper.pdf")

            assert "404" in str(exc_info.value) or "http" in str(exc_info.value).lower()


class TestPDFExtractorWithMockedPyMuPDF:
    """Tests for PDF extraction with mocked PyMuPDF."""

    @pytest.fixture
    def extractor(self):
        """Create extractor instance."""
        return PDFExtractor()

    def test_extract_from_bytes_success(self, extractor):
        """Test successful extraction from bytes."""
        # Create mock document
        mock_page = MagicMock()
        mock_page.get_text.return_value = "This is page content.\nWith multiple lines."

        mock_doc = MagicMock()
        mock_doc.__iter__ = lambda self: iter([mock_page, mock_page])
        mock_doc.__len__ = lambda self: 2
        mock_doc.metadata = {"title": "Test Paper", "author": "Test Author"}

        with patch("fitz.open", return_value=mock_doc):
            result = extractor._extract_from_bytes(b"fake pdf bytes")

        assert result.total_pages == 2
        assert len(result.pages) == 2
        assert result.extraction_method == "pymupdf"
        assert result.metadata.get("title") == "Test Paper"
        assert result.is_scanned is False

    def test_extract_detects_scanned_pdf(self, extractor):
        """Test detection of scanned PDFs (low text content)."""
        mock_page = MagicMock()
        mock_page.get_text.return_value = ""  # No text extracted

        mock_doc = MagicMock()
        mock_doc.__iter__ = lambda self: iter([mock_page])
        mock_doc.__len__ = lambda self: 1
        mock_doc.metadata = {}

        with patch("fitz.open", return_value=mock_doc):
            result = extractor._extract_from_bytes(b"fake pdf bytes")

        assert result.is_scanned is True

    def test_extract_handles_open_error(self, extractor):
        """Test handling of PDF open errors."""
        with patch("fitz.open", side_effect=Exception("Corrupt PDF")):
            with pytest.raises(PDFExtractionError) as exc_info:
                extractor._extract_from_bytes(b"corrupt pdf")

            assert "failed to open" in str(exc_info.value).lower()

    def test_extract_handles_missing_pymupdf(self, extractor):
        """Test error when PyMuPDF is not installed."""
        with patch.dict("sys.modules", {"fitz": None}):
            with patch("builtins.__import__", side_effect=ImportError("No module named 'fitz'")):
                with pytest.raises(PDFExtractionError) as exc_info:
                    extractor._extract_from_bytes(b"pdf bytes")

                assert "pymupdf" in str(exc_info.value).lower()


class TestGetPDFExtractor:
    """Tests for singleton access."""

    def setup_method(self):
        """Reset singleton before each test."""
        reset_pdf_extractor()

    def teardown_method(self):
        """Reset singleton after each test."""
        reset_pdf_extractor()

    def test_returns_extractor_instance(self):
        """Test that get_pdf_extractor returns an extractor."""
        extractor = get_pdf_extractor()

        assert isinstance(extractor, PDFExtractor)

    def test_returns_same_instance(self):
        """Test that get_pdf_extractor returns singleton."""
        extractor1 = get_pdf_extractor()
        extractor2 = get_pdf_extractor()

        assert extractor1 is extractor2

    def test_reset_clears_singleton(self):
        """Test that reset clears the singleton."""
        extractor1 = get_pdf_extractor()
        reset_pdf_extractor()
        extractor2 = get_pdf_extractor()

        assert extractor1 is not extractor2


class TestCleanTextIntegration:
    """Integration tests for text cleaning pipeline."""

    @pytest.fixture
    def extractor(self):
        """Create extractor with all cleaning enabled."""
        return PDFExtractor(
            remove_headers_footers=True,
            dehyphenate=True,
            normalize_unicode=True,
        )

    def test_full_cleaning_pipeline(self, extractor):
        """Test the full text cleaning pipeline."""
        raw_text = """arXiv:2106.01234v1 [cs.CL] 1 Jun 2021

This is the introduc-
tion to our paper on machine
learning.

We propose a novel approach
to natural language process-
ing tasks.

42

References are at the end."""

        cleaned = extractor._clean_text(raw_text)

        # Should remove arXiv header
        assert "arXiv" not in cleaned

        # Should dehyphenate
        assert "introduction" in cleaned
        assert "processing" in cleaned

        # Should remove page number
        assert "\n42\n" not in cleaned

        # Should preserve meaningful content
        assert "machine" in cleaned
        assert "learning" in cleaned
        assert "novel approach" in cleaned

    def test_preserves_paragraph_breaks(self, extractor):
        """Test that paragraph breaks are preserved."""
        raw_text = """First paragraph with some content.

Second paragraph with different content.

Third paragraph ends here."""

        cleaned = extractor._clean_text(raw_text)

        # Should have paragraph breaks
        assert "\n\n" in cleaned

        # Should have all paragraphs
        assert "First paragraph" in cleaned
        assert "Second paragraph" in cleaned
        assert "Third paragraph" in cleaned
