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
    MatchConfidence,
    MatchMethod,
    ProblemStatus,
    RelationType,
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
    Author,
    MatchCandidate,
    Paper,
    Problem,
    ProblemConcept,
    ProblemMention,
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
    "MatchConfidence",
    "MatchMethod",
    "ProblemStatus",
    "RelationType",
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
    "Author",
    "MatchCandidate",
    "Paper",
    "Problem",
    "ProblemConcept",
    "ProblemMention",
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
