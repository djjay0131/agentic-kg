"""
Unit tests for extraction schema models.
"""

import pytest

from agentic_kg.extraction.schemas import (
    BatchExtractionResult,
    ExtractedAssumption,
    ExtractedBaseline,
    ExtractedConstraint,
    ExtractedDataset,
    ExtractedMetric,
    ExtractedProblem,
    ExtractionResult,
    extracted_to_kg_problem,
)
from agentic_kg.knowledge_graph.models import ConstraintType, ProblemStatus


class TestExtractedAssumption:
    """Tests for ExtractedAssumption model."""

    def test_create_assumption(self):
        """Test creating an assumption."""
        assumption = ExtractedAssumption(
            text="The data is independent and identically distributed.",
            implicit=False,
            confidence=0.9,
        )

        assert "independent" in assumption.text
        assert assumption.implicit is False
        assert assumption.confidence == 0.9

    def test_default_values(self):
        """Test default values."""
        assumption = ExtractedAssumption(text="Test assumption text")

        assert assumption.implicit is False
        assert assumption.confidence == 0.8

    def test_text_min_length(self):
        """Test minimum text length validation."""
        with pytest.raises(ValueError):
            ExtractedAssumption(text="Hi")  # Too short


class TestExtractedConstraint:
    """Tests for ExtractedConstraint model."""

    def test_create_constraint(self):
        """Test creating a constraint."""
        constraint = ExtractedConstraint(
            text="Requires at least 8 GB of GPU memory",
            constraint_type="computational",
            confidence=0.95,
        )

        assert "GPU" in constraint.text
        assert constraint.constraint_type == "computational"

    def test_type_normalization(self):
        """Test that constraint type is normalized."""
        constraint = ExtractedConstraint(
            text="Limited by available data",
            constraint_type="COMPUTATIONAL",  # Should be lowercased
        )

        assert constraint.constraint_type == "computational"

    def test_type_inference_from_keywords(self):
        """Test constraint type inference from keywords."""
        # GPU should map to computational
        constraint = ExtractedConstraint(
            text="Needs GPU", constraint_type="gpu-related"
        )
        assert constraint.constraint_type == "computational"

        # Dataset should map to data
        constraint = ExtractedConstraint(
            text="Need more data", constraint_type="dataset issues"
        )
        assert constraint.constraint_type == "data"


class TestExtractedDataset:
    """Tests for ExtractedDataset model."""

    def test_create_dataset(self):
        """Test creating a dataset reference."""
        dataset = ExtractedDataset(
            name="ImageNet",
            url="https://image-net.org",
            available=True,
            description="Large-scale visual recognition dataset",
        )

        assert dataset.name == "ImageNet"
        assert dataset.available is True

    def test_default_values(self):
        """Test default values."""
        dataset = ExtractedDataset(name="MNIST")

        assert dataset.url is None
        assert dataset.available is True


class TestExtractedMetric:
    """Tests for ExtractedMetric model."""

    def test_create_metric(self):
        """Test creating a metric."""
        metric = ExtractedMetric(
            name="F1-score",
            description="Harmonic mean of precision and recall",
            baseline_value=0.85,
        )

        assert metric.name == "F1-score"
        assert metric.baseline_value == 0.85


class TestExtractedBaseline:
    """Tests for ExtractedBaseline model."""

    def test_create_baseline(self):
        """Test creating a baseline reference."""
        baseline = ExtractedBaseline(
            name="BERT-base",
            paper_reference="Devlin et al., 2019",
            performance_notes="Achieves 85% accuracy on the benchmark",
        )

        assert baseline.name == "BERT-base"
        assert "Devlin" in baseline.paper_reference


class TestExtractedProblem:
    """Tests for ExtractedProblem model."""

    def test_create_minimal_problem(self):
        """Test creating a problem with minimal fields."""
        problem = ExtractedProblem(
            statement="Current models struggle with long-range dependencies in text sequences.",
            quoted_text="the model struggles with sequences longer than 512 tokens",
        )

        assert "long-range" in problem.statement
        assert problem.confidence == 0.8
        assert problem.domain is None

    def test_create_full_problem(self):
        """Test creating a problem with all fields."""
        problem = ExtractedProblem(
            statement="Neural machine translation quality degrades for low-resource language pairs.",
            domain="Machine Translation",
            assumptions=[
                ExtractedAssumption(
                    text="Parallel training data is required", implicit=False
                )
            ],
            constraints=[
                ExtractedConstraint(
                    text="Limited parallel corpora available",
                    constraint_type="data",
                )
            ],
            datasets=[ExtractedDataset(name="WMT20", available=True)],
            metrics=[ExtractedMetric(name="BLEU", baseline_value=25.0)],
            baselines=[ExtractedBaseline(name="Transformer-base")],
            quoted_text="performance drops significantly for low-resource language pairs",
            confidence=0.92,
            reasoning="Authors explicitly identify this as an open problem",
        )

        assert problem.domain == "Machine Translation"
        assert len(problem.assumptions) == 1
        assert len(problem.constraints) == 1
        assert problem.confidence == 0.92

    def test_statement_length_validation(self):
        """Test statement length validation."""
        # Too short
        with pytest.raises(ValueError):
            ExtractedProblem(
                statement="Too short.",
                quoted_text="Some quoted text from the paper",
            )


class TestExtractionResult:
    """Tests for ExtractionResult model."""

    @pytest.fixture
    def sample_problems(self):
        """Create sample problems."""
        return [
            ExtractedProblem(
                statement="First problem with high confidence score here.",
                quoted_text="quoted text 1",
                confidence=0.9,
            ),
            ExtractedProblem(
                statement="Second problem with low confidence score.",
                quoted_text="quoted text 2",
                confidence=0.6,
            ),
            ExtractedProblem(
                statement="Third problem with medium confidence level.",
                quoted_text="quoted text 3",
                confidence=0.85,
            ),
        ]

    def test_create_result(self, sample_problems):
        """Test creating an extraction result."""
        result = ExtractionResult(
            problems=sample_problems,
            section_type="limitations",
        )

        assert result.problem_count == 3
        assert result.section_type == "limitations"

    def test_high_confidence_filter(self, sample_problems):
        """Test filtering high confidence problems."""
        result = ExtractionResult(
            problems=sample_problems,
            section_type="limitations",
        )

        high_conf = result.high_confidence_problems
        assert len(high_conf) == 2  # Only 0.9 and 0.85

    def test_empty_result(self):
        """Test empty extraction result."""
        result = ExtractionResult(problems=[], section_type="abstract")

        assert result.problem_count == 0
        assert len(result.high_confidence_problems) == 0


class TestBatchExtractionResult:
    """Tests for BatchExtractionResult model."""

    def test_get_all_problems(self):
        """Test getting all problems from batch."""
        results = [
            ExtractionResult(
                section_type="limitations",
                problems=[
                    ExtractedProblem(
                        statement="Problem from limitations section here.",
                        quoted_text="quote text for limitations problem",
                    )
                ],
            ),
            ExtractionResult(
                section_type="future_work",
                problems=[
                    ExtractedProblem(
                        statement="Problem from future work section.",
                        quoted_text="quote text for future work",
                    ),
                    ExtractedProblem(
                        statement="Another future work problem here.",
                        quoted_text="another quote text here",
                    ),
                ],
            ),
        ]

        batch = BatchExtractionResult(
            results=results,
            paper_title="Test Paper",
        )

        all_problems = batch.get_all_problems()
        assert len(all_problems) == 3

    def test_get_high_confidence(self):
        """Test getting high confidence problems from batch."""
        results = [
            ExtractionResult(
                section_type="limitations",
                problems=[
                    ExtractedProblem(
                        statement="High confidence problem in limitations.",
                        quoted_text="quote text for high confidence",
                        confidence=0.95,
                    ),
                    ExtractedProblem(
                        statement="Low confidence problem in limitations.",
                        quoted_text="quote text for low confidence",
                        confidence=0.5,
                    ),
                ],
            ),
        ]

        batch = BatchExtractionResult(results=results, paper_title="Test")

        high_conf = batch.get_all_high_confidence()
        assert len(high_conf) == 1


class TestExtractedToKGProblem:
    """Tests for conversion from extracted to KG problem."""

    @pytest.fixture
    def extracted_problem(self):
        """Create a sample extracted problem."""
        return ExtractedProblem(
            statement="Current transformer models have quadratic memory complexity with respect to sequence length.",
            domain="Natural Language Processing",
            assumptions=[
                ExtractedAssumption(
                    text="Full attention mechanism is needed",
                    implicit=True,
                    confidence=0.7,
                )
            ],
            constraints=[
                ExtractedConstraint(
                    text="Memory grows O(n^2) with sequence length",
                    constraint_type="computational",
                    confidence=0.95,
                )
            ],
            datasets=[
                ExtractedDataset(
                    name="Long Range Arena",
                    available=True,
                )
            ],
            metrics=[
                ExtractedMetric(
                    name="Accuracy",
                    description="Classification accuracy",
                    baseline_value=0.75,
                )
            ],
            baselines=[
                ExtractedBaseline(
                    name="Longformer",
                    performance_notes="Achieves linear complexity",
                )
            ],
            quoted_text="the quadratic memory complexity of standard transformers limits their application to long sequences",
            confidence=0.9,
        )

    def test_conversion_preserves_statement(self, extracted_problem):
        """Test that statement is preserved."""
        problem = extracted_to_kg_problem(
            extracted_problem,
            paper_doi="10.1234/test",
            paper_title="Test Paper",
            section="limitations",
        )

        assert "quadratic memory complexity" in problem.statement
        assert problem.domain == "Natural Language Processing"

    def test_conversion_creates_evidence(self, extracted_problem):
        """Test that evidence is created correctly."""
        problem = extracted_to_kg_problem(
            extracted_problem,
            paper_doi="10.1234/test",
            paper_title="Test Paper",
            section="limitations",
        )

        assert problem.evidence.source_doi == "10.1234/test"
        assert problem.evidence.source_title == "Test Paper"
        assert problem.evidence.section == "limitations"
        assert "quadratic memory" in problem.evidence.quoted_text

    def test_conversion_creates_metadata(self, extracted_problem):
        """Test that extraction metadata is created."""
        problem = extracted_to_kg_problem(
            extracted_problem,
            paper_doi="10.1234/test",
            paper_title="Test Paper",
            section="limitations",
            extraction_model="gpt-4-turbo",
            extractor_version="1.0.0",
        )

        assert problem.extraction_metadata.extraction_model == "gpt-4-turbo"
        assert problem.extraction_metadata.confidence_score == 0.9
        assert problem.extraction_metadata.human_reviewed is False

    def test_conversion_maps_assumptions(self, extracted_problem):
        """Test that assumptions are converted."""
        problem = extracted_to_kg_problem(
            extracted_problem,
            paper_doi="10.1234/test",
            paper_title="Test Paper",
            section="limitations",
        )

        assert len(problem.assumptions) == 1
        assert "attention" in problem.assumptions[0].text
        assert problem.assumptions[0].implicit is True

    def test_conversion_maps_constraints(self, extracted_problem):
        """Test that constraints are converted."""
        problem = extracted_to_kg_problem(
            extracted_problem,
            paper_doi="10.1234/test",
            paper_title="Test Paper",
            section="limitations",
        )

        assert len(problem.constraints) == 1
        assert problem.constraints[0].type == ConstraintType.COMPUTATIONAL

    def test_conversion_maps_datasets(self, extracted_problem):
        """Test that datasets are converted."""
        problem = extracted_to_kg_problem(
            extracted_problem,
            paper_doi="10.1234/test",
            paper_title="Test Paper",
            section="limitations",
        )

        assert len(problem.datasets) == 1
        assert problem.datasets[0].name == "Long Range Arena"

    def test_conversion_sets_status_open(self, extracted_problem):
        """Test that status is set to OPEN."""
        problem = extracted_to_kg_problem(
            extracted_problem,
            paper_doi="10.1234/test",
            paper_title="Test Paper",
            section="limitations",
        )

        assert problem.status == ProblemStatus.OPEN

    def test_conversion_generates_uuid(self, extracted_problem):
        """Test that a unique ID is generated."""
        problem1 = extracted_to_kg_problem(
            extracted_problem,
            paper_doi="10.1234/test",
            paper_title="Test Paper",
            section="limitations",
        )
        problem2 = extracted_to_kg_problem(
            extracted_problem,
            paper_doi="10.1234/test",
            paper_title="Test Paper",
            section="limitations",
        )

        assert problem1.id != problem2.id
