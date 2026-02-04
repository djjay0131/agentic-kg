"""
Unit tests for Knowledge Graph paper importer.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from agentic_kg.data_acquisition.importer import (
    BatchImportResult,
    ImportResult,
    PaperImporter,
    get_paper_importer,
    normalized_to_kg_author,
    normalized_to_kg_paper,
    reset_paper_importer,
)
from agentic_kg.data_acquisition.aggregator import AggregatedResult
from agentic_kg.data_acquisition.normalizer import NormalizedAuthor, NormalizedPaper
from agentic_kg.data_acquisition.exceptions import NotFoundError
from agentic_kg.knowledge_graph.repository import DuplicateError


class TestImportResult:
    """Tests for ImportResult dataclass."""

    def test_create_with_defaults(self):
        """Test creating result with default values."""
        result = ImportResult()

        assert result.paper is None
        assert result.created is False
        assert result.updated is False
        assert result.skipped is False
        assert result.error is None
        assert result.sources == []

    def test_create_created_result(self):
        """Test creating a result for newly created paper."""
        result = ImportResult(
            paper=MagicMock(),
            created=True,
            sources=["semantic_scholar"],
        )

        assert result.created is True
        assert result.updated is False
        assert result.skipped is False

    def test_create_error_result(self):
        """Test creating an error result."""
        result = ImportResult(error="Not found")

        assert result.error == "Not found"
        assert result.paper is None


class TestBatchImportResult:
    """Tests for BatchImportResult dataclass."""

    def test_create_with_defaults(self):
        """Test creating batch result with defaults."""
        result = BatchImportResult()

        assert result.total == 0
        assert result.created == 0
        assert result.updated == 0
        assert result.skipped == 0
        assert result.failed == 0
        assert result.results == []
        assert result.errors == {}

    def test_to_dict(self):
        """Test batch result serialization."""
        result = BatchImportResult(
            total=10,
            created=5,
            updated=2,
            skipped=2,
            failed=1,
            errors={"doi1": "Not found"},
        )

        d = result.to_dict()

        assert d["total"] == 10
        assert d["created"] == 5
        assert d["updated"] == 2
        assert d["skipped"] == 2
        assert d["failed"] == 1
        assert d["errors"]["doi1"] == "Not found"


class TestNormalizedToKgPaper:
    """Tests for normalized_to_kg_paper conversion."""

    def test_convert_minimal_paper(self):
        """Test converting paper with minimal fields."""
        normalized = NormalizedPaper(
            title="Test Paper",
            source="semantic_scholar",
            doi="10.1234/test",
        )

        paper = normalized_to_kg_paper(normalized)

        assert paper.doi == "10.1234/test"
        assert paper.title == "Test Paper"
        assert paper.authors == []

    def test_convert_full_paper(self):
        """Test converting paper with all fields."""
        normalized = NormalizedPaper(
            title="Full Paper",
            source="semantic_scholar",
            doi="10.1234/full",
            abstract="This is an abstract.",
            year=2023,
            venue="NeurIPS",
            authors=[
                NormalizedAuthor(name="Author 1"),
                NormalizedAuthor(name="Author 2"),
            ],
            external_ids={
                "arxiv": "2106.01345",
                "openalex": "W123",
                "semantic_scholar": "abc123",
            },
            pdf_url="https://example.com/paper.pdf",
        )

        paper = normalized_to_kg_paper(normalized)

        assert paper.doi == "10.1234/full"
        assert paper.title == "Full Paper"
        assert paper.abstract == "This is an abstract."
        assert paper.year == 2023
        assert paper.venue == "NeurIPS"
        assert paper.authors == ["Author 1", "Author 2"]
        assert paper.arxiv_id == "2106.01345"
        assert paper.openalex_id == "W123"
        assert paper.semantic_scholar_id == "abc123"
        assert paper.pdf_url == "https://example.com/paper.pdf"

    def test_convert_requires_doi(self):
        """Test that conversion fails without DOI."""
        normalized = NormalizedPaper(
            title="No DOI Paper",
            source="arxiv",
            doi=None,
        )

        with pytest.raises(ValueError) as exc_info:
            normalized_to_kg_paper(normalized)

        assert "DOI" in str(exc_info.value)

    def test_convert_defaults_year_to_current(self):
        """Test that year defaults to current year when not specified."""
        normalized = NormalizedPaper(
            title="Test Paper Title for Testing",
            source="test",
            doi="10.1234/test",
            year=None,
        )

        paper = normalized_to_kg_paper(normalized)

        assert paper.year == datetime.now().year


class TestNormalizedToKgAuthor:
    """Tests for normalized_to_kg_author conversion."""

    def test_convert_basic_author(self):
        """Test converting author with minimal fields."""
        normalized = NormalizedAuthor(name="John Doe")

        author = normalized_to_kg_author(normalized, position=1)

        assert author.name == "John Doe"
        assert author.affiliations == []

    def test_convert_full_author(self):
        """Test converting author with all fields."""
        normalized = NormalizedAuthor(
            name="Jane Smith",
            affiliations=["MIT", "Stanford"],
            external_ids={
                "orcid": "0000-0001-2345-6789",
                "semantic_scholar": "ss123",
            },
        )

        author = normalized_to_kg_author(normalized, position=1)

        assert author.name == "Jane Smith"
        assert author.affiliations == ["MIT", "Stanford"]
        assert author.orcid == "0000-0001-2345-6789"
        assert author.semantic_scholar_id == "ss123"


class TestPaperImporter:
    """Tests for PaperImporter class."""

    @pytest.fixture
    def mock_aggregator(self):
        """Create mock aggregator."""
        aggregator = MagicMock()
        aggregator.get_paper = AsyncMock()
        aggregator.semantic_scholar = MagicMock()
        aggregator.semantic_scholar.get_author_papers = AsyncMock()
        aggregator.openalex = MagicMock()
        aggregator.openalex.get_author_works = AsyncMock()
        return aggregator

    @pytest.fixture
    def mock_repository(self):
        """Create mock repository."""
        repo = MagicMock()
        repo.get_paper = MagicMock(return_value=None)
        repo.create_paper = MagicMock()
        repo.update_paper = MagicMock()
        repo.create_author = MagicMock()
        repo.link_paper_to_author = MagicMock()
        return repo

    @pytest.fixture
    def importer(self, mock_aggregator, mock_repository):
        """Create importer with mock dependencies."""
        return PaperImporter(
            aggregator=mock_aggregator,
            repository=mock_repository,
        )

    @pytest.fixture
    def sample_normalized_paper(self):
        """Create sample normalized paper."""
        return NormalizedPaper(
            title="Test Paper",
            source="semantic_scholar",
            doi="10.1234/test",
            abstract="Test abstract",
            year=2023,
            authors=[
                NormalizedAuthor(
                    name="Author 1",
                    external_ids={"semantic_scholar": "ss1"},
                ),
            ],
        )

    @pytest.fixture
    def sample_aggregated_result(self, sample_normalized_paper):
        """Create sample aggregated result."""
        return AggregatedResult(
            paper=sample_normalized_paper,
            sources=["semantic_scholar"],
        )

    @pytest.mark.asyncio
    async def test_import_paper_creates_new(
        self,
        importer,
        mock_aggregator,
        mock_repository,
        sample_aggregated_result,
    ):
        """Test importing a new paper."""
        mock_aggregator.get_paper.return_value = sample_aggregated_result
        mock_repository.get_paper.return_value = None
        mock_repository.create_paper.return_value = MagicMock(doi="10.1234/test")

        result = await importer.import_paper("10.1234/test")

        assert result.created is True
        assert result.updated is False
        assert result.skipped is False
        mock_repository.create_paper.assert_called_once()

    @pytest.mark.asyncio
    async def test_import_paper_skips_existing(
        self,
        importer,
        mock_aggregator,
        mock_repository,
        sample_aggregated_result,
    ):
        """Test that existing papers are skipped by default."""
        mock_aggregator.get_paper.return_value = sample_aggregated_result
        mock_repository.get_paper.return_value = MagicMock(doi="10.1234/test")

        result = await importer.import_paper("10.1234/test", update_existing=False)

        assert result.skipped is True
        assert result.created is False
        mock_repository.create_paper.assert_not_called()

    @pytest.mark.asyncio
    async def test_import_paper_updates_existing(
        self,
        importer,
        mock_aggregator,
        mock_repository,
        sample_aggregated_result,
    ):
        """Test updating existing papers when requested."""
        mock_aggregator.get_paper.return_value = sample_aggregated_result
        mock_repository.get_paper.return_value = MagicMock(doi="10.1234/test")
        mock_repository.update_paper.return_value = MagicMock(doi="10.1234/test")

        result = await importer.import_paper("10.1234/test", update_existing=True)

        assert result.updated is True
        assert result.created is False
        mock_repository.update_paper.assert_called_once()

    @pytest.mark.asyncio
    async def test_import_paper_no_doi(
        self,
        importer,
        mock_aggregator,
    ):
        """Test error when paper has no DOI."""
        normalized = NormalizedPaper(title="No DOI", source="arxiv", doi=None)
        mock_aggregator.get_paper.return_value = AggregatedResult(
            paper=normalized,
            sources=["arxiv"],
        )

        result = await importer.import_paper("2106.01345")

        assert result.error is not None
        assert "DOI" in result.error

    @pytest.mark.asyncio
    async def test_import_paper_not_found(
        self,
        importer,
        mock_aggregator,
    ):
        """Test error when paper not found in any source."""
        mock_aggregator.get_paper.side_effect = NotFoundError(
            resource_type="paper",
            identifier="test",
            source="aggregator",
        )

        result = await importer.import_paper("10.1234/nonexistent")

        assert result.error is not None
        assert "Not found" in result.error

    @pytest.mark.asyncio
    async def test_import_paper_duplicate_error(
        self,
        importer,
        mock_aggregator,
        mock_repository,
        sample_aggregated_result,
    ):
        """Test handling duplicate error during creation."""
        mock_aggregator.get_paper.return_value = sample_aggregated_result
        mock_repository.get_paper.return_value = None
        mock_repository.create_paper.side_effect = DuplicateError("Already exists")

        result = await importer.import_paper("10.1234/test")

        assert result.error is not None
        assert result.skipped is True

    @pytest.mark.asyncio
    async def test_import_paper_creates_authors(
        self,
        importer,
        mock_aggregator,
        mock_repository,
        sample_aggregated_result,
    ):
        """Test that authors are created when requested."""
        mock_aggregator.get_paper.return_value = sample_aggregated_result
        mock_repository.get_paper.return_value = None
        mock_repository.create_paper.return_value = MagicMock(doi="10.1234/test")
        mock_repository.create_author.return_value = MagicMock(id="author-id")

        result = await importer.import_paper(
            "10.1234/test",
            create_authors=True,
        )

        assert result.created is True
        mock_repository.create_author.assert_called()
        mock_repository.link_paper_to_author.assert_called()

    @pytest.mark.asyncio
    async def test_import_paper_no_authors(
        self,
        importer,
        mock_aggregator,
        mock_repository,
        sample_aggregated_result,
    ):
        """Test that authors are not created when disabled."""
        mock_aggregator.get_paper.return_value = sample_aggregated_result
        mock_repository.get_paper.return_value = None
        mock_repository.create_paper.return_value = MagicMock(doi="10.1234/test")

        result = await importer.import_paper(
            "10.1234/test",
            create_authors=False,
        )

        assert result.created is True
        mock_repository.create_author.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_import(
        self,
        importer,
        mock_aggregator,
        mock_repository,
    ):
        """Test batch importing multiple papers."""
        # Create papers with DOIs
        papers = []
        for i in range(3):
            paper = NormalizedPaper(
                title=f"Test Paper Number {i}",
                source="test",
                doi=f"10.1234/test{i}",
            )
            papers.append(paper)

        async def get_paper_side_effect(identifier, **kwargs):
            idx = int(identifier[-1])
            return AggregatedResult(paper=papers[idx], sources=["test"])

        mock_aggregator.get_paper.side_effect = get_paper_side_effect
        mock_repository.get_paper.return_value = None
        mock_repository.create_paper.return_value = MagicMock()

        result = await importer.batch_import(
            ["10.1234/test0", "10.1234/test1", "10.1234/test2"],
            create_authors=False,
        )

        assert result.total == 3
        assert result.created == 3
        assert result.failed == 0

    @pytest.mark.asyncio
    async def test_batch_import_with_failures(
        self,
        importer,
        mock_aggregator,
        mock_repository,
    ):
        """Test batch import handles partial failures."""
        # First paper succeeds, second fails
        paper1 = NormalizedPaper(title="Test Paper Number 1", source="test", doi="10.1234/test1")

        mock_aggregator.get_paper.side_effect = [
            AggregatedResult(paper=paper1, sources=["test"]),
            NotFoundError(resource_type="paper", identifier="test2", source="test"),
        ]
        mock_repository.get_paper.return_value = None
        mock_repository.create_paper.return_value = MagicMock()

        result = await importer.batch_import(
            ["10.1234/test1", "10.1234/test2"],
            create_authors=False,
        )

        assert result.total == 2
        assert result.created == 1
        assert result.failed == 1
        assert "10.1234/test2" in result.errors

    @pytest.mark.asyncio
    async def test_batch_import_progress_callback(
        self,
        importer,
        mock_aggregator,
        mock_repository,
    ):
        """Test that progress callback is called."""
        paper = NormalizedPaper(title="Test Paper Title", source="test", doi="10.1234/test")
        mock_aggregator.get_paper.return_value = AggregatedResult(
            paper=paper, sources=["test"]
        )
        mock_repository.get_paper.return_value = None
        mock_repository.create_paper.return_value = MagicMock()

        progress_calls = []

        def callback(current, total, result):
            progress_calls.append((current, total))

        await importer.batch_import(
            ["10.1234/test"],
            progress_callback=callback,
            create_authors=False,
        )

        assert len(progress_calls) == 1
        assert progress_calls[0] == (1, 1)

    @pytest.mark.asyncio
    async def test_import_author_papers_semantic_scholar(
        self,
        importer,
        mock_aggregator,
        mock_repository,
    ):
        """Test importing papers by author from Semantic Scholar."""
        # Mock author papers response
        mock_aggregator.semantic_scholar.get_author_papers.return_value = {
            "data": [
                {"externalIds": {"DOI": "10.1234/test1"}},
                {"externalIds": {"DOI": "10.1234/test2"}},
            ]
        }

        # Mock paper fetching
        paper = NormalizedPaper(title="Test Paper Title", source="test", doi="10.1234/test1")
        mock_aggregator.get_paper.return_value = AggregatedResult(
            paper=paper, sources=["semantic_scholar"]
        )
        mock_repository.get_paper.return_value = None
        mock_repository.create_paper.return_value = MagicMock()

        result = await importer.import_author_papers(
            "author123",
            source="semantic_scholar",
            limit=10,
        )

        assert result.total == 2
        mock_aggregator.semantic_scholar.get_author_papers.assert_called_once_with(
            "author123", limit=10
        )

    @pytest.mark.asyncio
    async def test_import_author_papers_openalex(
        self,
        importer,
        mock_aggregator,
        mock_repository,
    ):
        """Test importing papers by author from OpenAlex."""
        mock_aggregator.openalex.get_author_works.return_value = {
            "data": [
                {"doi": "https://doi.org/10.1234/test1"},
            ]
        }

        paper = NormalizedPaper(title="Test Paper Title", source="test", doi="10.1234/test1")
        mock_aggregator.get_paper.return_value = AggregatedResult(
            paper=paper, sources=["openalex"]
        )
        mock_repository.get_paper.return_value = None
        mock_repository.create_paper.return_value = MagicMock()

        result = await importer.import_author_papers(
            "A123",
            source="openalex",
            limit=10,
        )

        assert result.total == 1

    @pytest.mark.asyncio
    async def test_import_author_papers_unsupported_source(self, importer):
        """Test error for unsupported source."""
        result = await importer.import_author_papers(
            "author123",
            source="unsupported",
        )

        assert "Unsupported source" in result.errors.get("source", "")

    def test_lazy_initialization(self):
        """Test that aggregator and repository are lazily initialized."""
        importer = PaperImporter()

        # Access properties to trigger lazy init
        with patch("agentic_kg.data_acquisition.importer.get_paper_aggregator") as mock_agg:
            mock_agg.return_value = MagicMock()
            _ = importer.aggregator
            mock_agg.assert_called_once()

        with patch("agentic_kg.data_acquisition.importer.get_repository") as mock_repo:
            mock_repo.return_value = MagicMock()
            _ = importer.repository
            mock_repo.assert_called_once()


class TestGetPaperImporter:
    """Tests for singleton access."""

    def test_returns_importer_instance(self):
        """Test that get_paper_importer returns an importer."""
        importer = get_paper_importer()

        assert isinstance(importer, PaperImporter)

    def test_returns_same_instance(self):
        """Test that get_paper_importer returns singleton."""
        importer1 = get_paper_importer()
        importer2 = get_paper_importer()

        assert importer1 is importer2

    def test_reset_clears_singleton(self):
        """Test that reset clears the singleton."""
        importer1 = get_paper_importer()
        reset_paper_importer()
        importer2 = get_paper_importer()

        assert importer1 is not importer2
