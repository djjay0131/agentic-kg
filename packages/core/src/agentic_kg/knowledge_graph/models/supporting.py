"""
Supporting models for the Knowledge Graph.

Defines smaller models used as fields within entity models (assumptions,
constraints, datasets, metrics, baselines, evidence, extraction metadata).
"""

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from .enums import ConstraintType


def _utc_now() -> datetime:
    """Get current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)


class Assumption(BaseModel):
    """An assumption underlying a research problem."""

    text: str = Field(..., min_length=1, description="The assumption statement")
    implicit: bool = Field(default=False, description="Whether explicitly stated or inferred")
    confidence: float = Field(ge=0, le=1, default=0.8, description="Confidence score")


class Constraint(BaseModel):
    """A constraint on a research problem."""

    text: str = Field(..., min_length=1, description="The constraint description")
    type: ConstraintType = Field(..., description="Type of constraint")
    confidence: float = Field(ge=0, le=1, default=0.8, description="Confidence score")


class Dataset(BaseModel):
    """A dataset associated with a research problem."""

    name: str = Field(..., min_length=1, description="Dataset name")
    url: Optional[str] = Field(default=None, description="Link to dataset")
    available: bool = Field(default=True, description="Whether publicly available")
    size: Optional[str] = Field(default=None, description="Dataset size description")


class Metric(BaseModel):
    """An evaluation metric for a research problem."""

    name: str = Field(..., min_length=1, description="Metric name (e.g., 'F1-score', 'BLEU')")
    description: Optional[str] = Field(default=None, description="Metric description")
    baseline_value: Optional[float] = Field(default=None, description="Current best/baseline")


class Baseline(BaseModel):
    """A baseline method for a research problem."""

    name: str = Field(..., min_length=1, description="Baseline method name")
    paper_doi: Optional[str] = Field(default=None, description="DOI of baseline paper")
    performance: dict = Field(default_factory=dict, description="Metric-value pairs")


class Evidence(BaseModel):
    """Evidence linking a problem to its source paper."""

    source_doi: str = Field(..., description="DOI of source paper")
    source_title: str = Field(..., min_length=1, description="Paper title")
    section: str = Field(..., description="Section where extracted")
    quoted_text: str = Field(..., min_length=1, description="Original text from paper")
    char_offset_start: Optional[int] = Field(default=None, ge=0)
    char_offset_end: Optional[int] = Field(default=None, ge=0)

    @field_validator("source_doi")
    @classmethod
    def validate_doi(cls, v: str) -> str:
        """Validate DOI format (basic check)."""
        if not v.startswith("10."):
            raise ValueError("DOI must start with '10.'")
        return v


class ExtractionMetadata(BaseModel):
    """Metadata about how a problem was extracted."""

    extracted_at: datetime = Field(default_factory=_utc_now)
    extractor_version: str = Field(default="1.0.0")
    extraction_model: str = Field(..., description="Model used (e.g., 'gpt-4', 'claude-3')")
    confidence_score: float = Field(ge=0, le=1, description="Extraction confidence")
    human_reviewed: bool = Field(default=False)
    reviewed_by: Optional[str] = Field(default=None)
    reviewed_at: Optional[datetime] = Field(default=None)
