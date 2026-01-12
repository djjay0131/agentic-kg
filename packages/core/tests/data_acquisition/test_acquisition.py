"""Tests for unified Paper Acquisition Layer."""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import tempfile

from agentic_kg.data_acquisition.acquisition import (
    PaperAcquisitionLayer,
    IdentifierType,
    detect_identifier_type,
    clean_identifier,
    get_acquisition_layer,
    reset_acquisition_layer,
)
from agentic_kg.data_acquisition.models import (
    PaperMetadata,
    AuthorRef,
    SourceType,
    DownloadStatus,
)


class TestDetectIdentifierType:
    """Tests for identifier type detection."""

    def test_doi_simple(self):
        assert detect_identifier_type("10.1038/nature12373") == IdentifierType.DOI

    def test_doi_with_prefix(self):
        assert detect_identifier_type("doi:10.1038/nature12373") == IdentifierType.DOI

    def test_doi_with_url(self):
        assert (
            detect_identifier_type("https://doi.org/10.1038/nature12373")
            == IdentifierType.DOI
        )

    def test_arxiv_new_format(self):
        assert detect_identifier_type("2301.12345") == IdentifierType.ARXIV

    def test_arxiv_with_version(self):
        assert detect_identifier_type("2301.12345v2") == IdentifierType.ARXIV

    def test_arxiv_old_format(self):
        assert detect_identifier_type("cs.AI/0501001") == IdentifierType.ARXIV

    def test_arxiv_with_prefix(self):
        assert detect_identifier_type("arxiv:2301.12345") == IdentifierType.ARXIV

    def test_arxiv_url(self):
        assert (
            detect_identifier_type("https://arxiv.org/abs/2301.12345")
            == IdentifierType.ARXIV
        )

    def test_s2_id(self):
        # 40 character hex string
        s2_id = "649def34f8be52c8b66281af98ae884c09aef38b"
        assert detect_identifier_type(s2_id) == IdentifierType.S2_ID

    def test_s2_id_with_prefix(self):
        assert detect_identifier_type("s2:abc123") == IdentifierType.S2_ID

    def test_openalex_id(self):
        assert detect_identifier_type("W2741809807") == IdentifierType.OPENALEX_ID

    def test_openalex_with_prefix(self):
        assert (
            detect_identifier_type("openalex:W2741809807") == IdentifierType.OPENALEX_ID
        )

    def test_url(self):
        assert (
            detect_identifier_type("https://example.com/paper") == IdentifierType.URL
        )

    def test_unknown(self):
        assert detect_identifier_type("random-string") == IdentifierType.UNKNOWN


class TestCleanIdentifier:
    """Tests for identifier cleaning."""

    def test_clean_doi(self):
        result = clean_identifier(
            "https://doi.org/10.1038/nature12373", IdentifierType.DOI
        )
        assert result == "10.1038/nature12373"

    def test_clean_arxiv(self):
        result = clean_identifier("arxiv:2301.12345", IdentifierType.ARXIV)
        assert result == "2301.12345"

    def test_clean_s2(self):
        result = clean_identifier("s2:abc123", IdentifierType.S2_ID)
        assert result == "abc123"

    def test_clean_openalex(self):
        result = clean_identifier("openalex:W2741809807", IdentifierType.OPENALEX_ID)
        assert result == "W2741809807"


class TestPaperAcquisitionLayer:
    """Tests for PaperAcquisitionLayer."""

    @pytest.fixture
    def mock_s2_client(self):
        client = MagicMock()
        client.get_paper_by_doi.return_value = PaperMetadata(
            paper_id="test123",
            doi="10.1038/nature12373",
            title="Test Paper",
            source=SourceType.SEMANTIC_SCHOLAR,
            authors=[AuthorRef(name="Test Author")],
        )
        client.get_paper_by_arxiv_id.return_value = PaperMetadata(
            paper_id="test456",
            arxiv_id="2301.12345",
            title="arXiv Paper",
            source=SourceType.SEMANTIC_SCHOLAR,
        )
        client.get_embedding.return_value = [0.1] * 768
        return client

    @pytest.fixture
    def mock_arxiv_client(self):
        client = MagicMock()
        client.get_metadata.return_value = PaperMetadata(
            paper_id="2301.12345",
            arxiv_id="2301.12345",
            title="arXiv Test Paper",
            source=SourceType.ARXIV,
            pdf_url="https://arxiv.org/pdf/2301.12345.pdf",
        )
        client.get_pdf_url.return_value = "https://arxiv.org/pdf/2301.12345.pdf"
        return client

    @pytest.fixture
    def mock_openalex_client(self):
        client = MagicMock()
        client.get_work_by_doi.return_value = PaperMetadata(
            paper_id="W123",
            doi="10.1038/nature12373",
            title="OpenAlex Paper",
            source=SourceType.OPENALEX,
        )
        return client

    @pytest.fixture
    def acquisition(self, mock_s2_client, mock_arxiv_client, mock_openalex_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            layer = PaperAcquisitionLayer(
                s2_client=mock_s2_client,
                arxiv_client=mock_arxiv_client,
                openalex_client=mock_openalex_client,
                cache_dir=Path(temp_dir),
            )
            yield layer

    def test_get_identifier_type(self, acquisition):
        assert acquisition.get_identifier_type("10.1038/nature12373") == IdentifierType.DOI
        assert acquisition.get_identifier_type("2301.12345") == IdentifierType.ARXIV

    def test_get_paper_metadata_by_doi(self, acquisition, mock_s2_client):
        paper = acquisition.get_paper_metadata("10.1038/nature12373")

        assert paper is not None
        assert paper.title == "Test Paper"
        mock_s2_client.get_paper_by_doi.assert_called_once()

    def test_get_paper_metadata_by_arxiv(self, acquisition, mock_arxiv_client):
        paper = acquisition.get_paper_metadata("2301.12345")

        assert paper is not None
        assert paper.title == "arXiv Test Paper"
        mock_arxiv_client.get_metadata.assert_called_once()

    def test_get_paper_metadata_with_embedding(self, acquisition, mock_s2_client):
        paper = acquisition.get_paper_metadata(
            "10.1038/nature12373", include_embedding=True
        )

        assert paper is not None
        mock_s2_client.get_embedding.assert_called()

    def test_get_paper_metadata_not_found(self, acquisition, mock_s2_client, mock_openalex_client):
        from agentic_kg.data_acquisition.semantic_scholar import NotFoundError
        from agentic_kg.data_acquisition.openalex import OpenAlexNotFoundError

        mock_s2_client.get_paper_by_doi.side_effect = NotFoundError("Not found")
        mock_openalex_client.get_work_by_doi.side_effect = OpenAlexNotFoundError(
            "Not found"
        )

        paper = acquisition.get_paper_metadata("10.9999/nonexistent")
        assert paper is None

    def test_is_available_true(self, acquisition):
        assert acquisition.is_available("10.1038/nature12373") is True

    def test_is_available_false(self, acquisition, mock_s2_client, mock_openalex_client):
        from agentic_kg.data_acquisition.semantic_scholar import NotFoundError
        from agentic_kg.data_acquisition.openalex import OpenAlexNotFoundError

        mock_s2_client.get_paper_by_doi.side_effect = NotFoundError("Not found")
        mock_openalex_client.get_work_by_doi.side_effect = OpenAlexNotFoundError(
            "Not found"
        )

        assert acquisition.is_available("10.9999/nonexistent") is False

    def test_get_pdf_url_arxiv(self, acquisition, mock_arxiv_client):
        url = acquisition.get_pdf_url("2301.12345")
        assert url == "https://arxiv.org/pdf/2301.12345.pdf"

    def test_get_pdf_arxiv(self, acquisition, mock_arxiv_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create the mock PDF file so that path.stat() works
            pdf_path = Path(temp_dir) / "test.pdf"
            pdf_path.write_bytes(b"%PDF-1.4 mock content")
            mock_arxiv_client.download_pdf.return_value = pdf_path

            result = acquisition.get_pdf("2301.12345", Path(temp_dir))

            assert result.status == DownloadStatus.COMPLETED
            mock_arxiv_client.download_pdf.assert_called()

    def test_get_pdf_not_available(self, acquisition, mock_s2_client, mock_openalex_client):
        from agentic_kg.data_acquisition.semantic_scholar import NotFoundError
        from agentic_kg.data_acquisition.openalex import OpenAlexNotFoundError

        mock_s2_client.get_paper_by_doi.side_effect = NotFoundError("Not found")
        mock_openalex_client.get_work_by_doi.side_effect = OpenAlexNotFoundError(
            "Not found"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            result = acquisition.get_pdf("10.9999/nonexistent", Path(temp_dir))
            assert result.status == DownloadStatus.NOT_AVAILABLE


class TestAcquisitionLayerSearch:
    """Tests for search functionality."""

    @pytest.fixture
    def acquisition_with_search(self):
        mock_s2 = MagicMock()
        mock_s2.search_papers.return_value = [
            PaperMetadata(
                paper_id="s2_1",
                doi="10.1234/test1",
                title="S2 Result 1",
                source=SourceType.SEMANTIC_SCHOLAR,
            ),
        ]

        mock_arxiv = MagicMock()
        mock_arxiv.search.return_value = [
            PaperMetadata(
                paper_id="arxiv_1",
                arxiv_id="2301.11111",
                title="arXiv Result 1",
                source=SourceType.ARXIV,
            ),
        ]

        mock_openalex = MagicMock()
        mock_openalex.search_works.return_value = [
            PaperMetadata(
                paper_id="oa_1",
                doi="10.1234/test2",
                title="OpenAlex Result 1",
                source=SourceType.OPENALEX,
            ),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            layer = PaperAcquisitionLayer(
                s2_client=mock_s2,
                arxiv_client=mock_arxiv,
                openalex_client=mock_openalex,
                cache_dir=Path(temp_dir),
            )
            yield layer

    def test_search_all_sources(self, acquisition_with_search):
        results = acquisition_with_search.search("test query", limit=10)

        assert len(results) == 3  # One from each source

    def test_search_specific_source(self, acquisition_with_search):
        results = acquisition_with_search.search(
            "test query", source=SourceType.ARXIV, limit=10
        )

        # Only arXiv results
        assert all(r.source == SourceType.ARXIV for r in results)

    def test_search_deduplication(self):
        mock_s2 = MagicMock()
        mock_s2.search_papers.return_value = [
            PaperMetadata(
                paper_id="s2_1",
                doi="10.1234/same",
                title="Same Paper S2",
                source=SourceType.SEMANTIC_SCHOLAR,
            ),
        ]

        mock_openalex = MagicMock()
        mock_openalex.search_works.return_value = [
            PaperMetadata(
                paper_id="oa_1",
                doi="10.1234/same",  # Same DOI
                title="Same Paper OA",
                source=SourceType.OPENALEX,
            ),
        ]

        mock_arxiv = MagicMock()
        mock_arxiv.search.return_value = []

        with tempfile.TemporaryDirectory() as temp_dir:
            layer = PaperAcquisitionLayer(
                s2_client=mock_s2,
                arxiv_client=mock_arxiv,
                openalex_client=mock_openalex,
                cache_dir=Path(temp_dir),
            )

            results = layer.search("test", limit=10)

            # Should deduplicate by DOI
            assert len(results) == 1


class TestAcquisitionLayerGlobal:
    """Tests for global acquisition layer functions."""

    def setup_method(self):
        reset_acquisition_layer()

    def teardown_method(self):
        reset_acquisition_layer()

    def test_get_acquisition_layer(self):
        with patch("agentic_kg.data_acquisition.acquisition.get_config") as mock:
            mock.return_value.data_acquisition.cache.cache_dir = tempfile.mkdtemp()

            layer = get_acquisition_layer()
            assert layer is not None

    def test_singleton(self):
        with patch("agentic_kg.data_acquisition.acquisition.get_config") as mock:
            mock.return_value.data_acquisition.cache.cache_dir = tempfile.mkdtemp()

            layer1 = get_acquisition_layer()
            layer2 = get_acquisition_layer()
            assert layer1 is layer2
