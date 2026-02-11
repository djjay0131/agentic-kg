"""
Enum definitions for the Knowledge Graph.

Defines all enumeration types used across entity and relationship models.
"""

from enum import Enum


class ProblemStatus(str, Enum):
    """Status of a research problem."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    DEPRECATED = "deprecated"


class ConstraintType(str, Enum):
    """Type of constraint on a research problem."""

    COMPUTATIONAL = "computational"
    DATA = "data"
    METHODOLOGICAL = "methodological"
    THEORETICAL = "theoretical"


class RelationType(str, Enum):
    """Types of relationships between problems."""

    EXTENDS = "EXTENDS"
    CONTRADICTS = "CONTRADICTS"
    DEPENDS_ON = "DEPENDS_ON"
    REFRAMES = "REFRAMES"


class ContradictionType(str, Enum):
    """Types of contradictions between problems."""

    EMPIRICAL = "empirical"
    THEORETICAL = "theoretical"
    METHODOLOGICAL = "methodological"


class DependencyType(str, Enum):
    """Types of dependencies between problems."""

    PREREQUISITE = "prerequisite"
    DATA_DEPENDENCY = "data_dependency"
    METHODOLOGICAL = "methodological"


class MatchConfidence(str, Enum):
    """Confidence level for problem mention to concept matching."""

    HIGH = "high"  # >95% similarity - auto-link
    MEDIUM = "medium"  # 80-95% similarity - agent review
    LOW = "low"  # 50-80% similarity - multi-agent consensus
    REJECTED = "rejected"  # Explicitly rejected match


class ReviewStatus(str, Enum):
    """Status of a problem mention in the review workflow."""

    PENDING = "pending"  # Awaiting review
    APPROVED = "approved"  # Human approved
    REJECTED = "rejected"  # Human rejected
    NEEDS_CONSENSUS = "needs_consensus"  # Requires multi-agent debate
    BLACKLISTED = "blacklisted"  # Permanently blocked


class MatchMethod(str, Enum):
    """Method used to match mention to concept."""

    AUTO = "auto"  # Automatic high-confidence match
    AGENT = "agent"  # Single agent review
    HUMAN = "human"  # Human decision
    CONSENSUS = "consensus"  # Multi-agent consensus


class WorkflowState(str, Enum):
    """States in the mention matching workflow."""

    EXTRACTED = "extracted"
    MATCHING = "matching"
    HIGH_CONFIDENCE = "high_confidence"
    MEDIUM_CONFIDENCE = "medium_confidence"
    LOW_CONFIDENCE = "low_confidence"
    NO_MATCH = "no_match"
    AGENT_REVIEW = "agent_review"
    NEEDS_CONSENSUS = "needs_consensus"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    BLACKLISTED = "blacklisted"
    AUTO_LINKED = "auto_linked"
    CREATE_NEW_CONCEPT = "create_new_concept"


class EscalationReason(str, Enum):
    """Reasons for escalating a match decision to human review."""

    EVALUATOR_UNCERTAIN = "evaluator_uncertain"  # Evaluator couldn't decide
    CONSENSUS_FAILED = "consensus_failed"  # Maker/Hater couldn't agree
    ARBITER_LOW_CONFIDENCE = "arbiter_low_confidence"  # Arbiter uncertain
    MAX_ROUNDS_EXCEEDED = "max_rounds_exceeded"  # Hit 3-round limit


class ReviewResolution(str, Enum):
    """How a human reviewer resolved the match decision."""

    LINKED = "linked"  # Linked to existing concept
    CREATED_NEW = "created_new"  # Created new concept
    BLACKLISTED = "blacklisted"  # Permanently blocked


class ReviewPriority(str, Enum):
    """Priority levels for human review queue items."""

    HIGH = "high"  # Important paper, urgent
    MEDIUM = "medium"  # Normal priority
    LOW = "low"  # Can wait


class ReviewQueueStatus(str, Enum):
    """Status of items in the human review queue."""

    PENDING = "pending"  # Awaiting assignment
    ASSIGNED = "assigned"  # Assigned to reviewer
    IN_REVIEW = "in_review"  # Being actively reviewed
    RESOLVED = "resolved"  # Decision made
    EXPIRED = "expired"  # SLA breached
