"""Smoke tests for PDF extraction.

These tests verify basic PDF extraction functionality end-to-end,
catching issues like missing dependencies, attribute errors, etc.
"""

import pytest
from pathlib import Path
from agentic_kg.extraction.pdf_extractor import get_pdf_extractor, ExtractedText, PDFExtractionError


class TestPDFExtractionSmoke:
    """Smoke tests for PDF text extraction."""

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

    def test_pdf_extractor_available(self):
        """Test that PDF extractor can be instantiated."""
        extractor = get_pdf_extractor()
        assert extractor is not None
        assert hasattr(extractor, 'extract_from_file')

    def test_pdf_extraction_requires_pymupdf(self, tmp_path):
        """Test that PDF extraction fails gracefully if PyMuPDF is missing.

        This ensures we get a meaningful PDFExtractionError, not an AttributeError.
        """
        # Create a minimal valid PDF file
        test_file = tmp_path / "test.pdf"
        # Minimal PDF header
        test_file.write_bytes(b"%PDF-1.4\n%%EOF")

        extractor = get_pdf_extractor()

        try:
            # Try to extract - will fail if PyMuPDF not installed
            result = extractor.extract_from_file(test_file)

            # If we get here, PyMuPDF is installed
            assert isinstance(result, ExtractedText)
            assert hasattr(result, 'total_pages')
            assert not hasattr(result, 'page_count'), \
                "Should use total_pages, not page_count"
        except PDFExtractionError as e:
            # This is acceptable - means PyMuPDF not installed or PDF invalid
            # The key is we got a PDFExtractionError, not AttributeError
            assert "PyMuPDF" in str(e) or "failed" in str(e).lower()
        except AttributeError as e:
            pytest.fail(
                f"Got AttributeError instead of PDFExtractionError. "
                f"This indicates a bug in error handling or attribute naming: {e}"
            )
