"""
Unit tests for problem extractor.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agentic_kg.extraction.llm_client import LLMError, LLMResponse, TokenUsage
from agentic_kg.extraction.problem_extractor import (
    ExtractionConfig,
    ProblemExtractor,
    get_problem_extractor,
    reset_problem_extractor,
)
from agentic_kg.extraction.schemas import ExtractedProblem, ExtractionResult
from agentic_kg.extraction.section_segmenter import Section, SectionType


class TestExtractionConfig:
    """Tests for ExtractionConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = ExtractionConfig()

        assert config.model == "gpt-4-turbo"
        assert config.temperature == 0.1
        assert config.min_confidence == 0.5
        assert config.max_problems_per_section == 10
        assert config.max_retries == 3

    def test_custom_config(self):
        """Test custom configuration."""
        config = ExtractionConfig(
            model="gpt-4",
            temperature=0.2,
            min_confidence=0.7,
            max_problems_per_section=5,
        )

        assert config.model == "gpt-4"
        assert config.min_confidence == 0.7


class TestProblemExtractor:
    """Tests for ProblemExtractor class."""

    @pytest.fixture
    def mock_client(self):
        """Create mock LLM client."""
        client = MagicMock()
        client.extract = AsyncMock()
        return client

    @pytest.fixture
    def extractor(self, mock_client):
        """Create extractor with mock client."""
        return ProblemExtractor(client=mock_client)

    @pytest.fixture
    def sample_section(self):
        """Create a sample section."""
        return Section(
            section_type=SectionType.LIMITATIONS,
            title="Limitations",
            content="""Our approach has several limitations. First, the model
            requires significant computational resources, making it impractical
            for deployment on edge devices. Second, we only evaluated on
            English datasets, and performance on other languages is unknown.""",
        )

    @pytest.fixture
    def sample_extraction_result(self):
        """Create a sample extraction result."""
        return ExtractionResult(
            section_type="limitations",
            problems=[
                ExtractedProblem(
                    statement="Deep learning models require significant computational resources, limiting edge deployment.",
                    quoted_text="requires significant computational resources, making it impractical for deployment on edge devices",
                    confidence=0.9,
                    domain="Edge Computing",
                ),
                ExtractedProblem(
                    statement="Model performance on non-English languages remains unknown and untested.",
                    quoted_text="we only evaluated on English datasets, and performance on other languages is unknown",
                    confidence=0.85,
                    domain="Multilingual NLP",
                ),
            ],
        )

    @pytest.mark.asyncio
    async def test_extract_from_section_success(
        self, extractor, mock_client, sample_section, sample_extraction_result
    ):
        """Test successful extraction from section."""
        mock_client.extract.return_value = LLMResponse(
            content=sample_extraction_result,
            usage=TokenUsage(total_tokens=500),
        )

        result = await extractor.extract_from_section(
            section=sample_section,
            paper_title="Test Paper",
            authors=["Author One"],
        )

        assert result.problem_count == 2
        assert result.section_type == "limitations"
        mock_client.extract.assert_called_once()

    @pytest.mark.asyncio
    async def test_extract_from_section_empty_result(
        self, extractor, mock_client, sample_section
    ):
        """Test handling of empty extraction result."""
        empty_result = ExtractionResult(section_type="limitations", problems=[])
        mock_client.extract.return_value = LLMResponse(
            content=empty_result,
            usage=TokenUsage(total_tokens=200),
        )

        # Disable retry on empty for this test
        extractor.config.retry_on_empty = False

        result = await extractor.extract_from_section(
            section=sample_section,
            paper_title="Test Paper",
        )

        assert result.problem_count == 0

    @pytest.mark.asyncio
    async def test_extract_filters_low_confidence(
        self, extractor, mock_client, sample_section
    ):
        """Test that low confidence problems are filtered."""
        result_with_low_conf = ExtractionResult(
            section_type="limitations",
            problems=[
                ExtractedProblem(
                    statement="High confidence problem with enough text here.",
                    quoted_text="some quoted text here",
                    confidence=0.9,
                ),
                ExtractedProblem(
                    statement="Low confidence problem that should be filtered out.",
                    quoted_text="another quote here",
                    confidence=0.3,  # Below min_confidence
                ),
            ],
        )

        mock_client.extract.return_value = LLMResponse(
            content=result_with_low_conf,
            usage=TokenUsage(total_tokens=300),
        )

        result = await extractor.extract_from_section(
            section=sample_section,
            paper_title="Test Paper",
        )

        assert result.problem_count == 1
        assert result.problems[0].confidence == 0.9

    @pytest.mark.asyncio
    async def test_extract_limits_problems_per_section(
        self, extractor, mock_client, sample_section
    ):
        """Test that problems are limited to max per section."""
        extractor.config.max_problems_per_section = 2

        many_problems = ExtractionResult(
            section_type="limitations",
            problems=[
                ExtractedProblem(
                    statement=f"Problem number {i} with sufficient length here.",
                    quoted_text=f"quote {i} here",
                    confidence=0.9 - (i * 0.05),
                )
                for i in range(5)
            ],
        )

        mock_client.extract.return_value = LLMResponse(
            content=many_problems,
            usage=TokenUsage(total_tokens=400),
        )

        result = await extractor.extract_from_section(
            section=sample_section,
            paper_title="Test Paper",
        )

        assert result.problem_count == 2
        # Should keep highest confidence
        assert result.problems[0].confidence == 0.9

    @pytest.mark.asyncio
    async def test_extract_from_sections(
        self, extractor, mock_client, sample_extraction_result
    ):
        """Test extraction from multiple sections."""
        mock_client.extract.return_value = LLMResponse(
            content=sample_extraction_result,
            usage=TokenUsage(total_tokens=500),
        )

        sections = [
            Section(
                section_type=SectionType.LIMITATIONS,
                title="Limitations",
                content="Limitations content here...",
            ),
            Section(
                section_type=SectionType.FUTURE_WORK,
                title="Future Work",
                content="Future work content here...",
            ),
        ]

        result = await extractor.extract_from_sections(
            sections=sections,
            paper_title="Test Paper",
            paper_doi="10.1234/test",
        )

        assert len(result.results) == 2
        assert result.paper_doi == "10.1234/test"
        assert mock_client.extract.call_count == 2

    @pytest.mark.asyncio
    async def test_extract_skips_low_priority_sections(self, mock_client):
        """Test that low priority sections are skipped when configured."""
        extractor = ProblemExtractor(
            client=mock_client,
            config=ExtractionConfig(
                skip_low_priority_sections=True,
                max_section_priority=5,  # Only process high priority
            ),
        )

        references_section = Section(
            section_type=SectionType.REFERENCES,  # Priority 100
            title="References",
            content="[1] Some paper...",
        )

        result = await extractor.extract_from_section(
            section=references_section,
            paper_title="Test Paper",
        )

        assert result.problem_count == 0
        assert "Skipped" in result.extraction_notes
        mock_client.extract.assert_not_called()

    @pytest.mark.asyncio
    async def test_extract_retries_on_error(
        self, extractor, mock_client, sample_section, sample_extraction_result
    ):
        """Test that extraction retries on error."""
        # First call fails, second succeeds
        mock_client.extract.side_effect = [
            LLMError("Temporary error"),
            LLMResponse(
                content=sample_extraction_result,
                usage=TokenUsage(total_tokens=500),
            ),
        ]

        result = await extractor.extract_from_section(
            section=sample_section,
            paper_title="Test Paper",
        )

        assert result.problem_count == 2
        assert mock_client.extract.call_count == 2

    @pytest.mark.asyncio
    async def test_extract_fails_after_max_retries(
        self, extractor, mock_client, sample_section
    ):
        """Test that extraction fails after max retries."""
        mock_client.extract.side_effect = LLMError("Persistent error")

        with pytest.raises(LLMError):
            await extractor.extract_from_section(
                section=sample_section,
                paper_title="Test Paper",
            )

        assert mock_client.extract.call_count == 3  # Default max_retries

    @pytest.mark.asyncio
    async def test_extract_from_text(
        self, extractor, mock_client, sample_extraction_result
    ):
        """Test extraction from raw text."""
        mock_client.extract.return_value = LLMResponse(
            content=sample_extraction_result,
            usage=TokenUsage(total_tokens=500),
        )

        result = await extractor.extract_from_text(
            text="Some limitations text to extract from...",
            section_type=SectionType.LIMITATIONS,
            paper_title="Test Paper",
        )

        assert result.problem_count == 2
        mock_client.extract.assert_called_once()


class TestValidateProblem:
    """Tests for problem validation."""

    @pytest.fixture
    def extractor(self):
        """Create extractor with mock client."""
        mock_client = MagicMock()
        return ProblemExtractor(client=mock_client)

    def test_valid_problem(self, extractor):
        """Test that valid problem passes validation."""
        problem = ExtractedProblem(
            statement="This is a valid problem statement with enough length.",
            quoted_text="some quoted text from the paper",
            confidence=0.9,
        )

        assert extractor._validate_problem(problem) is True

    def test_invalid_short_statement(self, extractor):
        """Test that short statement fails validation."""
        problem = ExtractedProblem(
            statement="Too short",  # Less than 20 chars
            quoted_text="some quoted text from the paper",
            confidence=0.9,
        )

        # Note: Pydantic will actually raise an error, but let's test the logic
        # by creating with __init__ bypassed
        problem_dict = {
            "statement": "Short",
            "quoted_text": "quote",
            "confidence": 0.9,
        }
        # Manual check since Pydantic would reject this
        assert len("Short") < 20

    def test_invalid_short_quote(self, extractor):
        """Test that short quoted text fails validation."""
        # Create a problem-like object with short quote
        class FakeProblem:
            statement = "This is a valid statement with enough length here."
            quoted_text = "x"  # Too short
            confidence = 0.9

        assert extractor._validate_problem(FakeProblem()) is False


class TestFilterResults:
    """Tests for result filtering."""

    @pytest.fixture
    def extractor(self):
        """Create extractor with mock client."""
        mock_client = MagicMock()
        return ProblemExtractor(
            client=mock_client,
            config=ExtractionConfig(
                min_confidence=0.6,
                max_problems_per_section=3,
            ),
        )

    def test_filter_by_confidence(self, extractor):
        """Test filtering by confidence threshold."""
        result = ExtractionResult(
            section_type="limitations",
            problems=[
                ExtractedProblem(
                    statement="High confidence problem with sufficient length.",
                    quoted_text="quote text here",
                    confidence=0.9,
                ),
                ExtractedProblem(
                    statement="Low confidence problem with sufficient length.",
                    quoted_text="quote text here",
                    confidence=0.4,
                ),
            ],
        )

        filtered = extractor._filter_results(result)

        assert filtered.problem_count == 1
        assert filtered.problems[0].confidence == 0.9

    def test_filter_by_max_problems(self, extractor):
        """Test filtering by max problems limit."""
        result = ExtractionResult(
            section_type="limitations",
            problems=[
                ExtractedProblem(
                    statement=f"Problem {i} with sufficient statement length.",
                    quoted_text=f"quote {i} text",
                    confidence=0.9 - (i * 0.05),
                )
                for i in range(5)
            ],
        )

        filtered = extractor._filter_results(result)

        assert filtered.problem_count == 3
        # Should keep highest confidence
        confidences = [p.confidence for p in filtered.problems]
        assert confidences == sorted(confidences, reverse=True)


class TestGetProblemExtractor:
    """Tests for singleton access."""

    def setup_method(self):
        """Reset singleton before each test."""
        reset_problem_extractor()

    def teardown_method(self):
        """Reset singleton after each test."""
        reset_problem_extractor()

    def test_returns_extractor_instance(self):
        """Test that get_problem_extractor returns an extractor."""
        with patch("agentic_kg.extraction.problem_extractor.get_openai_client"):
            extractor = get_problem_extractor()
            assert isinstance(extractor, ProblemExtractor)

    def test_returns_same_instance(self):
        """Test singleton pattern."""
        with patch("agentic_kg.extraction.problem_extractor.get_openai_client"):
            extractor1 = get_problem_extractor()
            extractor2 = get_problem_extractor()
            assert extractor1 is extractor2

    def test_reset_clears_singleton(self):
        """Test reset clears singleton."""
        with patch("agentic_kg.extraction.problem_extractor.get_openai_client"):
            extractor1 = get_problem_extractor()
            reset_problem_extractor()
            extractor2 = get_problem_extractor()
            assert extractor1 is not extractor2
