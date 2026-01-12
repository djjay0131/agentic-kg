"""Tests for Semantic Scholar API client."""

import pytest
from unittest.mock import patch, MagicMock
import httpx

from agentic_kg.data_acquisition.semantic_scholar import (
    SemanticScholarClient,
    SemanticScholarError,
    RateLimitError,
    NotFoundError,
    get_semantic_scholar_client,
    reset_semantic_scholar_client,
)
from agentic_kg.data_acquisition.models import SourceType


# Sample API responses
SAMPLE_PAPER_RESPONSE = {
    "paperId": "649def34f8be52c8b66281af98ae884c09aef38b",
    "externalIds": {
        "DOI": "10.48550/arXiv.1706.03762",
        "ArXiv": "1706.03762",
    },
    "title": "Attention Is All You Need",
    "abstract": "The dominant sequence transduction models...",
    "year": 2017,
    "venue": "NeurIPS",
    "publicationDate": "2017-06-12",
    "authors": [
        {"authorId": "1234", "name": "Ashish Vaswani"},
        {"authorId": "5678", "name": "Noam Shazeer"},
    ],
    "citationCount": 50000,
    "influentialCitationCount": 5000,
    "isOpenAccess": True,
    "openAccessPdf": {"url": "https://arxiv.org/pdf/1706.03762.pdf"},
    "fieldsOfStudy": ["Computer Science"],
}

SAMPLE_SEARCH_RESPONSE = {
    "data": [SAMPLE_PAPER_RESPONSE],
    "total": 1,
}

SAMPLE_CITATIONS_RESPONSE = {
    "data": [
        {
            "citingPaper": {
                "paperId": "abc123",
                "externalIds": {"DOI": "10.1234/test"},
                "title": "Citing Paper",
                "year": 2020,
            },
            "isInfluential": True,
        }
    ]
}

SAMPLE_EMBEDDING_RESPONSE = {
    "paperId": "649def34f8be52c8b66281af98ae884c09aef38b",
    "embedding": {"vector": [0.1] * 768},
}


class TestSemanticScholarClient:
    """Tests for SemanticScholarClient."""

    @pytest.fixture
    def mock_response(self):
        """Create mock HTTP response."""
        response = MagicMock(spec=httpx.Response)
        response.status_code = 200
        return response

    @pytest.fixture
    def client(self):
        """Create client with mocked config."""
        from agentic_kg.config import SemanticScholarConfig

        config = SemanticScholarConfig(
            api_key="",
            base_url="https://api.semanticscholar.org/graph/v1",
            timeout=30.0,
            rate_limit_unauthenticated=10.0,  # Use unauthenticated since api_key is ""
            max_retries=1,
            retry_delay=0.1,
        )
        return SemanticScholarClient(config)

    def test_create_default(self):
        with patch("agentic_kg.data_acquisition.semantic_scholar.get_config") as mock:
            mock.return_value.data_acquisition.semantic_scholar.api_key = ""
            mock.return_value.data_acquisition.semantic_scholar.base_url = (
                "https://api.semanticscholar.org/graph/v1"
            )
            mock.return_value.data_acquisition.semantic_scholar.timeout = 30.0
            mock.return_value.data_acquisition.semantic_scholar.rate_limit = 1.0  # Property returns this
            mock.return_value.data_acquisition.semantic_scholar.max_retries = 3
            mock.return_value.data_acquisition.semantic_scholar.retry_delay = 1.0

            client = SemanticScholarClient()
            assert client is not None

    def test_context_manager(self, client):
        with client as c:
            assert c is client
        assert client._client is None

    @patch.object(httpx.Client, "request")
    def test_get_paper(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = SAMPLE_PAPER_RESPONSE
        mock_request.return_value = mock_response

        paper = client.get_paper("649def34f8be52c8b66281af98ae884c09aef38b")

        assert paper.title == "Attention Is All You Need"
        assert paper.doi == "10.48550/arXiv.1706.03762"
        assert paper.arxiv_id == "1706.03762"
        assert paper.source == SourceType.SEMANTIC_SCHOLAR
        assert len(paper.authors) == 2

    @patch.object(httpx.Client, "request")
    def test_get_paper_by_doi(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = SAMPLE_PAPER_RESPONSE
        mock_request.return_value = mock_response

        paper = client.get_paper_by_doi("10.48550/arXiv.1706.03762")

        assert paper.title == "Attention Is All You Need"
        # Verify correct endpoint called
        call_args = mock_request.call_args
        assert "DOI:" in call_args[0][1]

    @patch.object(httpx.Client, "request")
    def test_get_paper_by_arxiv_id(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = SAMPLE_PAPER_RESPONSE
        mock_request.return_value = mock_response

        paper = client.get_paper_by_arxiv_id("1706.03762")

        assert paper.title == "Attention Is All You Need"
        call_args = mock_request.call_args
        assert "ARXIV:" in call_args[0][1]

    @patch.object(httpx.Client, "request")
    def test_search_papers(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = SAMPLE_SEARCH_RESPONSE
        mock_request.return_value = mock_response

        papers = client.search_papers("attention transformer", limit=10)

        assert len(papers) == 1
        assert papers[0].title == "Attention Is All You Need"

    @patch.object(httpx.Client, "request")
    def test_get_citations(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = SAMPLE_CITATIONS_RESPONSE
        mock_request.return_value = mock_response

        citations = client.get_citations("649def34f8be52c8b66281af98ae884c09aef38b")

        assert len(citations) == 1
        assert citations[0].is_influential is True

    @patch.object(httpx.Client, "request")
    def test_get_embedding(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = SAMPLE_EMBEDDING_RESPONSE
        mock_request.return_value = mock_response

        embedding = client.get_embedding("649def34f8be52c8b66281af98ae884c09aef38b")

        assert embedding is not None
        assert len(embedding) == 768

    @patch.object(httpx.Client, "request")
    def test_not_found_error(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_request.return_value = mock_response

        with pytest.raises(NotFoundError):
            client.get_paper("nonexistent")

    @patch.object(httpx.Client, "request")
    def test_rate_limit_error(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {"Retry-After": "60"}
        mock_request.return_value = mock_response

        with pytest.raises(RateLimitError) as exc_info:
            client.get_paper("test123")

        assert exc_info.value.retry_after == 60

    @patch.object(httpx.Client, "request")
    def test_parse_paper_minimal(self, mock_request, client):
        minimal_response = {
            "paperId": "test123",
            "title": "Test Paper",
            "authors": [],
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = minimal_response
        mock_request.return_value = mock_response

        paper = client.get_paper("test123")

        assert paper.paper_id == "test123"
        assert paper.title == "Test Paper"
        assert paper.doi is None


class TestSemanticScholarClientGlobal:
    """Tests for global client functions."""

    def setup_method(self):
        reset_semantic_scholar_client()

    def teardown_method(self):
        reset_semantic_scholar_client()

    def test_get_client(self):
        with patch("agentic_kg.data_acquisition.semantic_scholar.get_config") as mock:
            mock.return_value.data_acquisition.semantic_scholar.api_key = ""
            mock.return_value.data_acquisition.semantic_scholar.base_url = (
                "https://api.semanticscholar.org/graph/v1"
            )
            mock.return_value.data_acquisition.semantic_scholar.timeout = 30.0
            mock.return_value.data_acquisition.semantic_scholar.rate_limit = 1.0
            mock.return_value.data_acquisition.semantic_scholar.max_retries = 3
            mock.return_value.data_acquisition.semantic_scholar.retry_delay = 1.0

            client = get_semantic_scholar_client()
            assert client is not None

    def test_singleton(self):
        with patch("agentic_kg.data_acquisition.semantic_scholar.get_config") as mock:
            mock.return_value.data_acquisition.semantic_scholar.api_key = ""
            mock.return_value.data_acquisition.semantic_scholar.base_url = (
                "https://api.semanticscholar.org/graph/v1"
            )
            mock.return_value.data_acquisition.semantic_scholar.timeout = 30.0
            mock.return_value.data_acquisition.semantic_scholar.rate_limit = 1.0
            mock.return_value.data_acquisition.semantic_scholar.max_retries = 3
            mock.return_value.data_acquisition.semantic_scholar.retry_delay = 1.0

            client1 = get_semantic_scholar_client()
            client2 = get_semantic_scholar_client()
            assert client1 is client2
