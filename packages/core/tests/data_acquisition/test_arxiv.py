"""Tests for arXiv API client."""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import tempfile
import httpx

from agentic_kg.data_acquisition.arxiv import (
    ArxivClient,
    ArxivError,
    ArxivNotFoundError,
    ArxivRateLimitError,
    parse_arxiv_id,
    normalize_arxiv_id,
    get_arxiv_client,
    reset_arxiv_client,
)
from agentic_kg.data_acquisition.models import SourceType


# Sample arXiv API response (Atom XML)
SAMPLE_ARXIV_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2301.12345v1</id>
    <title>Test Paper Title</title>
    <summary>This is the abstract of the test paper.</summary>
    <published>2023-01-15T00:00:00Z</published>
    <author>
      <name>John Doe</name>
      <arxiv:affiliation>MIT</arxiv:affiliation>
    </author>
    <author>
      <name>Jane Smith</name>
    </author>
    <link href="https://arxiv.org/pdf/2301.12345v1.pdf" title="pdf"/>
    <arxiv:doi>10.1234/test.12345</arxiv:doi>
    <category term="cs.AI"/>
    <category term="cs.LG"/>
  </entry>
</feed>
"""

SAMPLE_ARXIV_NOT_FOUND = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/api/errors#id_not_found</id>
    <title>Error</title>
    <summary>Paper not found</summary>
  </entry>
</feed>
"""


class TestParseArxivId:
    """Tests for arXiv ID parsing."""

    def test_new_format(self):
        base_id, version = parse_arxiv_id("2301.12345")
        assert base_id == "2301.12345"
        assert version is None

    def test_new_format_with_version(self):
        base_id, version = parse_arxiv_id("2301.12345v2")
        assert base_id == "2301.12345"
        assert version == "v2"

    def test_old_format(self):
        base_id, version = parse_arxiv_id("cs.AI/0501001")
        assert base_id == "cs.AI/0501001"
        assert version is None

    def test_old_format_with_version(self):
        base_id, version = parse_arxiv_id("hep-th/9901001v3")
        assert base_id == "hep-th/9901001"
        assert version == "v3"

    def test_with_arxiv_prefix(self):
        base_id, version = parse_arxiv_id("arXiv:2301.12345")
        assert base_id == "2301.12345"

    def test_with_url_prefix(self):
        base_id, version = parse_arxiv_id("https://arxiv.org/abs/2301.12345v1")
        assert base_id == "2301.12345"
        assert version == "v1"


class TestNormalizeArxivId:
    """Tests for arXiv ID normalization."""

    def test_simple(self):
        result = normalize_arxiv_id("2301.12345")
        assert result == "2301.12345"

    def test_with_version_excluded(self):
        result = normalize_arxiv_id("2301.12345v2", include_version=False)
        assert result == "2301.12345"

    def test_with_version_included(self):
        result = normalize_arxiv_id("2301.12345v2", include_version=True)
        assert result == "2301.12345v2"

    def test_with_prefix(self):
        result = normalize_arxiv_id("arXiv:2301.12345")
        assert result == "2301.12345"


class TestArxivClient:
    """Tests for ArxivClient."""

    @pytest.fixture
    def client(self):
        """Create client with mocked config."""
        from agentic_kg.config import ArxivConfig

        config = ArxivConfig(
            base_url="https://export.arxiv.org/api/",
            timeout=30.0,
            rate_limit=3.0,
            max_retries=1,
            retry_delay=0.1,
        )
        return ArxivClient(config)

    def test_context_manager(self, client):
        with client as c:
            assert c is client
        assert client._client is None

    @patch.object(httpx.Client, "get")
    def test_get_metadata(self, mock_get, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_ARXIV_RESPONSE
        mock_get.return_value = mock_response

        paper = client.get_metadata("2301.12345")

        assert paper.title == "Test Paper Title"
        assert paper.arxiv_id == "2301.12345"
        assert paper.doi == "10.1234/test.12345"
        assert paper.source == SourceType.ARXIV
        assert len(paper.authors) == 2
        assert paper.authors[0].name == "John Doe"
        assert "MIT" in paper.authors[0].affiliations
        assert paper.is_open_access is True

    @patch.object(httpx.Client, "get")
    def test_get_metadata_not_found(self, mock_get, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_ARXIV_NOT_FOUND
        mock_get.return_value = mock_response

        with pytest.raises(ArxivNotFoundError):
            client.get_metadata("0000.00000")

    @patch.object(httpx.Client, "get")
    def test_search(self, mock_get, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_ARXIV_RESPONSE
        mock_get.return_value = mock_response

        papers = client.search("cat:cs.AI AND all:transformer", max_results=10)

        assert len(papers) == 1
        assert papers[0].title == "Test Paper Title"

    def test_get_pdf_url(self, client):
        url = client.get_pdf_url("2301.12345")
        assert url == "https://arxiv.org/pdf/2301.12345.pdf"

    def test_get_pdf_url_with_version(self, client):
        url = client.get_pdf_url("2301.12345v2", include_version=True)
        assert url == "https://arxiv.org/pdf/2301.12345v2.pdf"

    @patch.object(httpx.Client, "stream")
    def test_download_pdf(self, mock_stream, client):
        # Create mock streaming response
        mock_context = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_bytes.return_value = [b"PDF content"]
        mock_context.__enter__ = MagicMock(return_value=mock_response)
        mock_context.__exit__ = MagicMock(return_value=None)
        mock_stream.return_value = mock_context

        with tempfile.TemporaryDirectory() as temp_dir:
            path = client.download_pdf("2301.12345", Path(temp_dir))

            assert path.exists()
            assert path.name == "2301.12345.pdf"

    @patch.object(httpx.Client, "get")
    def test_get_pdf_bytes(self, mock_get, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"PDF content here"
        mock_get.return_value = mock_response

        content = client.get_pdf_bytes("2301.12345")

        assert content == b"PDF content here"

    @patch.object(httpx.Client, "get")
    def test_rate_limit_error(self, mock_get, client):
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.headers = {"Retry-After": "30"}
        mock_get.return_value = mock_response

        with pytest.raises(ArxivRateLimitError):
            client.get_metadata("2301.12345")


class TestArxivClientGlobal:
    """Tests for global client functions."""

    def setup_method(self):
        reset_arxiv_client()

    def teardown_method(self):
        reset_arxiv_client()

    def test_get_client(self):
        with patch("agentic_kg.data_acquisition.arxiv.get_config") as mock:
            mock.return_value.data_acquisition.arxiv.base_url = (
                "https://export.arxiv.org/api/"
            )
            mock.return_value.data_acquisition.arxiv.timeout = 30.0
            mock.return_value.data_acquisition.arxiv.rate_limit = 3.0
            mock.return_value.data_acquisition.arxiv.max_retries = 3
            mock.return_value.data_acquisition.arxiv.retry_delay = 1.0

            client = get_arxiv_client()
            assert client is not None

    def test_singleton(self):
        with patch("agentic_kg.data_acquisition.arxiv.get_config") as mock:
            mock.return_value.data_acquisition.arxiv.base_url = (
                "https://export.arxiv.org/api/"
            )
            mock.return_value.data_acquisition.arxiv.timeout = 30.0
            mock.return_value.data_acquisition.arxiv.rate_limit = 3.0
            mock.return_value.data_acquisition.arxiv.max_retries = 3
            mock.return_value.data_acquisition.arxiv.retry_delay = 1.0

            client1 = get_arxiv_client()
            client2 = get_arxiv_client()
            assert client1 is client2
