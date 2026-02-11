"""
HaterAgent for LOW confidence matches.

Argues AGAINST linking a problem mention to a candidate concept in the
Maker/Hater/Arbiter consensus workflow.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, Field

from agentic_kg.agents.matching.schemas import Argument, HaterResult
from agentic_kg.agents.matching.state import (
    MatchingWorkflowState,
    add_matching_error,
    add_matching_message,
)

if TYPE_CHECKING:
    from agentic_kg.extraction.llm_client import BaseLLMClient

logger = logging.getLogger(__name__)


class HaterError(Exception):
    """Error during Hater argument generation."""

    pass


# =============================================================================
# Prompt Template
# =============================================================================

HATER_SYSTEM_PROMPT = """You are the HATER agent in a research problem matching debate. Your role is to
argue AGAINST linking this mention to the candidate concept.

Your goal is to build the strongest possible case for why these problems should NOT be linked.
Be critical but fair - if the match seems strong, acknowledge it honestly.

Your role is to ensure we don't conflate genuinely distinct research problems."""


HATER_USER_PROMPT = """## Problem Mention (from paper)
Statement: "{mention_statement}"
Domain: {mention_domain}
Paper DOI: {paper_doi}

## Candidate Concept
Canonical Statement: "{candidate_statement}"
Domain: {candidate_domain}
Current Mentions: {mention_count} mentions
Similarity Score: {similarity_score:.1%}

## Your Task
Build the strongest possible case for why these should NOT be linked:

1. **Semantic Differences**: How do the problem statements differ in meaning?
2. **Scope Mismatch**: Is one broader/narrower than the other?
3. **Domain Divergence**: Different research communities or contexts?
4. **Conflating Risk**: Would linking these conflate genuinely distinct problems?
5. **Methodological Differences**: Different approaches suggesting different problems?

Provide 3-5 arguments with supporting evidence. Be specific and cite text from both statements.
If the match genuinely seems strong, say so honestly - don't manufacture weak arguments."""


# =============================================================================
# LLM Response Model
# =============================================================================


class HaterLLMResponse(BaseModel):
    """Structured output from the LLM for Hater arguments."""

    arguments: list[dict] = Field(
        ...,
        min_length=1,
        max_length=5,
        description="Arguments against linking, each with claim, evidence, strength",
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Overall confidence against linking (0-1)"
    )
    strongest_argument: str = Field(
        ..., description="Summary of the strongest argument against linking"
    )
    semantic_difference_evidence: str = Field(
        default="", description="Evidence of semantic differences"
    )
    domain_mismatch_evidence: str = Field(
        default="", description="Evidence of domain mismatch"
    )


# =============================================================================
# HaterAgent
# =============================================================================


class HaterAgent:
    """
    Hater agent for LOW confidence consensus workflow.

    Argues AGAINST linking a problem mention to a candidate concept.
    Part of the Maker/Hater/Arbiter debate pattern.

    Provides 3-5 arguments with evidence against the match.
    """

    name: str = "HaterAgent"

    def __init__(
        self,
        llm_client: BaseLLMClient,
        model: str = "gpt-4o",
        temperature: float = 0.3,
        max_tokens: int = 1500,
        timeout: float = 15.0,
    ) -> None:
        """
        Initialize the HaterAgent.

        Args:
            llm_client: LLM client for making API calls.
            model: LLM model to use.
            temperature: Slightly higher for creative argument generation.
            max_tokens: Token limit for response.
            timeout: Timeout in seconds.
        """
        self.llm = llm_client
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    async def argue(
        self,
        state: MatchingWorkflowState,
    ) -> tuple[MatchingWorkflowState, HaterResult]:
        """
        Generate arguments AGAINST linking.

        Args:
            state: Current workflow state with mention and candidate info.

        Returns:
            Tuple of (updated state, hater result).

        Raises:
            HaterError: If argument generation fails.
        """
        trace_id = state.get("trace_id", "unknown")
        round_num = state.get("current_round", 1)
        start_time = time.time()

        # Validate inputs
        mention_statement = state.get("mention_statement", "")
        candidate_statement = state.get("candidate_statement", "")

        if not mention_statement:
            error_msg = "Empty mention statement - cannot argue"
            logger.error(f"[{self.name}] {trace_id}: {error_msg}")
            raise HaterError(error_msg)

        if not candidate_statement:
            error_msg = "Empty candidate statement - cannot argue"
            logger.error(f"[{self.name}] {trace_id}: {error_msg}")
            raise HaterError(error_msg)

        # Build prompt
        prompt = HATER_USER_PROMPT.format(
            mention_statement=mention_statement,
            mention_domain=state.get("mention_domain") or "Not specified",
            paper_doi=state.get("paper_doi") or "Unknown",
            candidate_statement=candidate_statement,
            candidate_domain=state.get("candidate_domain") or "Not specified",
            mention_count=state.get("candidate_mention_count", 0),
            similarity_score=state.get("similarity_score", 0.0),
        )

        logger.info(
            f"[{self.name}] {trace_id}: Generating arguments AGAINST linking (round {round_num})"
        )

        try:
            # Call LLM with structured output
            response = await self.llm.extract(
                prompt=prompt,
                response_model=HaterLLMResponse,
                system_prompt=HATER_SYSTEM_PROMPT,
            )

            llm_result = response.content

            # Parse arguments into Argument objects
            arguments = []
            for arg_dict in llm_result.arguments:
                if isinstance(arg_dict, dict):
                    arguments.append(
                        Argument(
                            claim=arg_dict.get("claim", arg_dict.get("argument", "")),
                            evidence=arg_dict.get("evidence", ""),
                            strength=arg_dict.get("strength", 0.5),
                        )
                    )

            # Ensure at least one argument
            if not arguments:
                arguments.append(
                    Argument(
                        claim="Similarity score leaves room for distinct interpretations",
                        evidence="The problems may share keywords but differ in scope",
                        strength=0.3,
                    )
                )

            # Build HaterResult
            result = HaterResult(
                arguments=arguments,
                confidence=llm_result.confidence,
                strongest_argument=llm_result.strongest_argument,
                semantic_difference_evidence=llm_result.semantic_difference_evidence,
                domain_mismatch_evidence=llm_result.domain_mismatch_evidence,
            )

            # Calculate duration
            duration_ms = int((time.time() - start_time) * 1000)

            # Update state
            hater_results = list(state.get("hater_results", []))
            hater_results.append(result.model_dump(mode="json"))

            updated_state = add_matching_message(
                state,
                self.name,
                f"Generated {len(arguments)} arguments AGAINST linking "
                f"(confidence={result.confidence:.2f}, round={round_num}, duration={duration_ms}ms)",
            )
            updated_state = {
                **updated_state,
                "hater_results": hater_results,
                "current_step": "hater_complete",
            }

            logger.info(
                f"[{self.name}] {trace_id}: Generated {len(arguments)} arguments "
                f"(confidence={result.confidence:.2f}, duration={duration_ms}ms)"
            )

            return updated_state, result

        except Exception as e:
            error_msg = f"Hater argument generation failed: {type(e).__name__}: {e}"
            logger.error(f"[{self.name}] {trace_id}: {error_msg}")
            updated_state = add_matching_error(state, error_msg)
            raise HaterError(error_msg) from e

    async def run(self, state: MatchingWorkflowState) -> MatchingWorkflowState:
        """
        LangGraph node function: generate arguments and return updated state.

        Args:
            state: Current workflow state.

        Returns:
            Updated workflow state with hater result.
        """
        try:
            updated_state, _ = await self.argue(state)
            return updated_state
        except HaterError:
            return {
                **state,
                "current_step": "hater_error",
            }


# =============================================================================
# Factory Function
# =============================================================================


def create_hater_agent(
    llm_client: BaseLLMClient,
    model: str = "gpt-4o",
) -> HaterAgent:
    """
    Create a HaterAgent with default configuration.

    Args:
        llm_client: LLM client to use.
        model: LLM model (default: gpt-4o).

    Returns:
        Configured HaterAgent instance.
    """
    return HaterAgent(
        llm_client=llm_client,
        model=model,
        temperature=0.3,
        max_tokens=1500,
        timeout=15.0,
    )
