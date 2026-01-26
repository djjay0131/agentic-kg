"""
Unit tests for paper metadata normalization.
"""

import pytest

from agentic_kg.data_acquisition.normalizer import (
    NormalizedAuthor,
    NormalizedPaper,
    PaperNormalizer,
    get_paper_normalizer,
    merge_normalized_papers,
)
from agentic_kg.data_acquisition.exceptions import NormalizationError


class TestNormalizedAuthor:
    """Tests for NormalizedAuthor dataclass."""

    def test_create_basic_author(self):
        """Test creating an author with minimal fields."""
        author = NormalizedAuthor(name="John Doe")

        assert author.name == "John Doe"
        assert author.external_ids == {}
        assert author.affiliations == []
        assert author.position is None

    def test_create_full_author(self):
        """Test creating an author with all fields."""
        author = NormalizedAuthor(
            name="Jane Smith",
            external_ids={"orcid": "0000-0001-1234-5678", "semantic_scholar": "12345"},
            affiliations=["MIT", "Stanford"],
            position=1,
        )

        assert author.name == "Jane Smith"
        assert author.external_ids["orcid"] == "0000-0001-1234-5678"
        assert len(author.affiliations) == 2
        assert author.position == 1

    def test_to_dict(self):
        """Test author serialization to dictionary."""
        author = NormalizedAuthor(
            name="Test Author",
            external_ids={"ss": "123"},
            affiliations=["University"],
            position=2,
        )

        result = author.to_dict()

        assert result["name"] == "Test Author"
        assert result["external_ids"] == {"ss": "123"}
        assert result["affiliations"] == ["University"]
        assert result["position"] == 2


class TestNormalizedPaper:
    """Tests for NormalizedPaper dataclass."""

    def test_create_minimal_paper(self):
        """Test creating a paper with minimal required fields."""
        paper = NormalizedPaper(
            title="Test Paper",
            source="semantic_scholar",
        )

        assert paper.title == "Test Paper"
        assert paper.source == "semantic_scholar"
        assert paper.doi is None
        assert paper.authors == []
        assert paper.is_open_access is False

    def test_create_full_paper(self):
        """Test creating a paper with all fields."""
        authors = [NormalizedAuthor(name="Author 1", position=1)]

        paper = NormalizedPaper(
            title="Full Test Paper",
            source="arxiv",
            doi="10.1234/test",
            external_ids={"arxiv": "2106.01345"},
            abstract="This is a test abstract.",
            year=2023,
            publication_date="2023-06-15",
            venue="NeurIPS",
            authors=authors,
            citation_count=100,
            reference_count=50,
            fields_of_study=["AI", "ML"],
            publication_types=["Conference"],
            is_open_access=True,
            pdf_url="https://example.com/paper.pdf",
            abstract_url="https://example.com/abs",
        )

        assert paper.title == "Full Test Paper"
        assert paper.doi == "10.1234/test"
        assert len(paper.authors) == 1
        assert paper.citation_count == 100

    def test_to_dict(self):
        """Test paper serialization to dictionary."""
        paper = NormalizedPaper(
            title="Test Paper",
            source="openalex",
            doi="10.1234/test",
            year=2023,
        )

        result = paper.to_dict()

        assert result["title"] == "Test Paper"
        assert result["source"] == "openalex"
        assert result["doi"] == "10.1234/test"
        assert result["year"] == 2023
        assert "authors" in result

    def test_to_dict_with_authors(self):
        """Test paper serialization includes author dicts."""
        paper = NormalizedPaper(
            title="Test",
            source="test",
            authors=[
                NormalizedAuthor(name="Author 1", position=1),
                NormalizedAuthor(name="Author 2", position=2),
            ],
        )

        result = paper.to_dict()

        assert len(result["authors"]) == 2
        assert result["authors"][0]["name"] == "Author 1"


class TestPaperNormalizer:
    """Tests for PaperNormalizer class."""

    @pytest.fixture
    def normalizer(self):
        """Create a normalizer instance."""
        return PaperNormalizer()

    def test_normalize_unknown_source(self, normalizer):
        """Test normalization fails for unknown source."""
        with pytest.raises(NormalizationError) as exc_info:
            normalizer.normalize({"title": "Test"}, source="unknown")

        assert "Unknown source" in str(exc_info.value)

    def test_normalize_semantic_scholar(
        self, normalizer, sample_semantic_scholar_paper
    ):
        """Test normalizing Semantic Scholar paper data."""
        paper = normalizer.normalize(sample_semantic_scholar_paper, "semantic_scholar")

        assert paper.title == "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding"
        assert paper.source == "semantic_scholar"
        assert paper.doi == "10.18653/v1/N18-1202"
        assert paper.external_ids["arxiv"] == "1802.05365"
        assert paper.external_ids["semantic_scholar"] == "649def34f8be52c8b66281af98ae884c09aef38b"
        assert paper.year == 2019
        assert paper.venue == "NAACL"
        assert len(paper.authors) == 2
        assert paper.authors[0].name == "Jacob Devlin"
        assert paper.authors[0].external_ids["semantic_scholar"] == "1234"
        assert paper.citation_count == 50000
        assert paper.is_open_access is True
        assert paper.pdf_url == "https://arxiv.org/pdf/1810.04805.pdf"

    def test_normalize_semantic_scholar_missing_fields(self, normalizer):
        """Test normalizing SS paper with missing optional fields."""
        data = {
            "paperId": "abc123",
            "title": "Minimal Paper",
        }

        paper = normalizer.normalize(data, "semantic_scholar")

        assert paper.title == "Minimal Paper"
        assert paper.doi is None
        assert paper.authors == []
        assert paper.citation_count is None

    def test_normalize_arxiv(self, normalizer, sample_arxiv_paper):
        """Test normalizing arXiv paper data."""
        paper = normalizer.normalize(sample_arxiv_paper, "arxiv")

        assert paper.title == "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding"
        assert paper.source == "arxiv"
        assert paper.doi == "10.18653/v1/N18-1202"
        assert paper.external_ids["arxiv"] == "1810.04805"
        assert paper.year == 2018
        assert paper.publication_date == "2018-10-11"
        assert paper.abstract == "We introduce BERT, a new language representation model..."
        assert len(paper.authors) == 2
        assert paper.fields_of_study == ["cs.CL"]
        assert paper.publication_types == ["preprint"]
        assert paper.is_open_access is True
        assert paper.pdf_url == "https://arxiv.org/pdf/1810.04805.pdf"

    def test_normalize_arxiv_year_from_id(self, normalizer):
        """Test extracting year from arXiv ID when date parsing fails."""
        data = {
            "id": "2106.01345",
            "title": "Test Paper",
            "published": "invalid-date",
        }

        paper = normalizer.normalize(data, "arxiv")

        # Year extracted from ID prefix "21" -> 2021
        assert paper.year == 2021

    def test_normalize_openalex(self, normalizer, sample_openalex_work):
        """Test normalizing OpenAlex work data."""
        # Add reconstructed abstract (normally done by client)
        sample_openalex_work["abstract"] = "We introduce BERT"

        paper = normalizer.normalize(sample_openalex_work, "openalex")

        assert paper.title == "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding"
        assert paper.source == "openalex"
        assert paper.doi == "10.18653/v1/n19-1423"
        assert paper.external_ids["openalex"] == "W2963403868"
        assert paper.year == 2019
        assert paper.publication_date == "2019-06-01"
        assert len(paper.authors) == 1
        assert paper.authors[0].name == "Jacob Devlin"
        assert paper.authors[0].affiliations == ["Google"]
        assert paper.authors[0].external_ids["orcid"] == "0000-0001-1234-5678"
        assert paper.citation_count == 55000
        assert paper.venue == "NAACL-HLT"
        assert paper.is_open_access is True

    def test_normalize_openalex_url_cleanup(self, normalizer):
        """Test that OpenAlex URLs are cleaned up properly."""
        data = {
            "id": "https://openalex.org/W123456",
            "doi": "https://doi.org/10.1234/test",
            "title": "Test",
        }

        paper = normalizer.normalize(data, "openalex")

        assert paper.external_ids["openalex"] == "W123456"
        assert paper.doi == "10.1234/test"

    def test_normalize_keeps_raw_data(self, normalizer, sample_semantic_scholar_paper):
        """Test that raw data is kept when requested."""
        paper = normalizer.normalize(
            sample_semantic_scholar_paper, "semantic_scholar", keep_raw=True
        )

        assert paper.raw_data == sample_semantic_scholar_paper

    def test_normalize_exception_handling(self, normalizer):
        """Test that unexpected errors are wrapped in NormalizationError."""
        # Pass data that will cause an error during processing
        with pytest.raises(NormalizationError):
            # This should fail because normalize expects a dict
            normalizer.normalize("not a dict", "semantic_scholar")


class TestMergeNormalizedPapers:
    """Tests for merge_normalized_papers function."""

    def test_merge_empty_list(self):
        """Test merging empty list raises error."""
        with pytest.raises(ValueError) as exc_info:
            merge_normalized_papers([])

        assert "Cannot merge empty" in str(exc_info.value)

    def test_merge_single_paper(self):
        """Test merging single paper returns it unchanged."""
        paper = NormalizedPaper(title="Single", source="test", doi="10.1234/test")

        result = merge_normalized_papers([paper])

        assert result is paper

    def test_merge_combines_external_ids(self):
        """Test that external IDs from all sources are combined."""
        paper1 = NormalizedPaper(
            title="Paper",
            source="semantic_scholar",
            external_ids={"semantic_scholar": "ss123", "doi": "10.1234/test"},
        )
        paper2 = NormalizedPaper(
            title="Paper",
            source="openalex",
            external_ids={"openalex": "W123", "mag": "456"},
        )

        result = merge_normalized_papers([paper1, paper2])

        assert result.external_ids["semantic_scholar"] == "ss123"
        assert result.external_ids["openalex"] == "W123"
        assert result.external_ids["mag"] == "456"

    def test_merge_prefers_first_doi(self):
        """Test that first available DOI is used."""
        paper1 = NormalizedPaper(title="Paper", source="test1", doi=None)
        paper2 = NormalizedPaper(title="Paper", source="test2", doi="10.1234/second")
        paper3 = NormalizedPaper(title="Paper", source="test3", doi="10.1234/third")

        result = merge_normalized_papers([paper1, paper2, paper3])

        assert result.doi == "10.1234/second"

    def test_merge_prefers_longest_title(self):
        """Test that longest title is preferred."""
        paper1 = NormalizedPaper(title="Short", source="test1")
        paper2 = NormalizedPaper(title="Much Longer Title Here", source="test2")

        result = merge_normalized_papers([paper1, paper2])

        assert result.title == "Much Longer Title Here"

    def test_merge_prefers_longest_abstract(self):
        """Test that longest abstract is preferred."""
        paper1 = NormalizedPaper(
            title="Paper",
            source="test1",
            abstract="Short abstract.",
        )
        paper2 = NormalizedPaper(
            title="Paper",
            source="test2",
            abstract="This is a much longer and more detailed abstract with more information.",
        )

        result = merge_normalized_papers([paper1, paper2])

        assert "much longer" in result.abstract

    def test_merge_prefers_highest_citations(self):
        """Test that highest citation count is used."""
        paper1 = NormalizedPaper(title="Paper", source="test1", citation_count=100)
        paper2 = NormalizedPaper(title="Paper", source="test2", citation_count=150)

        result = merge_normalized_papers([paper1, paper2])

        assert result.citation_count == 150

    def test_merge_prefers_authors_with_more_details(self):
        """Test that authors with more metadata are preferred."""
        # Paper 1: authors with no extra info
        paper1 = NormalizedPaper(
            title="Paper",
            source="test1",
            authors=[
                NormalizedAuthor(name="Author 1"),
                NormalizedAuthor(name="Author 2"),
            ],
        )
        # Paper 2: authors with affiliations
        paper2 = NormalizedPaper(
            title="Paper",
            source="test2",
            authors=[
                NormalizedAuthor(name="Author 1", affiliations=["MIT"], external_ids={"orcid": "123"}),
                NormalizedAuthor(name="Author 2", affiliations=["Stanford"]),
            ],
        )

        result = merge_normalized_papers([paper1, paper2])

        # Should use paper2's authors because they have more metadata
        assert result.authors[0].affiliations == ["MIT"]

    def test_merge_combines_fields_of_study(self):
        """Test that fields of study are merged and deduplicated."""
        paper1 = NormalizedPaper(
            title="Paper",
            source="test1",
            fields_of_study=["AI", "ML"],
        )
        paper2 = NormalizedPaper(
            title="Paper",
            source="test2",
            fields_of_study=["ML", "NLP"],
        )

        result = merge_normalized_papers([paper1, paper2])

        assert set(result.fields_of_study) == {"AI", "ML", "NLP"}

    def test_merge_open_access_true_if_any(self):
        """Test that open access is true if any source reports it."""
        paper1 = NormalizedPaper(title="Paper", source="test1", is_open_access=False)
        paper2 = NormalizedPaper(title="Paper", source="test2", is_open_access=True)

        result = merge_normalized_papers([paper1, paper2])

        assert result.is_open_access is True

    def test_merge_source_becomes_merged(self):
        """Test that source field becomes 'merged' after merging."""
        paper1 = NormalizedPaper(title="Paper", source="semantic_scholar")
        paper2 = NormalizedPaper(title="Paper", source="openalex")

        result = merge_normalized_papers([paper1, paper2])

        assert result.source == "merged"

    def test_merge_prefers_most_specific_publication_date(self):
        """Test that most specific publication date is preferred."""
        paper1 = NormalizedPaper(title="Paper", source="test1", publication_date="2023")
        paper2 = NormalizedPaper(title="Paper", source="test2", publication_date="2023-06-15")

        result = merge_normalized_papers([paper1, paper2])

        assert result.publication_date == "2023-06-15"


class TestGetPaperNormalizer:
    """Tests for singleton access."""

    def test_returns_normalizer_instance(self):
        """Test that get_paper_normalizer returns a PaperNormalizer."""
        normalizer = get_paper_normalizer()

        assert isinstance(normalizer, PaperNormalizer)

    def test_returns_same_instance(self):
        """Test that get_paper_normalizer returns singleton."""
        normalizer1 = get_paper_normalizer()
        normalizer2 = get_paper_normalizer()

        assert normalizer1 is normalizer2
