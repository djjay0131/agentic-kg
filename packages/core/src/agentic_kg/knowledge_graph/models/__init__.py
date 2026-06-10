"""
Knowledge Graph Models Package.

Exports all entity, relationship, enum, and supporting models for backward
compatibility with existing imports.
"""

# Enums
# Entity models
from .entities import (
    AgentContextForReview,
    Author,
    MatchCandidate,
    Method,
    Model,
    Paper,
    PendingReview,
    Problem,
    ProblemConcept,
    ProblemMention,
    ResearchConcept,
    SuggestedConceptForReview,
    Topic,
)
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
    TopicLevel,
    WorkflowState,
)

# Relationship models
from .relationships import (
    AuthoredByRelation,
    ContradictsRelation,
    DependsOnRelation,
    ExtendsRelation,
    ExtractedFromRelation,
    InstanceOfRelation,
    ProblemRelation,
    ReframesRelation,
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
    "TopicLevel",
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
    "Method",
    "Model",
    "Paper",
    "PendingReview",
    "Problem",
    "ProblemConcept",
    "ProblemMention",
    "ResearchConcept",
    "SuggestedConceptForReview",
    "Topic",
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
