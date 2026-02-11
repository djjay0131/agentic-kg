"""
Knowledge Graph Models Package.

Exports all entity, relationship, enum, and supporting models for backward
compatibility with existing imports.
"""

# Enums
from .enums import (
    ConstraintType,
    ContradictionType,
    DependencyType,
    EscalationReason,
    MatchConfidence,
    MatchMethod,
    ProblemStatus,
    RelationType,
    ReviewPriority,
    ReviewQueueStatus,
    ReviewResolution,
    ReviewStatus,
    WorkflowState,
)

# Supporting models
from .supporting import (
    Assumption,
    Baseline,
    Constraint,
    Dataset,
    Evidence,
    ExtractionMetadata,
    Metric,
)

# Entity models
from .entities import (
    AgentContextForReview,
    Author,
    MatchCandidate,
    Paper,
    PendingReview,
    Problem,
    ProblemConcept,
    ProblemMention,
    SuggestedConceptForReview,
)

# Relationship models
from .relationships import (
    AuthoredByRelation,
    ContradictsRelation,
    DependsOnRelation,
    ExtractedFromRelation,
    ExtendsRelation,
    InstanceOfRelation,
    ProblemRelation,
    ReframesRelation,
)

__all__ = [
    # Enums
    "ConstraintType",
    "ContradictionType",
    "DependencyType",
    "EscalationReason",
    "MatchConfidence",
    "MatchMethod",
    "ProblemStatus",
    "RelationType",
    "ReviewPriority",
    "ReviewQueueStatus",
    "ReviewResolution",
    "ReviewStatus",
    "WorkflowState",
    # Supporting models
    "Assumption",
    "Baseline",
    "Constraint",
    "Dataset",
    "Evidence",
    "ExtractionMetadata",
    "Metric",
    # Entity models
    "AgentContextForReview",
    "Author",
    "MatchCandidate",
    "Paper",
    "PendingReview",
    "Problem",
    "ProblemConcept",
    "ProblemMention",
    "SuggestedConceptForReview",
    # Relationship models
    "AuthoredByRelation",
    "ContradictsRelation",
    "DependsOnRelation",
    "ExtractedFromRelation",
    "ExtendsRelation",
    "InstanceOfRelation",
    "ProblemRelation",
    "ReframesRelation",
]
