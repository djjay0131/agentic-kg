"""
PDF Text Extraction Module.

Provides robust PDF text extraction using PyMuPDF (fitz) with fallback
to pdfplumber for complex layouts. Handles text cleanup, dehyphenation,
and unicode normalization.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# Singleton instance
_pdf_extractor: Optional["PDFExtractor"] = None


class PDFExtractionError(Exception):
    """Raised when PDF extraction fails."""

    def __init__(self, message: str, pdf_path: Optional[str] = None):
        self.pdf_path = pdf_path
        super().__init__(message)


@dataclass
class ExtractedPage:
    """Represents extracted text from a single PDF page."""

    page_number: int  # 1-indexed
    text: str
    char_count: int = 0
    word_count: int = 0

    def __post_init__(self):
        """Calculate character and word counts if not provided."""
        if self.char_count == 0:
            self.char_count = len(self.text)
        if self.word_count == 0:
            self.word_count = len(self.text.split())


@dataclass
class ExtractedText:
    """Represents extracted text from an entire PDF document."""

    pages: list[ExtractedPage] = field(default_factory=list)
    total_pages: int = 0
    is_scanned: bool = False
    extraction_method: str = "pymupdf"
    source_path: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        """Get the complete text from all pages."""
        return "\n\n".join(page.text for page in self.pages if page.text.strip())

    @property
    def total_chars(self) -> int:
        """Get total character count across all pages."""
        return sum(page.char_count for page in self.pages)

    @property
    def total_words(self) -> int:
        """Get total word count across all pages."""
        return sum(page.word_count for page in self.pages)


class PDFExtractor:
    """
    Extract text content from PDF documents.

    Uses PyMuPDF (fitz) as the primary extraction method with optional
    fallback to pdfplumber for complex layouts.
    """

    # Common header/footer patterns to remove
    HEADER_FOOTER_PATTERNS = [
        # Page numbers
        r"^\s*\d+\s*$",
        r"^\s*-\s*\d+\s*-\s*$",
        r"^\s*Page\s+\d+\s*(of\s+\d+)?\s*$",
        # Common headers
        r"^\s*arXiv:\d{4}\.\d{4,5}.*$",
        r"^\s*Preprint\..*$",
        r"^\s*Under review.*$",
        # Conference headers
        r"^\s*Proceedings of.*$",
        r"^\s*\d{4}\s+(IEEE|ACM|AAAI|NeurIPS|ICML|ICLR).*$",
    ]

    def __init__(
        self,
        remove_headers_footers: bool = True,
        dehyphenate: bool = True,
        normalize_unicode: bool = True,
        min_line_length: int = 3,
    ):
        """
        Initialize the PDF extractor.

        Args:
            remove_headers_footers: Whether to remove detected headers/footers.
            dehyphenate: Whether to rejoin hyphenated words at line breaks.
            normalize_unicode: Whether to normalize unicode to NFC form.
            min_line_length: Minimum line length to keep (filters noise).
        """
        self.remove_headers_footers = remove_headers_footers
        self.dehyphenate = dehyphenate
        self.normalize_unicode = normalize_unicode
        self.min_line_length = min_line_length

        # Compile header/footer patterns
        self._header_footer_re = [
            re.compile(pattern, re.IGNORECASE) for pattern in self.HEADER_FOOTER_PATTERNS
        ]

    async def extract_from_url(
        self,
        url: str,
        timeout: float = 60.0,
    ) -> ExtractedText:
        """
        Extract text from a PDF at a URL.

        Args:
            url: URL to the PDF file.
            timeout: Request timeout in seconds.

        Returns:
            ExtractedText with the document content.

        Raises:
            PDFExtractionError: If download or extraction fails.
        """
        logger.info(f"Downloading PDF from: {url}")

        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()

                # Verify content type
                content_type = response.headers.get("content-type", "")
                if "pdf" not in content_type.lower() and not url.endswith(".pdf"):
                    logger.warning(f"Unexpected content type: {content_type}")

                pdf_bytes = response.content

        except httpx.TimeoutException as e:
            raise PDFExtractionError(f"Timeout downloading PDF: {e}", url) from e
        except httpx.HTTPStatusError as e:
            raise PDFExtractionError(
                f"HTTP error downloading PDF: {e.response.status_code}", url
            ) from e
        except httpx.RequestError as e:
            raise PDFExtractionError(f"Error downloading PDF: {e}", url) from e

        return self._extract_from_bytes(pdf_bytes, source_path=url)

    def extract_from_file(self, file_path: Union[str, Path]) -> ExtractedText:
        """
        Extract text from a local PDF file.

        Args:
            file_path: Path to the PDF file.

        Returns:
            ExtractedText with the document content.

        Raises:
            PDFExtractionError: If file doesn't exist or extraction fails.
        """
        path = Path(file_path)

        if not path.exists():
            raise PDFExtractionError(f"PDF file not found: {path}", str(path))

        if not path.suffix.lower() == ".pdf":
            raise PDFExtractionError(f"File is not a PDF: {path}", str(path))

        logger.info(f"Extracting text from: {path}")

        with open(path, "rb") as f:
            pdf_bytes = f.read()

        return self._extract_from_bytes(pdf_bytes, source_path=str(path))

    def _extract_from_bytes(
        self,
        pdf_bytes: bytes,
        source_path: Optional[str] = None,
    ) -> ExtractedText:
        """
        Extract text from PDF bytes using PyMuPDF.

        Args:
            pdf_bytes: Raw PDF content.
            source_path: Original source path for reference.

        Returns:
            ExtractedText with the document content.

        Raises:
            PDFExtractionError: If extraction fails.
        """
        try:
            import fitz  # PyMuPDF
        except ImportError as e:
            raise PDFExtractionError(
                "PyMuPDF not installed. Install with: pip install PyMuPDF"
            ) from e

        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as e:
            raise PDFExtractionError(f"Failed to open PDF: {e}", source_path) from e

        pages: list[ExtractedPage] = []
        total_text_chars = 0
        metadata = {}

        try:
            # Extract metadata
            metadata = {
                "title": doc.metadata.get("title", ""),
                "author": doc.metadata.get("author", ""),
                "subject": doc.metadata.get("subject", ""),
                "creator": doc.metadata.get("creator", ""),
                "producer": doc.metadata.get("producer", ""),
            }
            # Remove empty metadata
            metadata = {k: v for k, v in metadata.items() if v}

            for page_num, page in enumerate(doc):
                # Extract text from page
                raw_text = page.get_text("text")

                if raw_text:
                    total_text_chars += len(raw_text)

                # Clean and process the text
                cleaned_text = self._clean_text(raw_text)

                pages.append(
                    ExtractedPage(
                        page_number=page_num + 1,  # 1-indexed
                        text=cleaned_text,
                    )
                )

        finally:
            doc.close()

        # Detect if PDF is scanned (very low text content)
        is_scanned = total_text_chars < 100 and len(pages) > 0

        if is_scanned:
            logger.warning(
                f"PDF appears to be scanned (only {total_text_chars} chars). "
                "OCR not supported in this version."
            )

        return ExtractedText(
            pages=pages,
            total_pages=len(pages),
            is_scanned=is_scanned,
            extraction_method="pymupdf",
            source_path=source_path,
            metadata=metadata,
        )

    def _clean_text(self, text: str) -> str:
        """
        Clean extracted text with various transformations.

        Args:
            text: Raw extracted text.

        Returns:
            Cleaned text.
        """
        if not text:
            return ""

        # Unicode normalization
        if self.normalize_unicode:
            text = unicodedata.normalize("NFC", text)

        # Split into lines for processing
        lines = text.split("\n")

        # Remove headers/footers
        if self.remove_headers_footers:
            lines = self._remove_headers_footers(lines)

        # Filter short lines (likely noise)
        lines = [line for line in lines if len(line.strip()) >= self.min_line_length or not line.strip()]

        # Rejoin text
        text = "\n".join(lines)

        # Dehyphenation (rejoin hyphenated words at line breaks)
        if self.dehyphenate:
            text = self._dehyphenate(text)

        # Normalize whitespace
        text = self._normalize_whitespace(text)

        return text.strip()

    def _remove_headers_footers(self, lines: list[str]) -> list[str]:
        """
        Remove detected header and footer lines.

        Args:
            lines: List of text lines.

        Returns:
            Lines with headers/footers removed.
        """
        result = []

        for line in lines:
            stripped = line.strip()

            # Check against header/footer patterns
            is_header_footer = any(
                pattern.match(stripped) for pattern in self._header_footer_re
            )

            if not is_header_footer:
                result.append(line)

        return result

    def _dehyphenate(self, text: str) -> str:
        """
        Rejoin words that were hyphenated at line breaks.

        Args:
            text: Text with potential hyphenation.

        Returns:
            Text with dehyphenated words.
        """
        # Pattern: word fragment ending with hyphen at end of line
        # followed by lowercase continuation on next line
        pattern = r"(\w+)-\s*\n\s*([a-z])"

        def dehyphenate_match(match):
            return match.group(1) + match.group(2)

        return re.sub(pattern, dehyphenate_match, text)

    def _normalize_whitespace(self, text: str) -> str:
        """
        Normalize whitespace in text.

        Args:
            text: Text with potentially irregular whitespace.

        Returns:
            Text with normalized whitespace.
        """
        # Replace multiple spaces with single space
        text = re.sub(r"[ \t]+", " ", text)

        # Replace multiple newlines with double newline (paragraph break)
        text = re.sub(r"\n{3,}", "\n\n", text)

        # Remove trailing whitespace from lines
        lines = [line.rstrip() for line in text.split("\n")]

        return "\n".join(lines)


def get_pdf_extractor(
    remove_headers_footers: bool = True,
    dehyphenate: bool = True,
    normalize_unicode: bool = True,
) -> PDFExtractor:
    """
    Get or create the singleton PDF extractor instance.

    Args:
        remove_headers_footers: Whether to remove detected headers/footers.
        dehyphenate: Whether to rejoin hyphenated words.
        normalize_unicode: Whether to normalize unicode.

    Returns:
        PDFExtractor instance.
    """
    global _pdf_extractor

    if _pdf_extractor is None:
        _pdf_extractor = PDFExtractor(
            remove_headers_footers=remove_headers_footers,
            dehyphenate=dehyphenate,
            normalize_unicode=normalize_unicode,
        )

    return _pdf_extractor


def reset_pdf_extractor() -> None:
    """Reset the singleton PDF extractor (for testing)."""
    global _pdf_extractor
    _pdf_extractor = None
