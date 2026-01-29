"""
Agent output schemas.

Pydantic models for structured agent outputs. These models define the
expected structure for each agent's output and can be used with the
instructor library for structured LLM responses.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def _utc_now() -> datetime:
    """Get current UTC time."""
    return datetime.now(timezone.utc)


# =============================================================================
# Enums
# =============================================================================


class CheckpointType(str, Enum):
    """Types of human-in-the-loop checkpoints."""

    SELECT_PROBLEM = "select_problem"
    APPROVE_PROPOSAL = "approve_proposal"
    REVIEW_EVALUATION = "review_evaluation"


class CheckpointDecision(str, Enum):
    """Human decisions at checkpoints."""

    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"


class WorkflowStatus(str, Enum):
    """Status of a research workflow."""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_CHECKPOINT = "waiting_checkpoint"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# =============================================================================
# Ranking Agent Schemas
# =============================================================================


class RankedProblem(BaseModel):
    """A problem scored by the Ranking Agent."""

    problem_id: str = Field(..., description="ID of the problem in the KG")
    statement: str = Field(..., description="Problem statement for display")
    score: float = Field(ge=0, le=1, description="Overall ranking score (0-1)")
    tractability: float = Field(
        ge=0, le=1, description="How feasible is this to work on (0-1)"
    )
    data_availability: float = Field(
        ge=0, le=1, description="Are datasets and baselines available (0-1)"
    )
    cross_domain_impact: float = Field(
        ge=0, le=1, description="Potential impact across research domains (0-1)"
    )
    rationale: str = Field(
        ..., min_length=10, description="Explanation of the ranking"
    )
    domain: Optional[str] = None
    related_problem_count: int = Field(
        default=0, description="Number of related problems in the graph"
    )


class RankingResult(BaseModel):
    """Output of the Ranking Agent."""

    ranked_problems: list[RankedProblem] = Field(
        default_factory=list, description="Problems sorted by score descending"
    )
    query_summary: str = Field(
        default="", description="Summary of the query/filters used"
    )
    total_candidates: int = Field(
        default=0, description="Total problems considered before filtering"
    )


# =============================================================================
# Continuation Agent Schemas
# =============================================================================


class ExperimentalStep(BaseModel):
    """A concrete step in a research continuation proposal."""

    step_number: int = Field(ge=1, description="Order of the step")
    description: str = Field(
        ..., min_length=10, description="What to do in this step"
    )
    expected_output: str = Field(
        ..., description="What this step should produce"
    )
    tools_or_methods: list[str] = Field(
        default_factory=list, description="Tools or methods needed"
    )


class ContinuationProposal(BaseModel):
    """Output of the Continuation Agent."""

    problem_id: str = Field(..., description="ID of the source problem")
    title: str = Field(..., min_length=5, description="Short title for the proposal")
    methodology: str = Field(
        ..., min_length=20, description="Proposed methodology to extend/resolve the problem"
    )
    expected_outcome: str = Field(
        ..., min_length=10, description="What results are expected"
    )
    required_resources: list[str] = Field(
        default_factory=list,
        description="Compute, data, tools needed",
    )
    experimental_steps: list[ExperimentalStep] = Field(
        default_factory=list, description="Concrete steps to execute"
    )
    metrics_to_evaluate: list[str] = Field(
        default_factory=list, description="Metrics to measure success"
    )
    confidence: float = Field(
        ge=0, le=1, default=0.5, description="Agent confidence in this proposal"
    )
    rationale: str = Field(
        default="", description="Why this continuation was proposed"
    )

    @field_validator("experimental_steps")
    @classmethod
    def validate_steps(cls, v: list[ExperimentalStep]) -> list[ExperimentalStep]:
        """Ensure steps are numbered sequentially."""
        for i, step in enumerate(v, 1):
            if step.step_number != i:
                step.step_number = i
        return v


# =============================================================================
# Evaluation Agent Schemas
# =============================================================================


class MetricResult(BaseModel):
    """Result of evaluating a single metric."""

    name: str = Field(..., description="Metric name")
    value: Optional[float] = Field(default=None, description="Computed value")
    baseline_value: Optional[float] = Field(
        default=None, description="Baseline value for comparison"
    )
    improvement: Optional[float] = Field(
        default=None, description="Improvement over baseline (fraction)"
    )
    notes: str = Field(default="", description="Additional context")


class EvaluationResult(BaseModel):
    """Output of the Evaluation Agent."""

    proposal_id: str = Field(
        default="", description="Reference to the continuation proposal"
    )
    feasibility_score: float = Field(
        ge=0, le=1, description="How feasible is this proposal (0-1)"
    )
    code_generated: str = Field(
        default="", description="Python code generated for evaluation"
    )
    execution_output: str = Field(
        default="", description="Stdout/stderr from sandboxed execution"
    )
    execution_success: bool = Field(
        default=False, description="Whether execution completed without errors"
    )
    metrics_results: list[MetricResult] = Field(
        default_factory=list, description="Results for each evaluated metric"
    )
    limitations: list[str] = Field(
        default_factory=list, description="Identified limitations of the approach"
    )
    verdict: str = Field(
        default="",
        description="Overall verdict: promising, inconclusive, or not_viable",
    )

    @field_validator("verdict")
    @classmethod
    def validate_verdict(cls, v: str) -> str:
        """Normalize verdict."""
        allowed = {"promising", "inconclusive", "not_viable", ""}
        normalized = v.lower().strip().replace(" ", "_")
        if normalized and normalized not in allowed:
            return "inconclusive"
        return normalized


# =============================================================================
# Synthesis Agent Schemas
# =============================================================================


class GraphUpdate(BaseModel):
    """A single update applied to the knowledge graph."""

    action: str = Field(
        ..., description="create_problem, create_relation, update_status"
    )
    target_id: Optional[str] = Field(
        default=None, description="ID of the created/updated entity"
    )
    details: str = Field(default="", description="Description of what was done")


class SynthesisReport(BaseModel):
    """Output of the Synthesis Agent."""

    summary: str = Field(
        ..., min_length=20, description="Summary of the workflow results"
    )
    new_problems: list[str] = Field(
        default_factory=list,
        description="Statements of new problems discovered",
    )
    new_relations: list[dict] = Field(
        default_factory=list,
        description="Relations created (source_id, target_id, type)",
    )
    graph_updates: list[GraphUpdate] = Field(
        default_factory=list, description="All updates applied to the KG"
    )
    source_problem_id: str = Field(
        default="", description="The original problem that was investigated"
    )
    recommendations: list[str] = Field(
        default_factory=list, description="Recommendations for next steps"
    )


# =============================================================================
# Human-in-the-Loop Schemas
# =============================================================================


class HumanCheckpoint(BaseModel):
    """Record of a human decision at a workflow checkpoint."""

    checkpoint_type: CheckpointType
    data: dict = Field(
        default_factory=dict, description="Data presented to the human"
    )
    decision: Optional[CheckpointDecision] = None
    feedback: str = Field(default="", description="Optional human feedback")
    edited_data: Optional[dict] = Field(
        default=None, description="Modified data if decision is EDIT"
    )
    timestamp: datetime = Field(default_factory=_utc_now)


# =============================================================================
# Workflow Summary Schemas
# =============================================================================


class WorkflowSummary(BaseModel):
    """Summary of a workflow run for listing."""

    run_id: str
    status: WorkflowStatus
    current_step: str = Field(default="", description="Current workflow step name")
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    problem_count: int = Field(
        default=0, description="Number of problems ranked"
    )
    selected_problem: Optional[str] = Field(
        default=None, description="Selected problem statement"
    )
    domain_filter: Optional[str] = None
