"""
Matching agents package.

Provides agents for problem mention-to-concept matching decisions:
- EvaluatorAgent: Single-agent review for MEDIUM confidence (80-95%)
- MakerAgent: Argues FOR linking in consensus workflow
- HaterAgent: Argues AGAINST linking in consensus workflow
- ArbiterAgent: Decides after Maker/Hater debate
"""

from agentic_kg.agents.matching.schemas import (
    AgentContext,
    ArbiterDecision,
    ArbiterResult,
    Argument,
    EscalationReason,
    EvaluatorDecision,
    EvaluatorResult,
    HaterResult,
    MakerResult,
    MatchingWorkflowSummary,
    ReviewResolution,
    SuggestedConcept,
)
from agentic_kg.agents.matching.state import (
    MatchingWorkflowState,
    add_matching_error,
    add_matching_message,
    complete_matching_workflow,
    create_matching_state,
    escalate_to_human,
)
from agentic_kg.agents.matching.evaluator import (
    EvaluatorAgent,
    EvaluatorError,
    create_evaluator_agent,
)

__all__ = [
    # Agents
    "EvaluatorAgent",
    "EvaluatorError",
    "create_evaluator_agent",
    # Schemas
    "AgentContext",
    "ArbiterDecision",
    "ArbiterResult",
    "Argument",
    "EscalationReason",
    "EvaluatorDecision",
    "EvaluatorResult",
    "HaterResult",
    "MakerResult",
    "MatchingWorkflowSummary",
    "ReviewResolution",
    "SuggestedConcept",
    # State
    "MatchingWorkflowState",
    "add_matching_error",
    "add_matching_message",
    "complete_matching_workflow",
    "create_matching_state",
    "escalate_to_human",
]
