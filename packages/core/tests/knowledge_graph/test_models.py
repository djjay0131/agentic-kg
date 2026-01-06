"""
Tests for agentic_kg.knowledge_graph.models module.

Tests Pydantic models for knowledge graph entities including validation,
serialization, and edge cases.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agentic_kg.knowledge_graph.models import (
    # Enums
    ConstraintType,
    ContradictionType,
    DependencyType,
    ProblemStatus,
    RelationType,
    # Supporting Models
    Assumption,
    Baseline,
    Constraint,
    Dataset,
    Evidence,
    ExtractionMetadata,
    Metric,
    # Core Entity Models
    Author,
    Paper,
    Problem,
    # Relation Models
    AuthoredByRelation,
    ContradictsRelation,
    DependsOnRelation,
    ExtractedFromRelation,
    ExtendsRelation,
    ProblemRelation,
    ReframesRelation,
)


# =============================================================================
# Enum Tests
# =============================================================================


class TestProblemStatus:
    """Tests for ProblemStatus enum."""

    def test_all_values_exist(self):
        """ProblemStatus has all expected values."""
        assert ProblemStatus.OPEN == "open"
        assert ProblemStatus.IN_PROGRESS == "in_progress"
        assert ProblemStatus.RESOLVED == "resolved"
        assert ProblemStatus.DEPRECATED == "deprecated"

    def test_string_value(self):
        """ProblemStatus values are strings."""
        assert ProblemStatus.OPEN.value == "open"
        assert isinstance(ProblemStatus.OPEN, str)


class TestConstraintType:
    """Tests for ConstraintType enum."""

    def test_all_values_exist(self):
        """ConstraintType has all expected values."""
        assert ConstraintType.COMPUTATIONAL == "computational"
        assert ConstraintType.DATA == "data"
        assert ConstraintType.METHODOLOGICAL == "methodological"
        assert ConstraintType.THEORETICAL == "theoretical"


class TestRelationType:
    """Tests for RelationType enum."""

    def test_all_values_exist(self):
        """RelationType has all expected values."""
        assert RelationType.EXTENDS == "EXTENDS"
        assert RelationType.CONTRADICTS == "CONTRADICTS"
        assert RelationType.DEPENDS_ON == "DEPENDS_ON"
        assert RelationType.REFRAMES == "REFRAMES"


class TestContradictionType:
    """Tests for ContradictionType enum."""

    def test_all_values_exist(self):
        """ContradictionType has all expected values."""
        assert ContradictionType.EMPIRICAL == "empirical"
        assert ContradictionType.THEORETICAL == "theoretical"
        assert ContradictionType.METHODOLOGICAL == "methodological"


class TestDependencyType:
    """Tests for DependencyType enum."""

    def test_all_values_exist(self):
        """DependencyType has all expected values."""
        assert DependencyType.PREREQUISITE == "prerequisite"
        assert DependencyType.DATA_DEPENDENCY == "data_dependency"
        assert DependencyType.METHODOLOGICAL == "methodological"


# =============================================================================
# Supporting Model Tests
# =============================================================================


class TestAssumption:
    """Tests for Assumption model."""

    # Happy path tests
    def test_create_with_required_fields(self):
        """Assumption can be created with required fields."""
        assumption = Assumption(text="Data is normally distributed")
        assert assumption.text == "Data is normally distributed"
        assert assumption.implicit is False
        assert assumption.confidence == 0.8

    def test_create_with_all_fields(self):
        """Assumption can be created with all fields."""
        assumption = Assumption(text="Implicit assumption", implicit=True, confidence=0.6)
        assert assumption.text == "Implicit assumption"
        assert assumption.implicit is True
        assert assumption.confidence == 0.6

    # Validation tests
    def test_text_cannot_be_empty(self):
        """Text field must have content."""
        with pytest.raises(ValidationError, match="String should have at least 1 character"):
            Assumption(text="")

    @pytest.mark.parametrize(
        "confidence",
        [-0.1, 1.1, 2.0, -1.0],
    )
    def test_confidence_must_be_in_range(self, confidence):
        """Confidence must be between 0 and 1."""
        with pytest.raises(ValidationError):
            Assumption(text="Valid text", confidence=confidence)

    @pytest.mark.parametrize(
        "confidence",
        [0.0, 0.5, 1.0, 0.001, 0.999],
    )
    def test_valid_confidence_values(self, confidence):
        """Valid confidence values are accepted."""
        assumption = Assumption(text="Valid text", confidence=confidence)
        assert assumption.confidence == confidence


class TestConstraint:
    """Tests for Constraint model."""

    # Happy path tests
    def test_create_with_required_fields(self):
        """Constraint can be created with required fields."""
        constraint = Constraint(text="Requires GPU", type=ConstraintType.COMPUTATIONAL)
        assert constraint.text == "Requires GPU"
        assert constraint.type == ConstraintType.COMPUTATIONAL
        assert constraint.confidence == 0.8

    def test_create_with_string_type(self):
        """Constraint accepts string type values."""
        constraint = Constraint(text="Requires GPU", type="computational")
        assert constraint.type == ConstraintType.COMPUTATIONAL

    # Validation tests
    def test_text_cannot_be_empty(self):
        """Text field must have content."""
        with pytest.raises(ValidationError):
            Constraint(text="", type=ConstraintType.COMPUTATIONAL)

    def test_invalid_type_rejected(self):
        """Invalid constraint type is rejected."""
        with pytest.raises(ValidationError):
            Constraint(text="Valid text", type="invalid_type")


class TestDataset:
    """Tests for Dataset model."""

    # Happy path tests
    def test_create_with_required_fields(self):
        """Dataset can be created with required fields."""
        dataset = Dataset(name="MNIST")
        assert dataset.name == "MNIST"
        assert dataset.url is None
        assert dataset.available is True
        assert dataset.size is None

    def test_create_with_all_fields(self, sample_dataset_data):
        """Dataset can be created with all fields."""
        dataset = Dataset(**sample_dataset_data)
        assert dataset.name == "ImageNet-1K"
        assert dataset.url == "https://image-net.org/"
        assert dataset.available is True
        assert dataset.size == "150GB"

    # Validation tests
    def test_name_cannot_be_empty(self):
        """Name field must have content."""
        with pytest.raises(ValidationError):
            Dataset(name="")


class TestMetric:
    """Tests for Metric model."""

    # Happy path tests
    def test_create_with_required_fields(self):
        """Metric can be created with required fields."""
        metric = Metric(name="Accuracy")
        assert metric.name == "Accuracy"
        assert metric.description is None
        assert metric.baseline_value is None

    def test_create_with_all_fields(self, sample_metric_data):
        """Metric can be created with all fields."""
        metric = Metric(**sample_metric_data)
        assert metric.name == "F1-score"
        assert metric.description == "Harmonic mean of precision and recall"
        assert metric.baseline_value == 0.85

    # Validation tests
    def test_name_cannot_be_empty(self):
        """Name field must have content."""
        with pytest.raises(ValidationError):
            Metric(name="")


class TestBaseline:
    """Tests for Baseline model."""

    # Happy path tests
    def test_create_with_required_fields(self):
        """Baseline can be created with required fields."""
        baseline = Baseline(name="Random baseline")
        assert baseline.name == "Random baseline"
        assert baseline.paper_doi is None
        assert baseline.performance == {}

    def test_create_with_all_fields(self, sample_baseline_data):
        """Baseline can be created with all fields."""
        baseline = Baseline(**sample_baseline_data)
        assert baseline.name == "BERT-base"
        assert baseline.paper_doi is not None
        assert baseline.performance == {"accuracy": 0.82, "f1": 0.79}

    # Validation tests
    def test_name_cannot_be_empty(self):
        """Name field must have content."""
        with pytest.raises(ValidationError):
            Baseline(name="")


class TestEvidence:
    """Tests for Evidence model."""

    # Happy path tests
    def test_create_with_required_fields(self, sample_doi):
        """Evidence can be created with required fields."""
        evidence = Evidence(
            source_doi=sample_doi,
            source_title="Research Paper",
            section="Introduction",
            quoted_text="This is the quoted text.",
        )
        assert evidence.source_doi == sample_doi
        assert evidence.source_title == "Research Paper"
        assert evidence.char_offset_start is None
        assert evidence.char_offset_end is None

    def test_create_with_all_fields(self, sample_evidence_data):
        """Evidence can be created with all fields."""
        evidence = Evidence(**sample_evidence_data)
        assert evidence.char_offset_start == 100
        assert evidence.char_offset_end == 150

    # DOI Validation tests
    def test_doi_must_start_with_10(self):
        """DOI must start with '10.'."""
        with pytest.raises(ValidationError, match="DOI must start with '10.'"):
            Evidence(
                source_doi="invalid-doi",
                source_title="Paper",
                section="Intro",
                quoted_text="Text",
            )

    @pytest.mark.parametrize(
        "valid_doi",
        [
            "10.1234/test",
            "10.1000/xyz123",
            "10.1038/nature12373",
            "10.48550/arXiv.2401.12345",
        ],
    )
    def test_valid_dois_accepted(self, valid_doi):
        """Valid DOI formats are accepted."""
        evidence = Evidence(
            source_doi=valid_doi,
            source_title="Paper",
            section="Intro",
            quoted_text="Text",
        )
        assert evidence.source_doi == valid_doi

    @pytest.mark.parametrize(
        "invalid_doi",
        [
            "invalid",
            "doi:10.1234/test",
            "http://doi.org/10.1234",
            "1.1234/test",
            "",
        ],
    )
    def test_invalid_dois_rejected(self, invalid_doi):
        """Invalid DOI formats are rejected."""
        with pytest.raises(ValidationError):
            Evidence(
                source_doi=invalid_doi,
                source_title="Paper",
                section="Intro",
                quoted_text="Text",
            )

    # Other validation tests
    def test_source_title_cannot_be_empty(self, sample_doi):
        """Source title must have content."""
        with pytest.raises(ValidationError):
            Evidence(
                source_doi=sample_doi,
                source_title="",
                section="Intro",
                quoted_text="Text",
            )

    def test_quoted_text_cannot_be_empty(self, sample_doi):
        """Quoted text must have content."""
        with pytest.raises(ValidationError):
            Evidence(
                source_doi=sample_doi,
                source_title="Paper",
                section="Intro",
                quoted_text="",
            )

    def test_char_offset_must_be_non_negative(self, sample_doi):
        """Character offsets must be non-negative."""
        with pytest.raises(ValidationError):
            Evidence(
                source_doi=sample_doi,
                source_title="Paper",
                section="Intro",
                quoted_text="Text",
                char_offset_start=-1,
            )


class TestExtractionMetadata:
    """Tests for ExtractionMetadata model."""

    # Happy path tests
    def test_create_with_required_fields(self):
        """ExtractionMetadata can be created with required fields."""
        metadata = ExtractionMetadata(extraction_model="gpt-4", confidence_score=0.9)
        assert metadata.extraction_model == "gpt-4"
        assert metadata.confidence_score == 0.9
        assert metadata.extractor_version == "1.0.0"
        assert metadata.human_reviewed is False

    def test_create_with_all_fields(self, sample_datetime):
        """ExtractionMetadata can be created with all fields."""
        metadata = ExtractionMetadata(
            extracted_at=sample_datetime,
            extractor_version="2.0.0",
            extraction_model="claude-3",
            confidence_score=0.95,
            human_reviewed=True,
            reviewed_by="researcher@example.com",
            reviewed_at=sample_datetime,
        )
        assert metadata.extracted_at == sample_datetime
        assert metadata.extractor_version == "2.0.0"
        assert metadata.human_reviewed is True
        assert metadata.reviewed_by == "researcher@example.com"

    def test_extracted_at_defaults_to_now(self):
        """extracted_at defaults to current time."""
        before = datetime.now(timezone.utc)
        metadata = ExtractionMetadata(extraction_model="gpt-4", confidence_score=0.9)
        after = datetime.now(timezone.utc)
        assert before <= metadata.extracted_at <= after

    # Validation tests
    @pytest.mark.parametrize(
        "confidence",
        [-0.1, 1.1, 2.0],
    )
    def test_confidence_must_be_in_range(self, confidence):
        """Confidence score must be between 0 and 1."""
        with pytest.raises(ValidationError):
            ExtractionMetadata(extraction_model="gpt-4", confidence_score=confidence)


# =============================================================================
# Core Entity Model Tests
# =============================================================================


class TestProblem:
    """Tests for Problem model."""

    # Happy path tests
    def test_create_with_required_fields(self, sample_evidence_data, sample_extraction_metadata_data):
        """Problem can be created with required fields."""
        problem = Problem(
            statement="How can we improve machine learning model interpretability?",
            evidence=sample_evidence_data,
            extraction_metadata=sample_extraction_metadata_data,
        )
        assert "interpretability" in problem.statement
        assert problem.domain is None
        assert problem.status == ProblemStatus.OPEN
        assert problem.version == 1

    def test_create_with_all_fields(self, sample_problem_data, sample_assumption_data):
        """Problem can be created with all fields."""
        sample_problem_data["assumptions"] = [sample_assumption_data]
        problem = Problem(**sample_problem_data)
        assert problem.domain == "Natural Language Processing"
        assert len(problem.assumptions) == 1

    def test_id_auto_generated(self, sample_problem_data):
        """Problem ID is auto-generated if not provided."""
        problem = Problem(**sample_problem_data)
        assert problem.id is not None
        assert len(problem.id) == 36  # UUID format

    def test_custom_id_preserved(self, sample_problem_data):
        """Custom problem ID is preserved."""
        sample_problem_data["id"] = "custom-id-123"
        problem = Problem(**sample_problem_data)
        assert problem.id == "custom-id-123"

    def test_timestamps_auto_generated(self, sample_problem_data):
        """Timestamps are auto-generated."""
        before = datetime.now(timezone.utc)
        problem = Problem(**sample_problem_data)
        after = datetime.now(timezone.utc)
        assert before <= problem.created_at <= after
        assert before <= problem.updated_at <= after

    # Validation tests
    def test_statement_minimum_length(self, sample_evidence_data, sample_extraction_metadata_data):
        """Statement must have minimum length of 20 characters."""
        with pytest.raises(ValidationError, match="String should have at least 20 characters"):
            Problem(
                statement="Too short",
                evidence=sample_evidence_data,
                extraction_metadata=sample_extraction_metadata_data,
            )

    def test_statement_at_minimum_length(self, sample_evidence_data, sample_extraction_metadata_data):
        """Statement with exactly 20 characters is valid."""
        problem = Problem(
            statement="12345678901234567890",  # Exactly 20 chars
            evidence=sample_evidence_data,
            extraction_metadata=sample_extraction_metadata_data,
        )
        assert len(problem.statement) == 20

    def test_version_must_be_positive(self, sample_problem_data):
        """Version must be at least 1."""
        sample_problem_data["version"] = 0
        with pytest.raises(ValidationError):
            Problem(**sample_problem_data)

    def test_evidence_required(self, sample_extraction_metadata_data):
        """Evidence field is required."""
        with pytest.raises(ValidationError):
            Problem(
                statement="This is a valid problem statement.",
                extraction_metadata=sample_extraction_metadata_data,
            )

    def test_extraction_metadata_required(self, sample_evidence_data):
        """Extraction metadata field is required."""
        with pytest.raises(ValidationError):
            Problem(
                statement="This is a valid problem statement.",
                evidence=sample_evidence_data,
            )

    # Serialization tests
    def test_to_neo4j_properties(self, sample_problem_data, sample_assumption_data):
        """to_neo4j_properties returns correct dictionary."""
        sample_problem_data["assumptions"] = [sample_assumption_data]
        problem = Problem(**sample_problem_data)
        props = problem.to_neo4j_properties()

        assert props["statement"] == problem.statement
        assert props["domain"] == problem.domain
        assert "embedding" not in props  # Excluded
        assert isinstance(props["created_at"], str)  # ISO format
        assert isinstance(props["updated_at"], str)
        assert isinstance(props["assumptions"], list)
        assert isinstance(props["evidence"], dict)

    def test_to_neo4j_properties_converts_datetimes(self, sample_problem_data):
        """to_neo4j_properties converts datetimes to ISO strings."""
        problem = Problem(**sample_problem_data)
        props = problem.to_neo4j_properties()

        # Check datetime conversion
        assert isinstance(props["created_at"], str)
        assert "T" in props["created_at"]  # ISO format contains T
        assert isinstance(props["extraction_metadata"]["extracted_at"], str)

    def test_to_neo4j_properties_excludes_embedding(self, sample_problem_data):
        """to_neo4j_properties excludes embedding field."""
        sample_problem_data["embedding"] = [0.1] * 1536
        problem = Problem(**sample_problem_data)
        props = problem.to_neo4j_properties()
        assert "embedding" not in props

    def test_to_neo4j_properties_handles_reviewed_at(
        self, sample_evidence_data, sample_datetime
    ):
        """to_neo4j_properties handles optional reviewed_at datetime."""
        metadata = {
            "extraction_model": "gpt-4",
            "confidence_score": 0.9,
            "human_reviewed": True,
            "reviewed_at": sample_datetime,
        }
        problem = Problem(
            statement="This is a valid problem statement.",
            evidence=sample_evidence_data,
            extraction_metadata=metadata,
        )
        props = problem.to_neo4j_properties()
        assert isinstance(props["extraction_metadata"]["reviewed_at"], str)


class TestPaper:
    """Tests for Paper model."""

    # Happy path tests
    def test_create_with_required_fields(self, sample_doi):
        """Paper can be created with required fields."""
        paper = Paper(
            doi=sample_doi,
            title="A Research Paper on Machine Learning",
            year=2024,
        )
        assert paper.doi == sample_doi
        assert paper.title == "A Research Paper on Machine Learning"
        assert paper.year == 2024
        assert paper.authors == []

    def test_create_with_all_fields(self, sample_paper_data):
        """Paper can be created with all fields."""
        paper = Paper(**sample_paper_data)
        assert len(paper.authors) == 2
        assert paper.venue == "NeurIPS 2024"
        assert paper.arxiv_id == "2401.12345"

    # DOI Validation tests
    def test_doi_must_start_with_10(self):
        """DOI must start with '10.'."""
        with pytest.raises(ValidationError, match="DOI must start with '10.'"):
            Paper(doi="invalid-doi", title="Valid Title Here", year=2024)

    # Title validation tests
    def test_title_minimum_length(self, sample_doi):
        """Title must have minimum length of 10 characters."""
        with pytest.raises(ValidationError, match="String should have at least 10 characters"):
            Paper(doi=sample_doi, title="Short", year=2024)

    def test_title_at_minimum_length(self, sample_doi):
        """Title with exactly 10 characters is valid."""
        paper = Paper(doi=sample_doi, title="1234567890", year=2024)
        assert len(paper.title) == 10

    # Year validation tests
    @pytest.mark.parametrize(
        "year",
        [1899, 2101, 1000, 3000],
    )
    def test_year_out_of_range_rejected(self, sample_doi, year):
        """Year outside valid range is rejected."""
        with pytest.raises(ValidationError):
            Paper(doi=sample_doi, title="Valid Title Here", year=year)

    @pytest.mark.parametrize(
        "year",
        [1900, 2000, 2024, 2100],
    )
    def test_year_in_range_accepted(self, sample_doi, year):
        """Year within valid range is accepted."""
        paper = Paper(doi=sample_doi, title="Valid Title Here", year=year)
        assert paper.year == year

    # Serialization tests
    def test_to_neo4j_properties(self, sample_paper_data):
        """to_neo4j_properties returns correct dictionary."""
        paper = Paper(**sample_paper_data)
        props = paper.to_neo4j_properties()

        assert props["doi"] == paper.doi
        assert props["title"] == paper.title
        assert props["year"] == paper.year
        assert "full_text" not in props  # Excluded
        assert isinstance(props["ingested_at"], str)

    def test_to_neo4j_properties_excludes_full_text(self, sample_paper_data):
        """to_neo4j_properties excludes full_text field."""
        sample_paper_data["full_text"] = "Very long text content..."
        paper = Paper(**sample_paper_data)
        props = paper.to_neo4j_properties()
        assert "full_text" not in props


class TestAuthor:
    """Tests for Author model."""

    # Happy path tests
    def test_create_with_required_fields(self):
        """Author can be created with required fields."""
        author = Author(name="John Doe")
        assert author.name == "John Doe"
        assert author.affiliations == []
        assert author.orcid is None

    def test_create_with_all_fields(self, sample_author_data):
        """Author can be created with all fields."""
        author = Author(**sample_author_data)
        assert author.name == "John Doe"
        assert len(author.affiliations) == 2
        assert author.orcid is not None

    def test_id_auto_generated(self):
        """Author ID is auto-generated if not provided."""
        author = Author(name="John Doe")
        assert author.id is not None
        assert len(author.id) == 36  # UUID format

    # ORCID Validation tests
    def test_orcid_must_start_with_0000(self):
        """ORCID must start with '0000-'."""
        with pytest.raises(ValidationError, match="ORCID must start with '0000-'"):
            Author(name="John Doe", orcid="1234-0000-0000-0000")

    @pytest.mark.parametrize(
        "valid_orcid",
        [
            "0000-0001-2345-6789",
            "0000-0002-1825-0097",
            "0000-0003-0000-0000",
        ],
    )
    def test_valid_orcids_accepted(self, valid_orcid):
        """Valid ORCID formats are accepted."""
        author = Author(name="John Doe", orcid=valid_orcid)
        assert author.orcid == valid_orcid

    def test_none_orcid_accepted(self):
        """None ORCID is accepted."""
        author = Author(name="John Doe", orcid=None)
        assert author.orcid is None

    # Name validation tests
    def test_name_cannot_be_empty(self):
        """Name must have content."""
        with pytest.raises(ValidationError):
            Author(name="")

    # Serialization tests
    def test_to_neo4j_properties(self, sample_author_data):
        """to_neo4j_properties returns correct dictionary."""
        author = Author(**sample_author_data)
        props = author.to_neo4j_properties()

        assert props["name"] == author.name
        assert props["affiliations"] == author.affiliations
        assert props["orcid"] == author.orcid
        assert props["id"] == author.id


# =============================================================================
# Relation Model Tests
# =============================================================================


class TestProblemRelation:
    """Tests for ProblemRelation model."""

    def test_create_with_required_fields(self):
        """ProblemRelation can be created with required fields."""
        relation = ProblemRelation(
            from_problem_id="problem-1",
            to_problem_id="problem-2",
            relation_type=RelationType.EXTENDS,
        )
        assert relation.from_problem_id == "problem-1"
        assert relation.to_problem_id == "problem-2"
        assert relation.confidence == 0.8
        assert relation.evidence_doi is None

    def test_confidence_validation(self):
        """Confidence must be between 0 and 1."""
        with pytest.raises(ValidationError):
            ProblemRelation(
                from_problem_id="p1",
                to_problem_id="p2",
                relation_type=RelationType.EXTENDS,
                confidence=1.5,
            )

    def test_created_at_defaults_to_now(self):
        """created_at defaults to current time."""
        before = datetime.now(timezone.utc)
        relation = ProblemRelation(
            from_problem_id="p1",
            to_problem_id="p2",
            relation_type=RelationType.EXTENDS,
        )
        after = datetime.now(timezone.utc)
        assert before <= relation.created_at <= after


class TestExtendsRelation:
    """Tests for ExtendsRelation model."""

    def test_create_with_defaults(self):
        """ExtendsRelation has correct default relation type."""
        relation = ExtendsRelation(
            from_problem_id="p1",
            to_problem_id="p2",
        )
        assert relation.relation_type == RelationType.EXTENDS
        assert relation.inferred_by is None

    def test_with_inferred_by(self):
        """ExtendsRelation accepts inferred_by field."""
        relation = ExtendsRelation(
            from_problem_id="p1",
            to_problem_id="p2",
            inferred_by="gpt-4",
        )
        assert relation.inferred_by == "gpt-4"


class TestContradictsRelation:
    """Tests for ContradictsRelation model."""

    def test_create_with_required_fields(self):
        """ContradictsRelation requires contradiction_type."""
        relation = ContradictsRelation(
            from_problem_id="p1",
            to_problem_id="p2",
            contradiction_type=ContradictionType.EMPIRICAL,
        )
        assert relation.relation_type == RelationType.CONTRADICTS
        assert relation.contradiction_type == ContradictionType.EMPIRICAL

    def test_contradiction_type_required(self):
        """contradiction_type is required."""
        with pytest.raises(ValidationError):
            ContradictsRelation(
                from_problem_id="p1",
                to_problem_id="p2",
            )


class TestDependsOnRelation:
    """Tests for DependsOnRelation model."""

    def test_create_with_required_fields(self):
        """DependsOnRelation requires dependency_type."""
        relation = DependsOnRelation(
            from_problem_id="p1",
            to_problem_id="p2",
            dependency_type=DependencyType.PREREQUISITE,
        )
        assert relation.relation_type == RelationType.DEPENDS_ON
        assert relation.dependency_type == DependencyType.PREREQUISITE

    def test_dependency_type_required(self):
        """dependency_type is required."""
        with pytest.raises(ValidationError):
            DependsOnRelation(
                from_problem_id="p1",
                to_problem_id="p2",
            )


class TestReframesRelation:
    """Tests for ReframesRelation model."""

    def test_create_with_defaults(self):
        """ReframesRelation has correct default relation type."""
        relation = ReframesRelation(
            from_problem_id="p1",
            to_problem_id="p2",
        )
        assert relation.relation_type == RelationType.REFRAMES


class TestExtractedFromRelation:
    """Tests for ExtractedFromRelation model."""

    def test_create_with_required_fields(self, sample_doi):
        """ExtractedFromRelation can be created with required fields."""
        relation = ExtractedFromRelation(
            problem_id="problem-1",
            paper_doi=sample_doi,
            section="Introduction",
        )
        assert relation.problem_id == "problem-1"
        assert relation.paper_doi == sample_doi
        assert relation.section == "Introduction"

    def test_extraction_date_defaults_to_now(self, sample_doi):
        """extraction_date defaults to current time."""
        before = datetime.now(timezone.utc)
        relation = ExtractedFromRelation(
            problem_id="p1",
            paper_doi=sample_doi,
            section="Methods",
        )
        after = datetime.now(timezone.utc)
        assert before <= relation.extraction_date <= after


class TestAuthoredByRelation:
    """Tests for AuthoredByRelation model."""

    def test_create_with_required_fields(self, sample_doi):
        """AuthoredByRelation can be created with required fields."""
        relation = AuthoredByRelation(
            paper_doi=sample_doi,
            author_id="author-1",
            author_position=1,
        )
        assert relation.paper_doi == sample_doi
        assert relation.author_id == "author-1"
        assert relation.author_position == 1

    def test_author_position_must_be_positive(self, sample_doi):
        """Author position must be at least 1."""
        with pytest.raises(ValidationError):
            AuthoredByRelation(
                paper_doi=sample_doi,
                author_id="author-1",
                author_position=0,
            )

    @pytest.mark.parametrize("position", [1, 2, 10, 100])
    def test_valid_author_positions(self, sample_doi, position):
        """Valid author positions are accepted."""
        relation = AuthoredByRelation(
            paper_doi=sample_doi,
            author_id="author-1",
            author_position=position,
        )
        assert relation.author_position == position


# =============================================================================
# Edge Case Tests
# =============================================================================


class TestModelEdgeCases:
    """Edge case tests for all models."""

    def test_assumption_with_unicode(self):
        """Assumption handles unicode text."""
        assumption = Assumption(text="Unicode text")
        assert "Unicode" in assumption.text

    def test_problem_with_very_long_statement(self, sample_evidence_data, sample_extraction_metadata_data):
        """Problem handles very long statements."""
        long_statement = "A" * 10000
        problem = Problem(
            statement=long_statement,
            evidence=sample_evidence_data,
            extraction_metadata=sample_extraction_metadata_data,
        )
        assert len(problem.statement) == 10000

    def test_paper_with_many_authors(self, sample_doi):
        """Paper handles many authors."""
        authors = [f"Author {i}" for i in range(100)]
        paper = Paper(
            doi=sample_doi,
            title="Paper with Many Authors",
            year=2024,
            authors=authors,
        )
        assert len(paper.authors) == 100

    def test_problem_with_empty_lists(self, sample_evidence_data, sample_extraction_metadata_data):
        """Problem handles empty lists for optional fields."""
        problem = Problem(
            statement="This is a valid problem statement.",
            evidence=sample_evidence_data,
            extraction_metadata=sample_extraction_metadata_data,
            assumptions=[],
            constraints=[],
            datasets=[],
            metrics=[],
            baselines=[],
        )
        assert problem.assumptions == []
        assert problem.constraints == []

    def test_model_dump_json_serializable(self, sample_problem_data):
        """model_dump output is JSON serializable."""
        import json

        problem = Problem(**sample_problem_data)
        data = problem.model_dump(mode="json")
        json_str = json.dumps(data)
        assert json_str is not None

    def test_evidence_with_section_special_chars(self, sample_doi):
        """Evidence handles special characters in section."""
        evidence = Evidence(
            source_doi=sample_doi,
            source_title="Paper Title",
            section="Section 3.1: Results & Discussion",
            quoted_text="Quoted text here.",
        )
        assert "&" in evidence.section

    def test_nested_model_validation(self, sample_evidence_data, sample_extraction_metadata_data):
        """Problem validates nested models."""
        # Invalid assumption within problem
        with pytest.raises(ValidationError):
            Problem(
                statement="This is a valid problem statement.",
                evidence=sample_evidence_data,
                extraction_metadata=sample_extraction_metadata_data,
                assumptions=[{"text": "", "confidence": 0.5}],  # Empty text
            )
