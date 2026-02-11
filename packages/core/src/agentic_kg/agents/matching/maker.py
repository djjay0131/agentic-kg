"""
MakerAgent for LOW confidence matches.

Argues FOR linking a problem mention to a candidate concept in the
Maker/Hater/Arbiter consensus workflow.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, Field

from agentic_kg.agents.matching.schemas import Argument, MakerResult
from agentic_kg.agents.matching.state import (
    MatchingWorkflowState,
    add_matching_error,
    add_matching_message,
)

if TYPE_CHECKING:
    from agentic_kg.extraction.llm_client import BaseLLMClient

logger = logging.getLogger(__name__)


class MakerError(Exception):
    """Error during Maker argument generation."""

    pass


# =============================================================================
# Prompt Template
# =============================================================================

MAKER_SYSTEM_PROMPT = """You are the MAKER agent in a research problem matching debate. Your role is to
argue FOR linking this mention to the candidate concept.

Your goal is to build the strongest possible case for why these problems should be linked.
Be persuasive but honest - acknowledge weak points if they exist.

Remember: Missing a duplicate is worse than creating a false link. Err on the side of linking."""


MAKER_USER_PROMPT = """## Problem Mention (from paper)
Statement: "{mention_statement}"
Domain: {mention_domain}
Paper DOI: {paper_doi}

## Candidate Concept
Canonical Statement: "{candidate_statement}"
Domain: {candidate_domain}
Current Mentions: {mention_count} mentions
Similarity Score: {similarity_score:.1%}

## Your Task
Build the strongest possible case for why these should be linked:

1. **Semantic Similarity**: How are the problem statements semantically equivalent?
2. **Scope Alignment**: Do they address the same scope of research challenge?
3. **Domain Evidence**: Are they from the same research domain/community?
4. **Contextual Clues**: Citations, methodology overlap, metric similarity?
5. **Conservative Linking**: Remember, missing duplicates is worse than over-linking.

Provide 3-5 arguments with supporting evidence. Be specific and cite text from both statements."""


# =============================================================================
# LLM Response Model
# =============================================================================


class MakerLLMResponse(BaseModel):
    """Structured output from the LLM for Maker arguments."""

    arguments: list[dict] = Field(
        ...,
        min_length=1,
        max_length=5,
        description="Arguments for linking, each with claim, evidence, strength",
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Overall confidence in linking (0-1)"
    )
    strongest_argument: str = Field(
        ..., description="Summary of the strongest argument for linking"
    )
    semantic_similarity_evidence: str = Field(
        default="", description="Evidence of semantic similarity"
    )
    domain_alignment_evidence: str = Field(
        default="", description="Evidence of domain alignment"
    )


# =============================================================================
# MakerAgent
# =============================================================================


class MakerAgent:
    """
    Maker agent for LOW confidence consensus workflow.

    Argues FOR linking a problem mention to a candidate concept.
    Part of the Maker/Hater/Arbiter debate pattern.

    Provides 3-5 arguments with evidence supporting the match.
    """

    name: str = "MakerAgent"

    def __init__(
        self,
        llm_client: BaseLLMClient,
        model: str = "gpt-4o",
        temperature: float = 0.3,
        max_tokens: int = 1500,
        timeout: float = 15.0,
    ) -> None:
        """
        Initialize the MakerAgent.

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
    ) -> tuple[MatchingWorkflowState, MakerResult]:
        """
        Generate arguments FOR linking.

        Args:
            state: Current workflow state with mention and candidate info.

        Returns:
            Tuple of (updated state, maker result).

        Raises:
            MakerError: If argument generation fails.
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
            raise MakerError(error_msg)

        if not candidate_statement:
            error_msg = "Empty candidate statement - cannot argue"
            logger.error(f"[{self.name}] {trace_id}: {error_msg}")
            raise MakerError(error_msg)

        # Build prompt
        prompt = MAKER_USER_PROMPT.format(
            mention_statement=mention_statement,
            mention_domain=state.get("mention_domain") or "Not specified",
            paper_doi=state.get("paper_doi") or "Unknown",
            candidate_statement=candidate_statement,
            candidate_domain=state.get("candidate_domain") or "Not specified",
            mention_count=state.get("candidate_mention_count", 0),
            similarity_score=state.get("similarity_score", 0.0),
        )

        logger.info(
            f"[{self.name}] {trace_id}: Generating arguments FOR linking (round {round_num})"
        )

        try:
            # Call LLM with structured output
            response = await self.llm.extract(
                prompt=prompt,
                response_model=MakerLLMResponse,
                system_prompt=MAKER_SYSTEM_PROMPT,
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
                        claim="Semantic similarity suggests these are the same problem",
                        evidence="Vector similarity score indicates high overlap",
                        strength=0.5,
                    )
                )

            # Build MakerResult
            result = MakerResult(
                arguments=arguments,
                confidence=llm_result.confidence,
                strongest_argument=llm_result.strongest_argument,
                semantic_similarity_evidence=llm_result.semantic_similarity_evidence,
                domain_alignment_evidence=llm_result.domain_alignment_evidence,
            )

            # Calculate duration
            duration_ms = int((time.time() - start_time) * 1000)

            # Update state
            maker_results = list(state.get("maker_results", []))
            maker_results.append(result.model_dump(mode="json"))

            updated_state = add_matching_message(
                state,
                self.name,
                f"Generated {len(arguments)} arguments FOR linking "
                f"(confidence={result.confidence:.2f}, round={round_num}, duration={duration_ms}ms)",
            )
            updated_state = {
                **updated_state,
                "maker_results": maker_results,
                "current_step": "maker_complete",
            }

            logger.info(
                f"[{self.name}] {trace_id}: Generated {len(arguments)} arguments "
                f"(confidence={result.confidence:.2f}, duration={duration_ms}ms)"
            )

            return updated_state, result

        except Exception as e:
            error_msg = f"Maker argument generation failed: {type(e).__name__}: {e}"
            logger.error(f"[{self.name}] {trace_id}: {error_msg}")
            updated_state = add_matching_error(state, error_msg)
            raise MakerError(error_msg) from e

    async def run(self, state: MatchingWorkflowState) -> MatchingWorkflowState:
        """
        LangGraph node function: generate arguments and return updated state.

        Args:
            state: Current workflow state.

        Returns:
            Updated workflow state with maker result.
        """
        try:
            updated_state, _ = await self.argue(state)
            return updated_state
        except MakerError:
            return {
                **state,
                "current_step": "maker_error",
            }


# =============================================================================
# Factory Function
# =============================================================================


def create_maker_agent(
    llm_client: BaseLLMClient,
    model: str = "gpt-4o",
) -> MakerAgent:
    """
    Create a MakerAgent with default configuration.

    Args:
        llm_client: LLM client to use.
        model: LLM model (default: gpt-4o).

    Returns:
        Configured MakerAgent instance.
    """
    return MakerAgent(
        llm_client=llm_client,
        model=model,
        temperature=0.3,
        max_tokens=1500,
        timeout=15.0,
    )
