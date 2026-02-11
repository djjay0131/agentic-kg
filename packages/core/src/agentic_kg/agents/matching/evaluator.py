"""
EvaluatorAgent for MEDIUM confidence matches.

Reviews problem mention-to-concept matches with 80-95% similarity and decides
whether to APPROVE, REJECT, or ESCALATE to multi-agent consensus.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, Field

from agentic_kg.agents.matching.schemas import EvaluatorDecision, EvaluatorResult
from agentic_kg.agents.matching.state import (
    MatchingWorkflowState,
    add_matching_error,
    add_matching_message,
)

if TYPE_CHECKING:
    from agentic_kg.extraction.llm_client import BaseLLMClient

logger = logging.getLogger(__name__)


class EvaluatorError(Exception):
    """Error during evaluation."""

    pass


# =============================================================================
# Prompt Template
# =============================================================================

EVALUATOR_SYSTEM_PROMPT = """You are a research problem matching expert. Your task is to decide whether a
problem mention from a paper should be linked to an existing canonical concept.

You will analyze semantic similarity, scope alignment, and domain context to make your decision.

IMPORTANT: Err on the side of APPROVE. Missing a duplicate is worse than linking related-but-distinct problems.
Only REJECT if the problems are clearly different in scope, meaning, or domain."""


EVALUATOR_USER_PROMPT = """## Problem Mention (from paper)
Statement: "{mention_statement}"
Domain: {mention_domain}
Paper DOI: {paper_doi}

## Candidate Concept
Canonical Statement: "{candidate_statement}"
Domain: {candidate_domain}
Current Mentions: {mention_count} mentions
Similarity Score: {similarity_score:.1%}

## Your Task
Decide whether these represent the SAME underlying research problem:

- APPROVE: They are the same problem (different wording, same meaning)
- REJECT: They are different problems (similar wording, different scope/meaning)
- ESCALATE: Genuinely uncertain, need deeper multi-agent analysis

Consider:
1. Semantic equivalence (not just keyword overlap)
2. Problem scope (broad vs narrow framing)
3. Domain context (same research area?)
4. Assumptions and constraints alignment

Return your analysis as structured JSON."""


# =============================================================================
# LLM Response Model
# =============================================================================


class EvaluatorLLMResponse(BaseModel):
    """Structured output from the LLM for evaluation."""

    decision: str = Field(
        ..., description="One of: approve, reject, escalate"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence in decision (0-1)"
    )
    reasoning: str = Field(
        ..., min_length=10, description="2-3 sentence explanation"
    )
    key_factors: list[str] = Field(
        default_factory=list, description="Key factors that influenced decision"
    )
    similarity_assessment: str = Field(
        default="", description="Assessment of semantic similarity"
    )
    domain_match: bool = Field(
        default=True, description="Whether domains are compatible"
    )


# =============================================================================
# EvaluatorAgent
# =============================================================================


class EvaluatorAgent:
    """
    Single-agent evaluator for MEDIUM confidence matches (80-95%).

    Reviews a problem mention and candidate concept, deciding whether to:
    - APPROVE: Link the mention to the concept
    - REJECT: Create a new concept instead
    - ESCALATE: Send to Maker/Hater/Arbiter consensus

    Target performance: <5 seconds per decision.
    """

    name: str = "EvaluatorAgent"

    def __init__(
        self,
        llm_client: BaseLLMClient,
        model: str = "gpt-4o",
        temperature: float = 0.2,
        max_tokens: int = 1024,
        timeout: float = 10.0,
    ) -> None:
        """
        Initialize the EvaluatorAgent.

        Args:
            llm_client: LLM client for making API calls.
            model: LLM model to use (gpt-4o recommended for speed).
            temperature: Lower temperature for more deterministic decisions.
            max_tokens: Token limit for response (~500 needed for structured output).
            timeout: Timeout in seconds (target: <5s response).
        """
        self.llm = llm_client
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    async def evaluate(
        self,
        state: MatchingWorkflowState,
    ) -> tuple[MatchingWorkflowState, EvaluatorResult]:
        """
        Evaluate a MEDIUM confidence match.

        Args:
            state: Current workflow state with mention and candidate info.

        Returns:
            Tuple of (updated state, evaluation result).

        Raises:
            EvaluatorError: If evaluation fails after retries.
        """
        trace_id = state.get("trace_id", "unknown")
        start_time = time.time()

        # Validate inputs
        mention_statement = state.get("mention_statement", "")
        candidate_statement = state.get("candidate_statement", "")

        if not mention_statement:
            error_msg = "Empty mention statement - cannot evaluate"
            logger.error(f"[{self.name}] {trace_id}: {error_msg}")
            updated_state = add_matching_error(state, error_msg)
            raise EvaluatorError(error_msg)

        if not candidate_statement:
            error_msg = "Empty candidate statement - cannot evaluate"
            logger.error(f"[{self.name}] {trace_id}: {error_msg}")
            updated_state = add_matching_error(state, error_msg)
            raise EvaluatorError(error_msg)

        # Build prompt
        prompt = EVALUATOR_USER_PROMPT.format(
            mention_statement=mention_statement,
            mention_domain=state.get("mention_domain") or "Not specified",
            paper_doi=state.get("paper_doi") or "Unknown",
            candidate_statement=candidate_statement,
            candidate_domain=state.get("candidate_domain") or "Not specified",
            mention_count=state.get("candidate_mention_count", 0),
            similarity_score=state.get("similarity_score", 0.0),
        )

        # Log the evaluation start
        logger.info(
            f"[{self.name}] {trace_id}: Evaluating match "
            f"(similarity={state.get('similarity_score', 0):.1%})"
        )

        try:
            # Call LLM with structured output
            response = await self.llm.extract(
                prompt=prompt,
                response_model=EvaluatorLLMResponse,
                system_prompt=EVALUATOR_SYSTEM_PROMPT,
            )

            llm_result = response.content

            # Parse decision into enum
            decision = self._parse_decision(llm_result.decision)

            # Build EvaluatorResult
            result = EvaluatorResult(
                decision=decision,
                confidence=llm_result.confidence,
                reasoning=llm_result.reasoning,
                key_factors=llm_result.key_factors or ["No factors specified"],
                similarity_assessment=llm_result.similarity_assessment,
                domain_match=llm_result.domain_match,
            )

            # Calculate duration
            duration_ms = int((time.time() - start_time) * 1000)

            # Update state
            updated_state = add_matching_message(
                state,
                self.name,
                f"Decision: {decision.value} (confidence={result.confidence:.2f}, "
                f"duration={duration_ms}ms)",
            )
            updated_state = {
                **updated_state,
                "evaluator_result": result.model_dump(mode="json"),
                "evaluator_decision": decision.value,
                "current_step": "evaluator_complete",
            }

            # Log decision
            logger.info(
                f"[{self.name}] {trace_id}: Decision={decision.value} "
                f"confidence={result.confidence:.2f} duration={duration_ms}ms"
            )

            return updated_state, result

        except json.JSONDecodeError as e:
            error_msg = f"Failed to parse LLM JSON response: {e}"
            logger.error(f"[{self.name}] {trace_id}: {error_msg}")
            updated_state = add_matching_error(state, error_msg)
            raise EvaluatorError(error_msg) from e

        except TimeoutError as e:
            error_msg = f"LLM request timed out after {self.timeout}s"
            logger.error(f"[{self.name}] {trace_id}: {error_msg}")
            updated_state = add_matching_error(state, error_msg)
            raise EvaluatorError(error_msg) from e

        except Exception as e:
            error_msg = f"Evaluation failed: {type(e).__name__}: {e}"
            logger.error(f"[{self.name}] {trace_id}: {error_msg}")
            updated_state = add_matching_error(state, error_msg)
            raise EvaluatorError(error_msg) from e

    def _parse_decision(self, decision_str: str) -> EvaluatorDecision:
        """Parse decision string into enum, defaulting to ESCALATE on unknown."""
        decision_lower = decision_str.lower().strip()

        if decision_lower == "approve":
            return EvaluatorDecision.APPROVE
        elif decision_lower == "reject":
            return EvaluatorDecision.REJECT
        elif decision_lower == "escalate":
            return EvaluatorDecision.ESCALATE
        else:
            logger.warning(
                f"[{self.name}] Unknown decision '{decision_str}', defaulting to ESCALATE"
            )
            return EvaluatorDecision.ESCALATE

    async def run(
        self, state: MatchingWorkflowState
    ) -> MatchingWorkflowState:
        """
        LangGraph node function: run evaluation and return updated state.

        Args:
            state: Current workflow state.

        Returns:
            Updated workflow state with evaluation result.
        """
        try:
            updated_state, _ = await self.evaluate(state)
            return updated_state
        except EvaluatorError:
            # State already updated with error in evaluate()
            return {
                **state,
                "evaluator_decision": "escalate",
                "current_step": "evaluator_error",
            }


# =============================================================================
# Factory Function
# =============================================================================


def create_evaluator_agent(
    llm_client: BaseLLMClient,
    model: str = "gpt-4o",
) -> EvaluatorAgent:
    """
    Create an EvaluatorAgent with default configuration.

    Args:
        llm_client: LLM client to use.
        model: LLM model (default: gpt-4o for speed).

    Returns:
        Configured EvaluatorAgent instance.
    """
    return EvaluatorAgent(
        llm_client=llm_client,
        model=model,
        temperature=0.2,
        max_tokens=1024,
        timeout=10.0,
    )
