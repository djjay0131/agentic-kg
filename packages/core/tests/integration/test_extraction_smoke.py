"""Smoke tests for extraction pipeline.

These tests verify basic extraction functionality end-to-end,
catching issues like missing dependencies, attribute errors, etc.
"""

import pytest
from pathlib import Path
from agentic_kg.extraction.pipeline import extract_text_from_file
from agentic_kg.extraction.pdf_extractor import ExtractedText


class TestExtractionSmoke:
    """Smoke tests for text extraction."""

    def test_extract_text_from_simple_text_file(self, tmp_path):
        """Test that we can extract text from a simple text file."""
        # Create a simple text file
        test_file = tmp_path / "test.txt"
        test_content = "This is a test file.\nIt has multiple lines.\n"
        test_file.write_text(test_content)

        # Extract text
        result = extract_text_from_file(str(test_file))

        # Verify result structure
        assert isinstance(result, ExtractedText)
        assert result.total_pages == 1
        assert len(result.pages) == 1
        assert result.pages[0].content == test_content
        assert result.pages[0].page_number == 1

    def test_extract_text_from_markdown_file(self, tmp_path):
        """Test that we can extract text from a markdown file."""
        # Create a markdown file
        test_file = tmp_path / "test.md"
        test_content = "# Test Document\n\nThis is a test.\n"
        test_file.write_text(test_content)

        # Extract text
        result = extract_text_from_file(str(test_file))

        # Verify result structure
        assert isinstance(result, ExtractedText)
        assert result.total_pages == 1
        assert len(result.pages) == 1
        assert result.pages[0].content == test_content

    def test_extract_text_from_pdf_requires_pymupdf(self, tmp_path):
        """Test that PDF extraction properly handles PyMuPDF dependency.

        This test ensures we get a meaningful error if PyMuPDF is missing,
        rather than an AttributeError about missing attributes.
        """
        # Create a dummy PDF file (won't be valid, but tests the code path)
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"%PDF-1.4\n%")

        # Try to extract - should either work (if PyMuPDF installed)
        # or raise ImportError (if not installed)
        try:
            result = extract_text_from_file(str(test_file))
            # If we get here, PyMuPDF is installed and extraction worked
            assert isinstance(result, ExtractedText)
            # Should have total_pages attribute (not page_count)
            assert hasattr(result, 'total_pages')
            assert not hasattr(result, 'page_count')
        except ImportError as e:
            # This is acceptable - means PyMuPDF not installed
            assert "PyMuPDF" in str(e) or "fitz" in str(e)
        except Exception as e:
            # Any other error should be specific, not AttributeError
            if isinstance(e, AttributeError):
                pytest.fail(f"Got AttributeError instead of proper error handling: {e}")

    def test_extracted_text_has_correct_attributes(self):
        """Test that ExtractedText dataclass has the expected attributes.

        This catches issues like renaming attributes without updating
        all references (e.g., page_count vs total_pages).
        """
        # Create an ExtractedText instance
        extracted = ExtractedText()

        # Verify it has the correct attributes
        assert hasattr(extracted, 'pages')
        assert hasattr(extracted, 'total_pages')

        # Verify it does NOT have incorrect attributes
        assert not hasattr(extracted, 'page_count'), \
            "ExtractedText should use 'total_pages', not 'page_count'"

    def test_extraction_result_metadata(self, tmp_path):
        """Test that extraction results include proper metadata."""
        # Create a test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("Test content")

        # Extract text
        result = extract_text_from_file(str(test_file))

        # Verify pages have required metadata
        assert len(result.pages) > 0
        page = result.pages[0]
        assert hasattr(page, 'page_number')
        assert hasattr(page, 'content')
        assert page.page_number >= 1
        assert isinstance(page.content, str)
