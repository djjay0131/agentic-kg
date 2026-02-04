"""
Extraction Schema Models.

Pydantic models for LLM extraction output. These models define the
expected structure for research problem extraction and can be directly
used with the instructor library for structured LLM output.
"""

from datetime import datetime, timezone
from typing import Optional
import uuid

from pydantic import BaseModel, Field, field_validator

from agentic_kg.knowledge_graph.models import (
    Assumption,
    Baseline,
    Constraint,
    ConstraintType,
    Dataset,
    Evidence,
    ExtractionMetadata,
    Metric,
    Problem,
    ProblemStatus,
)


def _utc_now() -> datetime:
    """Get current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)


class ExtractedAssumption(BaseModel):
    """An assumption extracted from paper text."""

    text: str = Field(..., min_length=5, description="The assumption statement")
    implicit: bool = Field(
        default=False, description="True if inferred, False if explicitly stated"
    )
    confidence: float = Field(
        ge=0, le=1, default=0.8, description="Confidence that this is a valid assumption"
    )


class ExtractedConstraint(BaseModel):
    """A constraint extracted from paper text."""

    text: str = Field(..., min_length=5, description="The constraint description")
    constraint_type: str = Field(
        ...,
        description="Type: computational, data, methodological, or theoretical",
    )
    confidence: float = Field(ge=0, le=1, default=0.8)

    @field_validator("constraint_type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        """Validate and normalize constraint type."""
        normalized = v.lower().strip()
        valid_types = ["computational", "data", "methodological", "theoretical"]
        if normalized not in valid_types:
            # Try to map common variations
            if "compute" in normalized or "gpu" in normalized or "memory" in normalized:
                return "computational"
            elif "dataset" in normalized or "annotation" in normalized:
                return "data"
            elif "method" in normalized or "algorithm" in normalized:
                return "methodological"
            elif "theory" in normalized:
                return "theoretical"
            # Default to methodological if unclear
            return "methodological"
        return normalized


class ExtractedDataset(BaseModel):
    """A dataset reference extracted from paper text."""

    name: str = Field(..., min_length=1, description="Dataset name")
    url: Optional[str] = Field(default=None, description="URL if mentioned")
    available: bool = Field(
        default=True, description="Whether dataset is publicly available"
    )
    description: Optional[str] = Field(
        default=None, description="Brief description of the dataset"
    )


class ExtractedMetric(BaseModel):
    """An evaluation metric extracted from paper text."""

    name: str = Field(..., min_length=1, description="Metric name (e.g., F1, BLEU)")
    description: Optional[str] = Field(default=None, description="What the metric measures")
    baseline_value: Optional[float] = Field(
        default=None, description="Current best/baseline value if mentioned"
    )


class ExtractedBaseline(BaseModel):
    """A baseline method extracted from paper text."""

    name: str = Field(..., min_length=1, description="Baseline method name")
    paper_reference: Optional[str] = Field(
        default=None, description="Reference to the paper introducing this baseline"
    )
    performance_notes: Optional[str] = Field(
        default=None, description="Notes on performance"
    )


class ExtractedProblem(BaseModel):
    """
    A research problem extracted from paper text.

    This schema is designed for use with the instructor library to
    get structured output from LLMs.
    """

    statement: str = Field(
        ...,
        min_length=20,
        max_length=1000,
        description="Clear, concise statement of the research problem",
    )
    domain: Optional[str] = Field(
        default=None,
        description="Research domain (e.g., NLP, Computer Vision)",
    )
    assumptions: list[ExtractedAssumption] = Field(
        default_factory=list,
        description="Assumptions underlying this problem",
    )
    constraints: list[ExtractedConstraint] = Field(
        default_factory=list,
        description="Constraints affecting this problem",
    )
    datasets: list[ExtractedDataset] = Field(
        default_factory=list,
        description="Datasets relevant to this problem",
    )
    metrics: list[ExtractedMetric] = Field(
        default_factory=list,
        description="Metrics for evaluating solutions",
    )
    baselines: list[ExtractedBaseline] = Field(
        default_factory=list,
        description="Baseline methods mentioned",
    )
    quoted_text: str = Field(
        ...,
        min_length=10,
        description="Exact text from the paper supporting this problem identification",
    )
    confidence: float = Field(
        ge=0,
        le=1,
        default=0.8,
        description="Confidence in the extraction (0.0-1.0)",
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="Brief explanation of why this is identified as a research problem",
    )


class ExtractionResult(BaseModel):
    """Result of extracting problems from a section."""

    problems: list[ExtractedProblem] = Field(
        default_factory=list,
        description="List of extracted research problems",
    )
    section_type: str = Field(..., description="Type of section extracted from")
    extraction_notes: Optional[str] = Field(
        default=None,
        description="Notes about the extraction process",
    )

    @property
    def problem_count(self) -> int:
        """Number of problems extracted."""
        return len(self.problems)

    @property
    def high_confidence_problems(self) -> list[ExtractedProblem]:
        """Get problems with confidence >= 0.8."""
        return [p for p in self.problems if p.confidence >= 0.8]


def extracted_to_kg_problem(
    extracted: ExtractedProblem,
    paper_doi: Optional[str],
    paper_title: Optional[str],
    section: str,
    extraction_model: str = "gpt-4-turbo",
    extractor_version: str = "1.0.0",
) -> Problem:
    """
    Convert an ExtractedProblem to a Knowledge Graph Problem entity.

    Args:
        extracted: The extracted problem from LLM.
        paper_doi: DOI of the source paper (optional).
        paper_title: Title of the source paper (optional).
        section: Section where the problem was extracted from.
        extraction_model: LLM model used for extraction.
        extractor_version: Version of the extraction pipeline.

    Returns:
        Problem entity ready for storage in the Knowledge Graph.
    """
    # Convert assumptions
    assumptions = [
        Assumption(
            text=a.text,
            implicit=a.implicit,
            confidence=a.confidence,
        )
        for a in extracted.assumptions
    ]

    # Convert constraints
    constraints = []
    for c in extracted.constraints:
        try:
            constraint_type = ConstraintType(c.constraint_type)
        except ValueError:
            constraint_type = ConstraintType.METHODOLOGICAL
        constraints.append(
            Constraint(
                text=c.text,
                type=constraint_type,
                confidence=c.confidence,
            )
        )

    # Convert datasets
    datasets = [
        Dataset(
            name=d.name,
            url=d.url,
            available=d.available,
            size=d.description,  # Using size field for description
        )
        for d in extracted.datasets
    ]

    # Convert metrics
    metrics = [
        Metric(
            name=m.name,
            description=m.description,
            baseline_value=m.baseline_value,
        )
        for m in extracted.metrics
    ]

    # Convert baselines
    baselines = [
        Baseline(
            name=b.name,
            paper_doi=None,  # Would need DOI resolution
            performance={},  # Could parse from notes
        )
        for b in extracted.baselines
    ]

    # Create evidence only if we have valid paper info
    evidence = None
    if paper_doi and paper_title:
        evidence = Evidence(
            source_doi=paper_doi,
            source_title=paper_title,
            section=section,
            quoted_text=extracted.quoted_text,
        )

    # Create extraction metadata
    extraction_metadata = ExtractionMetadata(
        extracted_at=_utc_now(),
        extractor_version=extractor_version,
        extraction_model=extraction_model,
        confidence_score=extracted.confidence,
        human_reviewed=False,
    )

    return Problem(
        id=str(uuid.uuid4()),
        statement=extracted.statement,
        domain=extracted.domain,
        status=ProblemStatus.OPEN,
        assumptions=assumptions,
        constraints=constraints,
        datasets=datasets,
        metrics=metrics,
        baselines=baselines,
        evidence=evidence,
        extraction_metadata=extraction_metadata,
    )


class BatchExtractionResult(BaseModel):
    """Result of extracting problems from multiple sections."""

    results: list[ExtractionResult] = Field(default_factory=list)
    paper_title: str = ""
    paper_doi: Optional[str] = None
    total_problems: int = 0
    total_high_confidence: int = 0
    total_tokens: int = 0

    def model_post_init(self, __context) -> None:
        """Calculate totals if not provided."""
        if self.total_problems == 0:
            object.__setattr__(
                self, 'total_problems', sum(r.problem_count for r in self.results)
            )
        if self.total_high_confidence == 0:
            object.__setattr__(
                self, 'total_high_confidence',
                sum(len(r.high_confidence_problems) for r in self.results)
            )

    def get_all_problems(self) -> list[ExtractedProblem]:
        """Get all problems from all sections."""
        problems = []
        for result in self.results:
            problems.extend(result.problems)
        return problems

    def get_all_high_confidence(self) -> list[ExtractedProblem]:
        """Get all high-confidence problems."""
        problems = []
        for result in self.results:
            problems.extend(result.high_confidence_problems)
        return problems
