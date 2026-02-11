"""
ArbiterAgent for LOW confidence matches.

Weighs Maker and Hater arguments and makes a final decision in the
Maker/Hater/Arbiter consensus workflow.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, Field

from agentic_kg.agents.matching.schemas import (
    ArbiterDecision,
    ArbiterResult,
    MakerResult,
    HaterResult,
)
from agentic_kg.agents.matching.state import (
    MatchingWorkflowState,
    add_matching_error,
    add_matching_message,
)

if TYPE_CHECKING:
    from agentic_kg.extraction.llm_client import BaseLLMClient

logger = logging.getLogger(__name__)


# Confidence threshold for final decision
ARBITER_CONFIDENCE_THRESHOLD = 0.7


class ArbiterError(Exception):
    """Error during Arbiter decision making."""

    pass


# =============================================================================
# Prompt Template
# =============================================================================

ARBITER_SYSTEM_PROMPT = """You are the ARBITER agent. You've heard arguments from MAKER (pro-link) and
HATER (anti-link). Make a final decision.

Decision Framework:
1. Which arguments are most compelling and evidence-based?
2. Consider false positive vs false negative risk:
   - False positive (wrong link): ~5% acceptable, can be corrected
   - False negative (missed duplicate): MUST be near 0%, creates fragmentation
3. When in doubt, favor LINK (we can correct mistakes later)

Rules:
- If your confidence is below 0.7, you MUST return "retry"
- Be explicit about what tipped the scale"""


ARBITER_USER_PROMPT = """## Problem Mention (from paper)
Statement: "{mention_statement}"
Domain: {mention_domain}
Paper DOI: {paper_doi}

## Candidate Concept
Canonical Statement: "{candidate_statement}"
Domain: {candidate_domain}
Current Mentions: {mention_count} mentions
Similarity Score: {similarity_score:.1%}

## MAKER Arguments (for linking):
{maker_arguments}

## HATER Arguments (against linking):
{hater_arguments}

## Current Round: {round_num}/3
{round_context}

## Your Task
Weigh both sides and decide:
- **LINK**: The problems are the same, link them
- **CREATE_NEW**: The problems are different, create new concept
- **RETRY**: Cannot decide with confidence ≥0.7, need another round of debate

Remember: Missing a duplicate is worse than a false link. When in doubt, favor LINK."""


def format_arguments(result: dict) -> str:
    """Format agent arguments for the Arbiter prompt."""
    lines = []

    # Get arguments
    arguments = result.get("arguments", [])
    for i, arg in enumerate(arguments, 1):
        if isinstance(arg, dict):
            claim = arg.get("claim", "")
            evidence = arg.get("evidence", "")
            strength = arg.get("strength", 0.5)
            lines.append(f"{i}. **{claim}** (strength: {strength:.0%})")
            if evidence:
                lines.append(f"   Evidence: {evidence}")
        else:
            lines.append(f"{i}. {arg}")

    # Add strongest argument summary
    strongest = result.get("strongest_argument", "")
    if strongest:
        lines.append(f"\n**Strongest Argument:** {strongest}")

    # Add confidence
    confidence = result.get("confidence", 0.0)
    lines.append(f"**Agent Confidence:** {confidence:.0%}")

    return "\n".join(lines)


# =============================================================================
# LLM Response Model
# =============================================================================


class ArbiterLLMResponse(BaseModel):
    """Structured output from the LLM for Arbiter decision."""

    decision: str = Field(
        ..., description="One of: link, create_new, retry"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence in decision (0-1)"
    )
    reasoning: str = Field(
        ..., min_length=20, description="Explanation of how arguments were weighed"
    )
    maker_weight: float = Field(
        ge=0.0, le=1.0, description="How much Maker convinced (0-1)"
    )
    hater_weight: float = Field(
        ge=0.0, le=1.0, description="How much Hater convinced (0-1)"
    )
    decisive_factor: str = Field(
        ..., description="What tipped the scale"
    )
    false_negative_risk: str = Field(
        default="", description="Assessment of risk of missing duplicate"
    )


# =============================================================================
# ArbiterAgent
# =============================================================================


class ArbiterAgent:
    """
    Arbiter agent for LOW confidence consensus workflow.

    Weighs Maker (pro-link) and Hater (anti-link) arguments and makes
    a final decision. Part of the Maker/Hater/Arbiter debate pattern.

    Returns RETRY if confidence < 0.7 (triggering another round).
    After 3 rounds, escalates to human review.
    """

    name: str = "ArbiterAgent"

    def __init__(
        self,
        llm_client: BaseLLMClient,
        model: str = "gpt-4o",
        temperature: float = 0.2,
        max_tokens: int = 1500,
        timeout: float = 15.0,
        confidence_threshold: float = ARBITER_CONFIDENCE_THRESHOLD,
    ) -> None:
        """
        Initialize the ArbiterAgent.

        Args:
            llm_client: LLM client for making API calls.
            model: LLM model to use.
            temperature: Lower for more deterministic decisions.
            max_tokens: Token limit for response.
            timeout: Timeout in seconds.
            confidence_threshold: Min confidence for final decision (default 0.7).
        """
        self.llm = llm_client
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.confidence_threshold = confidence_threshold

    async def decide(
        self,
        state: MatchingWorkflowState,
    ) -> tuple[MatchingWorkflowState, ArbiterResult]:
        """
        Make a decision after hearing Maker and Hater arguments.

        Args:
            state: Current workflow state with maker/hater results.

        Returns:
            Tuple of (updated state, arbiter result).

        Raises:
            ArbiterError: If decision making fails.
        """
        trace_id = state.get("trace_id", "unknown")
        round_num = state.get("current_round", 1)
        max_rounds = state.get("max_rounds", 3)
        start_time = time.time()

        # Validate inputs
        mention_statement = state.get("mention_statement", "")
        candidate_statement = state.get("candidate_statement", "")

        if not mention_statement or not candidate_statement:
            error_msg = "Empty statement - cannot decide"
            logger.error(f"[{self.name}] {trace_id}: {error_msg}")
            raise ArbiterError(error_msg)

        # Get latest Maker and Hater results
        maker_results = state.get("maker_results", [])
        hater_results = state.get("hater_results", [])

        if not maker_results or not hater_results:
            error_msg = "Missing Maker or Hater arguments - cannot decide"
            logger.error(f"[{self.name}] {trace_id}: {error_msg}")
            raise ArbiterError(error_msg)

        # Use the most recent results
        latest_maker = maker_results[-1]
        latest_hater = hater_results[-1]

        # Format arguments for prompt
        maker_formatted = format_arguments(latest_maker)
        hater_formatted = format_arguments(latest_hater)

        # Round context
        if round_num < max_rounds:
            round_context = f"You may request a retry if uncertain (rounds remaining: {max_rounds - round_num})"
        else:
            round_context = "FINAL ROUND: You must make a decision (no more retries allowed)"

        # Build prompt
        prompt = ARBITER_USER_PROMPT.format(
            mention_statement=mention_statement,
            mention_domain=state.get("mention_domain") or "Not specified",
            paper_doi=state.get("paper_doi") or "Unknown",
            candidate_statement=candidate_statement,
            candidate_domain=state.get("candidate_domain") or "Not specified",
            mention_count=state.get("candidate_mention_count", 0),
            similarity_score=state.get("similarity_score", 0.0),
            maker_arguments=maker_formatted,
            hater_arguments=hater_formatted,
            round_num=round_num,
            round_context=round_context,
        )

        logger.info(
            f"[{self.name}] {trace_id}: Making decision (round {round_num}/{max_rounds})"
        )

        try:
            # Call LLM with structured output
            response = await self.llm.extract(
                prompt=prompt,
                response_model=ArbiterLLMResponse,
                system_prompt=ARBITER_SYSTEM_PROMPT,
            )

            llm_result = response.content

            # Parse decision
            decision = self._parse_decision(
                llm_result.decision,
                llm_result.confidence,
                round_num,
                max_rounds,
            )

            # Build ArbiterResult
            result = ArbiterResult(
                decision=decision,
                confidence=llm_result.confidence,
                reasoning=llm_result.reasoning,
                maker_weight=llm_result.maker_weight,
                hater_weight=llm_result.hater_weight,
                decisive_factor=llm_result.decisive_factor,
                false_negative_risk=llm_result.false_negative_risk,
            )

            # Calculate duration
            duration_ms = int((time.time() - start_time) * 1000)

            # Update state
            arbiter_results = list(state.get("arbiter_results", []))
            arbiter_results.append(result.model_dump(mode="json"))

            # Determine if consensus reached
            consensus_reached = decision != ArbiterDecision.RETRY

            updated_state = add_matching_message(
                state,
                self.name,
                f"Decision: {decision.value} (confidence={result.confidence:.2f}, "
                f"maker_weight={result.maker_weight:.2f}, hater_weight={result.hater_weight:.2f}, "
                f"round={round_num}, duration={duration_ms}ms)",
            )
            updated_state = {
                **updated_state,
                "arbiter_results": arbiter_results,
                "consensus_reached": consensus_reached,
                "current_step": "arbiter_complete",
                "final_confidence": result.confidence,
            }

            logger.info(
                f"[{self.name}] {trace_id}: Decision={decision.value} "
                f"confidence={result.confidence:.2f} "
                f"maker_weight={result.maker_weight:.2f} "
                f"hater_weight={result.hater_weight:.2f} "
                f"duration={duration_ms}ms"
            )

            return updated_state, result

        except Exception as e:
            error_msg = f"Arbiter decision failed: {type(e).__name__}: {e}"
            logger.error(f"[{self.name}] {trace_id}: {error_msg}")
            updated_state = add_matching_error(state, error_msg)
            raise ArbiterError(error_msg) from e

    def _parse_decision(
        self,
        decision_str: str,
        confidence: float,
        round_num: int,
        max_rounds: int,
    ) -> ArbiterDecision:
        """
        Parse decision string into enum with confidence enforcement.

        Rules:
        - If confidence < threshold and not final round: force RETRY
        - If final round: force LINK or CREATE_NEW (no retry)
        """
        decision_lower = decision_str.lower().strip()

        # Parse raw decision
        if decision_lower == "link":
            raw_decision = ArbiterDecision.LINK
        elif decision_lower == "create_new":
            raw_decision = ArbiterDecision.CREATE_NEW
        elif decision_lower == "retry":
            raw_decision = ArbiterDecision.RETRY
        else:
            logger.warning(
                f"[{self.name}] Unknown decision '{decision_str}', defaulting to RETRY"
            )
            raw_decision = ArbiterDecision.RETRY

        # Enforce confidence threshold
        if confidence < self.confidence_threshold and raw_decision != ArbiterDecision.RETRY:
            if round_num < max_rounds:
                logger.info(
                    f"[{self.name}] Confidence {confidence:.2f} < {self.confidence_threshold}, forcing RETRY"
                )
                return ArbiterDecision.RETRY
            else:
                # Final round - keep decision but log warning
                logger.warning(
                    f"[{self.name}] Final round with low confidence {confidence:.2f}, "
                    f"proceeding with {raw_decision.value}"
                )

        # Prevent retry on final round
        if raw_decision == ArbiterDecision.RETRY and round_num >= max_rounds:
            # Default to LINK on final round (conservative approach)
            logger.info(
                f"[{self.name}] Final round retry requested, defaulting to LINK"
            )
            return ArbiterDecision.LINK

        return raw_decision

    async def run(self, state: MatchingWorkflowState) -> MatchingWorkflowState:
        """
        LangGraph node function: make decision and return updated state.

        Args:
            state: Current workflow state.

        Returns:
            Updated workflow state with arbiter result.
        """
        try:
            updated_state, _ = await self.decide(state)
            return updated_state
        except ArbiterError:
            # On error, increment round and return for retry or escalation
            current_round = state.get("current_round", 1)
            max_rounds = state.get("max_rounds", 3)

            if current_round >= max_rounds:
                # Escalate to human review
                return {
                    **state,
                    "current_step": "arbiter_error_escalate",
                    "consensus_reached": False,
                }
            else:
                return {
                    **state,
                    "current_step": "arbiter_error",
                    "consensus_reached": False,
                }


# =============================================================================
# Factory Function
# =============================================================================


def create_arbiter_agent(
    llm_client: BaseLLMClient,
    model: str = "gpt-4o",
    confidence_threshold: float = ARBITER_CONFIDENCE_THRESHOLD,
) -> ArbiterAgent:
    """
    Create an ArbiterAgent with default configuration.

    Args:
        llm_client: LLM client to use.
        model: LLM model (default: gpt-4o).
        confidence_threshold: Min confidence for decision (default 0.7).

    Returns:
        Configured ArbiterAgent instance.
    """
    return ArbiterAgent(
        llm_client=llm_client,
        model=model,
        temperature=0.2,
        max_tokens=1500,
        timeout=15.0,
        confidence_threshold=confidence_threshold,
    )
