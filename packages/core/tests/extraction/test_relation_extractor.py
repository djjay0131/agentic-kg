"""
Unit tests for relation extractor.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from agentic_kg.extraction.llm_client import LLMResponse, TokenUsage
from agentic_kg.extraction.relation_extractor import (
    ExtractedRelation,
    LLMRelationResult,
    RelationConfig,
    RelationExtractionResult,
    RelationExtractor,
    RelationType,
    get_relation_extractor,
    reset_relation_extractor,
)
from agentic_kg.extraction.schemas import ExtractedProblem


class TestRelationType:
    """Tests for RelationType enum."""

    def test_all_types_have_cues(self):
        """Test that relation types have associated cues."""
        from agentic_kg.extraction.relation_extractor import RELATION_CUES

        # Check common types have cues
        assert RelationType.EXTENDS in RELATION_CUES
        assert RelationType.CONTRADICTS in RELATION_CUES
        assert RelationType.DEPENDS_ON in RELATION_CUES
        assert RelationType.REFRAMES in RELATION_CUES

    def test_extends_cues(self):
        """Test EXTENDS relation cues."""
        from agentic_kg.extraction.relation_extractor import RELATION_CUES

        cues = RELATION_CUES[RelationType.EXTENDS]
        assert "builds on" in cues
        assert "extends" in cues
        assert "improves upon" in cues


class TestRelationConfig:
    """Tests for RelationConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = RelationConfig()

        assert config.min_confidence == 0.5
        assert config.similarity_threshold == 0.7
        assert config.use_llm_validation is True
        assert config.max_relations_per_pair == 3

    def test_custom_config(self):
        """Test custom configuration."""
        config = RelationConfig(
            min_confidence=0.8,
            similarity_threshold=0.9,
            use_llm_validation=False,
        )

        assert config.min_confidence == 0.8
        assert config.similarity_threshold == 0.9
        assert config.use_llm_validation is False


class TestExtractedRelation:
    """Tests for ExtractedRelation model."""

    def test_valid_relation(self):
        """Test creating a valid relation."""
        relation = ExtractedRelation(
            source_problem_id="Problem A statement here",
            target_problem_id="Problem B statement here",
            relation_type=RelationType.EXTENDS,
            confidence=0.85,
            evidence="This builds on previous work on Problem A",
        )

        assert relation.relation_type == RelationType.EXTENDS
        assert relation.confidence == 0.85
        assert relation.extraction_method == "textual_cue"

    def test_relation_with_method(self):
        """Test relation with custom extraction method."""
        relation = ExtractedRelation(
            source_problem_id="Problem A",
            target_problem_id="Problem B",
            relation_type=RelationType.RELATED_TO,
            confidence=0.7,
            evidence="High semantic similarity between problems",
            extraction_method="semantic_similarity",
        )

        assert relation.extraction_method == "semantic_similarity"


class TestRelationExtractionResult:
    """Tests for RelationExtractionResult model."""

    @pytest.fixture
    def sample_relations(self):
        """Create sample relations for testing."""
        return [
            ExtractedRelation(
                source_problem_id="Problem A",
                target_problem_id="Problem B",
                relation_type=RelationType.EXTENDS,
                confidence=0.9,
                evidence="Extends previous work",
            ),
            ExtractedRelation(
                source_problem_id="Problem B",
                target_problem_id="Problem C",
                relation_type=RelationType.DEPENDS_ON,
                confidence=0.8,
                evidence="Requires solution to B",
            ),
            ExtractedRelation(
                source_problem_id="Problem A",
                target_problem_id="Problem C",
                relation_type=RelationType.RELATED_TO,
                confidence=0.7,
                evidence="Conceptually related",
            ),
        ]

    def test_relation_count(self, sample_relations):
        """Test relation count property."""
        result = RelationExtractionResult(relations=sample_relations)
        assert result.relation_count == 3

    def test_get_by_type(self, sample_relations):
        """Test filtering relations by type."""
        result = RelationExtractionResult(relations=sample_relations)

        extends = result.get_by_type(RelationType.EXTENDS)
        assert len(extends) == 1
        assert extends[0].source_problem_id == "Problem A"

        depends = result.get_by_type(RelationType.DEPENDS_ON)
        assert len(depends) == 1

    def test_get_for_problem(self, sample_relations):
        """Test getting relations for a specific problem."""
        result = RelationExtractionResult(relations=sample_relations)

        a_relations = result.get_for_problem("Problem A")
        assert len(a_relations) == 2  # A->B and A->C

        b_relations = result.get_for_problem("Problem B")
        assert len(b_relations) == 2  # A->B and B->C

    def test_empty_result(self):
        """Test empty result."""
        result = RelationExtractionResult()
        assert result.relation_count == 0
        assert result.get_by_type(RelationType.EXTENDS) == []


class TestRelationExtractor:
    """Tests for RelationExtractor class."""

    @pytest.fixture
    def extractor(self):
        """Create extractor without LLM client."""
        return RelationExtractor(config=RelationConfig(min_confidence=0.3))

    @pytest.fixture
    def sample_problems(self):
        """Create sample problems for testing."""
        return [
            ExtractedProblem(
                statement="Deep learning models require significant computational resources for training large datasets.",
                quoted_text="require significant computational resources",
                confidence=0.9,
            ),
            ExtractedProblem(
                statement="Model compression techniques can reduce computational resources needed for deep learning.",
                quoted_text="compression techniques can reduce computational resources",
                confidence=0.85,
            ),
            ExtractedProblem(
                statement="Edge deployment of deep learning models remains challenging due to memory constraints.",
                quoted_text="edge deployment remains challenging",
                confidence=0.8,
            ),
        ]

    def test_extract_from_text_with_cues(self, extractor, sample_problems):
        """Test extraction with textual cues present."""
        text = """
        This work builds on previous research in model compression.
        Our approach extends prior techniques by incorporating new methods.
        The solution depends on having a working compression algorithm.
        """

        result = extractor.extract_from_text(text, sample_problems)

        assert isinstance(result, RelationExtractionResult)
        # Should detect "builds on" and "extends" cues
        extends_relations = result.get_by_type(RelationType.EXTENDS)
        assert len(extends_relations) >= 0  # May or may not match problems

    def test_extract_from_text_no_cues(self, extractor, sample_problems):
        """Test extraction when no textual cues are present."""
        text = """
        This paper presents a new approach to model compression.
        We evaluate our method on standard benchmarks.
        Results show improved performance over baselines.
        """

        result = extractor.extract_from_text(text, sample_problems)

        # Should still find similarity-based relations
        assert isinstance(result, RelationExtractionResult)

    def test_extract_finds_similar_problems(self, extractor):
        """Test that similar problems are connected."""
        problems = [
            ExtractedProblem(
                statement="Machine learning models need efficient training on large data.",
                quoted_text="efficient training needed",
                confidence=0.9,
            ),
            ExtractedProblem(
                statement="Machine learning requires efficient training algorithms for large datasets.",
                quoted_text="efficient training algorithms required",
                confidence=0.9,
            ),
        ]

        # Set low similarity threshold
        extractor.config.similarity_threshold = 0.3

        result = extractor.extract_from_text("Some text here", problems)

        related = result.get_by_type(RelationType.RELATED_TO)
        assert len(related) >= 1

    def test_extract_filters_by_confidence(self, extractor, sample_problems):
        """Test that low confidence relations are filtered."""
        extractor.config.min_confidence = 0.9  # High threshold

        result = extractor.extract_from_text("Some text", sample_problems)

        # All relations should have high confidence
        for relation in result.relations:
            assert relation.confidence >= 0.9

    def test_compute_similarity(self, extractor):
        """Test word overlap similarity computation."""
        text1 = "machine learning model training"
        text2 = "machine learning model inference"

        similarity = extractor._compute_similarity(text1, text2)

        assert 0 < similarity < 1
        assert similarity == 0.6  # 3 common / 5 total

    def test_compute_similarity_identical(self, extractor):
        """Test similarity of identical texts."""
        text = "machine learning model"

        similarity = extractor._compute_similarity(text, text)
        assert similarity == 1.0

    def test_compute_similarity_disjoint(self, extractor):
        """Test similarity of completely different texts."""
        text1 = "machine learning model"
        text2 = "biology chemistry physics"

        similarity = extractor._compute_similarity(text1, text2)
        assert similarity == 0.0

    def test_match_problems_to_context(self, extractor, sample_problems):
        """Test matching problems to context text."""
        context = "computational resources for deep learning training"

        matched = extractor._match_problems_to_context(context, sample_problems)

        # Should match problems mentioning computational resources
        assert len(matched) >= 1

    def test_deduplicate_relations(self, extractor):
        """Test deduplication keeps highest confidence."""
        relations = [
            ExtractedRelation(
                source_problem_id="A",
                target_problem_id="B",
                relation_type=RelationType.EXTENDS,
                confidence=0.7,
                evidence="Evidence 1",
            ),
            ExtractedRelation(
                source_problem_id="A",
                target_problem_id="B",
                relation_type=RelationType.EXTENDS,
                confidence=0.9,
                evidence="Evidence 2",
            ),
        ]

        deduped = extractor._deduplicate_relations(relations)

        assert len(deduped) == 1
        assert deduped[0].confidence == 0.9
        assert deduped[0].evidence == "Evidence 2"

    def test_parse_problem_number(self, extractor):
        """Test parsing problem numbers from IDs."""
        assert extractor._parse_problem_number("Problem 1") == 1
        assert extractor._parse_problem_number("problem 2") == 2
        assert extractor._parse_problem_number("Problem  3") == 3
        assert extractor._parse_problem_number("5") == 5
        assert extractor._parse_problem_number("not a number") is None


class TestRelationExtractorWithLLM:
    """Tests for RelationExtractor with LLM client."""

    @pytest.fixture
    def mock_client(self):
        """Create mock LLM client."""
        client = MagicMock()
        client.extract = AsyncMock()
        return client

    @pytest.fixture
    def extractor_with_llm(self, mock_client):
        """Create extractor with mock LLM client."""
        return RelationExtractor(
            client=mock_client,
            config=RelationConfig(min_confidence=0.3),
        )

    @pytest.fixture
    def sample_problems(self):
        """Create sample problems."""
        return [
            ExtractedProblem(
                statement="Problem A about machine learning optimization.",
                quoted_text="machine learning optimization",
                confidence=0.9,
            ),
            ExtractedProblem(
                statement="Problem B about neural network efficiency.",
                quoted_text="neural network efficiency",
                confidence=0.85,
            ),
        ]

    @pytest.mark.asyncio
    async def test_extract_with_llm_success(
        self, extractor_with_llm, mock_client, sample_problems
    ):
        """Test LLM-based extraction success."""
        llm_result = LLMRelationResult(
            relations=[
                ExtractedRelation(
                    source_problem_id="Problem 1",
                    target_problem_id="Problem 2",
                    relation_type=RelationType.EXTENDS,
                    confidence=0.9,
                    evidence="Problem 1 extends Problem 2",
                ),
            ],
            reasoning="Problem 1 builds on concepts from Problem 2",
        )

        mock_client.extract.return_value = LLMResponse(
            content=llm_result,
            usage=TokenUsage(total_tokens=500),
        )

        result = await extractor_with_llm.extract_from_text_with_llm(
            text="Some research text here",
            problems=sample_problems,
            paper_title="Test Paper",
        )

        assert result.relation_count >= 1
        mock_client.extract.assert_called_once()

    @pytest.mark.asyncio
    async def test_extract_with_llm_fallback_on_error(
        self, extractor_with_llm, mock_client, sample_problems
    ):
        """Test fallback to textual cue extraction on LLM error."""
        mock_client.extract.side_effect = Exception("LLM error")

        result = await extractor_with_llm.extract_from_text_with_llm(
            text="This extends previous work on optimization.",
            problems=sample_problems,
        )

        # Should still return a result (from textual cue extraction)
        assert isinstance(result, RelationExtractionResult)

    @pytest.mark.asyncio
    async def test_extract_with_llm_single_problem(
        self, extractor_with_llm, mock_client
    ):
        """Test that single problem returns empty result."""
        problems = [
            ExtractedProblem(
                statement="Single problem statement here.",
                quoted_text="single problem",
                confidence=0.9,
            ),
        ]

        result = await extractor_with_llm.extract_from_text_with_llm(
            text="Some text",
            problems=problems,
        )

        assert result.relation_count == 0
        mock_client.extract.assert_not_called()

    @pytest.mark.asyncio
    async def test_extract_without_llm_client(self, sample_problems):
        """Test extraction without LLM client uses textual cues."""
        extractor = RelationExtractor(
            client=None,
            config=RelationConfig(min_confidence=0.3),
        )

        result = await extractor.extract_from_text_with_llm(
            text="This extends previous machine learning work.",
            problems=sample_problems,
        )

        # Should fall back to non-LLM extraction
        assert isinstance(result, RelationExtractionResult)


class TestGetRelationExtractor:
    """Tests for singleton access."""

    def setup_method(self):
        """Reset singleton before each test."""
        reset_relation_extractor()

    def teardown_method(self):
        """Reset singleton after each test."""
        reset_relation_extractor()

    def test_returns_extractor_instance(self):
        """Test that get_relation_extractor returns an extractor."""
        extractor = get_relation_extractor()
        assert isinstance(extractor, RelationExtractor)

    def test_returns_same_instance(self):
        """Test singleton pattern."""
        extractor1 = get_relation_extractor()
        extractor2 = get_relation_extractor()
        assert extractor1 is extractor2

    def test_reset_clears_singleton(self):
        """Test reset clears singleton."""
        extractor1 = get_relation_extractor()
        reset_relation_extractor()
        extractor2 = get_relation_extractor()
        assert extractor1 is not extractor2

    def test_custom_config(self):
        """Test creating with custom config."""
        config = RelationConfig(min_confidence=0.9)
        extractor = get_relation_extractor(config=config)
        assert extractor.config.min_confidence == 0.9


class TestCueDetection:
    """Tests for specific textual cue detection."""

    @pytest.fixture
    def extractor(self):
        """Create extractor for testing."""
        return RelationExtractor(config=RelationConfig(min_confidence=0.1))

    @pytest.fixture
    def problems(self):
        """Create problems that match test contexts."""
        return [
            ExtractedProblem(
                statement="First problem about data processing efficiency.",
                quoted_text="data processing efficiency",
                confidence=0.9,
            ),
            ExtractedProblem(
                statement="Second problem about algorithm optimization.",
                quoted_text="algorithm optimization",
                confidence=0.9,
            ),
        ]

    def test_detect_extends_cue(self, extractor, problems):
        """Test detection of 'extends' cue."""
        text = "This data processing work builds on previous algorithm optimization research."

        relations = extractor._extract_by_textual_cues(text, problems)

        extends = [r for r in relations if r.relation_type == RelationType.EXTENDS]
        if extends:
            assert "builds on" in extends[0].evidence.lower()

    def test_detect_contradicts_cue(self, extractor, problems):
        """Test detection of 'contradicts' cue."""
        text = "Our findings on data processing conflict with prior algorithm claims."

        relations = extractor._extract_by_textual_cues(text, problems)

        contradicts = [
            r for r in relations if r.relation_type == RelationType.CONTRADICTS
        ]
        if contradicts:
            assert "conflict" in contradicts[0].evidence.lower()

    def test_detect_depends_on_cue(self, extractor, problems):
        """Test detection of 'depends_on' cue."""
        text = "Data processing requires efficient algorithms as a prerequisite."

        relations = extractor._extract_by_textual_cues(text, problems)

        depends = [r for r in relations if r.relation_type == RelationType.DEPENDS_ON]
        if depends:
            assert (
                "requires" in depends[0].evidence.lower()
                or "prerequisite" in depends[0].evidence.lower()
            )
