"""
Concept Refinement Service.

Refines canonical statements as mentions accumulate, synthesizing
the best representation from all linked mentions at threshold counts.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from agentic_kg.extraction.llm_client import BaseLLMClient, LLMError
from agentic_kg.knowledge_graph.models import (
    ProblemConcept,
    ProblemMention,
)
from agentic_kg.knowledge_graph.repository import Neo4jRepository, get_repository

logger = logging.getLogger(__name__)


# =============================================================================
# Response Models
# =============================================================================


class RefinementResult(BaseModel):
    """Result of canonical statement synthesis."""

    canonical_statement: str = Field(
        ...,
        min_length=20,
        description="The refined canonical problem statement",
    )


# =============================================================================
# Exceptions
# =============================================================================


class RefinementError(Exception):
    """Error during concept refinement."""

    pass


class ConceptNotFoundError(RefinementError):
    """Concept not found in database."""

    pass


# =============================================================================
# Service
# =============================================================================


class ConceptRefinementService:
    """
    Refines canonical statements as mentions accumulate.

    Triggers refinement at specific threshold counts (5, 10, 25, 50 mentions).
    Uses LLM to synthesize the best canonical statement from all mentions.
    Protects human-edited concepts from auto-refinement.
    """

    REFINEMENT_THRESHOLDS = [5, 10, 25, 50]

    def __init__(
        self,
        repository: Optional[Neo4jRepository] = None,
        llm_client: Optional[BaseLLMClient] = None,
    ):
        """
        Initialize the refinement service.

        Args:
            repository: Neo4j repository. Uses global repository if not provided.
            llm_client: LLM client for synthesis. Required for actual refinement.
        """
        self._repo = repository or get_repository()
        self._llm = llm_client

    async def check_and_refine(
        self,
        concept_id: str,
        trace_id: str,
    ) -> Optional[ProblemConcept]:
        """
        Check if concept needs refinement and refine if so.

        Refinement triggers when:
        1. mention_count is at a threshold (5, 10, 25, 50)
        2. Concept is NOT human-edited
        3. Concept hasn't already been refined at this threshold

        Args:
            concept_id: ID of the concept to potentially refine.
            trace_id: Trace ID for logging/debugging.

        Returns:
            Refined ProblemConcept if refinement occurred, None otherwise.

        Raises:
            ConceptNotFoundError: If concept doesn't exist.
            RefinementError: If refinement fails.
        """
        logger.debug(f"[{trace_id}] Checking refinement for concept {concept_id}")

        # Get concept
        concept = self._get_concept(concept_id)
        if concept is None:
            raise ConceptNotFoundError(f"Concept not found: {concept_id}")

        # Skip if human-edited
        if concept.human_edited:
            logger.info(
                f"[{trace_id}] Skipping refinement for human-edited concept {concept_id}"
            )
            return None

        # Check if at threshold
        if concept.mention_count not in self.REFINEMENT_THRESHOLDS:
            logger.debug(
                f"[{trace_id}] Concept {concept_id} not at threshold "
                f"(mention_count={concept.mention_count})"
            )
            return None

        # Check if already refined at this threshold
        last_refined = concept.last_refined_at_count or 0
        if last_refined >= concept.mention_count:
            logger.debug(
                f"[{trace_id}] Concept {concept_id} already refined at "
                f"mention_count={concept.mention_count}"
            )
            return None

        logger.info(
            f"[{trace_id}] Refining concept {concept_id} at "
            f"{concept.mention_count} mentions"
        )

        # Get all mentions for synthesis
        mentions = self._get_mentions_for_concept(concept_id)
        if not mentions:
            logger.warning(
                f"[{trace_id}] No mentions found for concept {concept_id}, "
                "skipping refinement"
            )
            return None

        # Synthesize new canonical statement
        try:
            new_statement = await self._synthesize(concept, mentions, trace_id)
        except Exception as e:
            raise RefinementError(
                f"[{trace_id}] Failed to synthesize canonical statement: {e}"
            ) from e

        # Update concept in database
        try:
            updated_concept = self._update_concept_after_refinement(
                concept_id=concept_id,
                new_statement=new_statement,
                mention_count=concept.mention_count,
                new_version=concept.version + 1,
                trace_id=trace_id,
            )
            logger.info(
                f"[{trace_id}] Successfully refined concept {concept_id} "
                f"(v{concept.version} -> v{updated_concept.version})"
            )
            return updated_concept

        except Exception as e:
            raise RefinementError(
                f"[{trace_id}] Failed to update concept after refinement: {e}"
            ) from e

    def _get_concept(self, concept_id: str) -> Optional[ProblemConcept]:
        """Get concept by ID from database."""
        query = """
        MATCH (c:ProblemConcept {id: $concept_id})
        RETURN c
        """

        with self._repo.session() as session:
            result = session.run(query, concept_id=concept_id)
            record = result.single()

            if not record:
                return None

            node = record["c"]
            return self._node_to_concept(node)

    def _get_mentions_for_concept(self, concept_id: str) -> list[ProblemMention]:
        """Get all mentions linked to a concept."""
        query = """
        MATCH (m:ProblemMention)-[:INSTANCE_OF]->(c:ProblemConcept {id: $concept_id})
        RETURN m
        ORDER BY m.created_at ASC
        """

        with self._repo.session() as session:
            result = session.run(query, concept_id=concept_id)
            mentions = []

            for record in result:
                node = record["m"]
                mention = self._node_to_mention(node)
                mentions.append(mention)

            return mentions

    def _update_concept_after_refinement(
        self,
        concept_id: str,
        new_statement: str,
        mention_count: int,
        new_version: int,
        trace_id: str,
    ) -> ProblemConcept:
        """Update concept in database after refinement."""
        query = """
        MATCH (c:ProblemConcept {id: $concept_id})
        SET c.canonical_statement = $new_statement,
            c.synthesis_method = 'synthesized',
            c.synthesized_at = datetime($synthesized_at),
            c.synthesized_by = 'refinement_agent',
            c.version = $new_version,
            c.last_refined_at_count = $mention_count,
            c.updated_at = datetime($updated_at)
        RETURN c
        """

        now = datetime.now(timezone.utc)

        with self._repo.session() as session:
            result = session.run(
                query,
                concept_id=concept_id,
                new_statement=new_statement,
                synthesized_at=now.isoformat(),
                new_version=new_version,
                mention_count=mention_count,
                updated_at=now.isoformat(),
            )
            record = result.single()

            if not record:
                raise RefinementError(f"Concept {concept_id} not found during update")

            return self._node_to_concept(record["c"])

    async def _synthesize(
        self,
        concept: ProblemConcept,
        mentions: list[ProblemMention],
        trace_id: str,
    ) -> str:
        """
        Synthesize canonical statement from all mentions using LLM.

        Args:
            concept: Current concept to refine.
            mentions: All mentions linked to this concept.
            trace_id: Trace ID for logging.

        Returns:
            New canonical statement string.

        Raises:
            RefinementError: If LLM synthesis fails.
        """
        if self._llm is None:
            raise RefinementError("LLM client not configured for refinement")

        # Build mentions text
        mentions_text = "\n".join(
            [f"- Paper {m.paper_doi}: \"{m.statement}\"" for m in mentions]
        )

        prompt = f"""You are synthesizing a canonical problem statement from multiple paper mentions.

Current Canonical Statement:
"{concept.canonical_statement}"

All {len(mentions)} Mentions:
{mentions_text}

Create a refined canonical statement that:
1. Captures the essence of ALL mentions
2. Is clear and concise (1-2 sentences)
3. Is general enough to encompass all framings
4. Is specific enough to distinguish from related problems
5. Avoids paper-specific details

Return ONLY the refined statement, no explanation or quotes."""

        system_prompt = "You are a research problem synthesis expert. Your task is to create clear, canonical problem statements."

        try:
            response = await self._llm.extract(
                prompt=prompt,
                response_model=RefinementResult,
                system_prompt=system_prompt,
            )
            return response.content.canonical_statement

        except LLMError as e:
            logger.error(f"[{trace_id}] LLM synthesis failed: {e}")
            raise RefinementError(f"LLM synthesis failed: {e}") from e

    def _node_to_concept(self, node: dict) -> ProblemConcept:
        """Convert Neo4j node to ProblemConcept."""
        # Handle nested JSON fields
        assumptions = node.get("assumptions", [])
        if isinstance(assumptions, str):
            assumptions = json.loads(assumptions)

        constraints = node.get("constraints", [])
        if isinstance(constraints, str):
            constraints = json.loads(constraints)

        datasets = node.get("datasets", [])
        if isinstance(datasets, str):
            datasets = json.loads(datasets)

        metrics = node.get("metrics", [])
        if isinstance(metrics, str):
            metrics = json.loads(metrics)

        verified_baselines = node.get("verified_baselines", [])
        if isinstance(verified_baselines, str):
            verified_baselines = json.loads(verified_baselines)

        claimed_baselines = node.get("claimed_baselines", [])
        if isinstance(claimed_baselines, str):
            claimed_baselines = json.loads(claimed_baselines)

        return ProblemConcept(
            id=node["id"],
            canonical_statement=node["canonical_statement"],
            status=node.get("status", "open"),
            assumptions=assumptions,
            constraints=constraints,
            datasets=datasets,
            metrics=metrics,
            verified_baselines=verified_baselines,
            claimed_baselines=claimed_baselines,
            synthesis_method=node.get("synthesis_method", "first_mention"),
            synthesis_model=node.get("synthesis_model"),
            synthesized_at=node.get("synthesized_at"),
            synthesized_by=node.get("synthesized_by"),
            human_edited=node.get("human_edited", False),
            mention_count=node.get("mention_count", 0),
            paper_count=node.get("paper_count", 0),
            first_mentioned_year=node.get("first_mentioned_year"),
            last_mentioned_year=node.get("last_mentioned_year"),
            version=node.get("version", 1),
            last_refined_at_count=node.get("last_refined_at_count"),
        )

    def _node_to_mention(self, node: dict) -> ProblemMention:
        """Convert Neo4j node to ProblemMention."""
        # Handle nested JSON fields
        assumptions = node.get("assumptions", [])
        if isinstance(assumptions, str):
            assumptions = json.loads(assumptions)

        constraints = node.get("constraints", [])
        if isinstance(constraints, str):
            constraints = json.loads(constraints)

        datasets = node.get("datasets", [])
        if isinstance(datasets, str):
            datasets = json.loads(datasets)

        metrics = node.get("metrics", [])
        if isinstance(metrics, str):
            metrics = json.loads(metrics)

        baselines = node.get("baselines", [])
        if isinstance(baselines, str):
            baselines = json.loads(baselines)

        return ProblemMention(
            id=node["id"],
            statement=node["statement"],
            paper_doi=node["paper_doi"],
            paper_title=node.get("paper_title"),
            section=node.get("section"),
            quoted_text=node.get("quoted_text"),
            assumptions=assumptions,
            constraints=constraints,
            datasets=datasets,
            metrics=metrics,
            baselines=baselines,
            confidence_score=node.get("confidence_score", 0.0),
            concept_id=node.get("concept_id"),
            match_confidence=node.get("match_confidence"),
            match_score=node.get("match_score"),
            match_method=node.get("match_method"),
            review_status=node.get("review_status", "pending"),
            workflow_state=node.get("workflow_state", "extracted"),
        )


# =============================================================================
# Singleton Pattern
# =============================================================================


_refinement_service: Optional[ConceptRefinementService] = None


def get_refinement_service(
    repository: Optional[Neo4jRepository] = None,
    llm_client: Optional[BaseLLMClient] = None,
) -> ConceptRefinementService:
    """
    Get or create the concept refinement service singleton.

    Args:
        repository: Neo4j repository. Uses global if not provided.
        llm_client: LLM client for synthesis.

    Returns:
        ConceptRefinementService instance.
    """
    global _refinement_service
    if _refinement_service is None:
        _refinement_service = ConceptRefinementService(
            repository=repository,
            llm_client=llm_client,
        )
    return _refinement_service


def reset_refinement_service() -> None:
    """Reset the singleton (for testing)."""
    global _refinement_service
    _refinement_service = None
