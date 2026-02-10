"""
Auto-linking service for HIGH confidence matches.

Automatically links problem mentions to canonical concepts when similarity
is >95%, or creates new concepts when no HIGH confidence match exists.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from neo4j import ManagedTransaction

from agentic_kg.knowledge_graph.concept_matcher import ConceptMatcher, get_concept_matcher
from agentic_kg.knowledge_graph.embeddings import EmbeddingService
from agentic_kg.knowledge_graph.models import (
    InstanceOfRelation,
    MatchCandidate,
    MatchConfidence,
    MatchMethod,
    ProblemConcept,
    ProblemMention,
    ProblemStatus,
    ReviewStatus,
)
from agentic_kg.knowledge_graph.repository import Neo4jRepository, get_repository

logger = logging.getLogger(__name__)


class AutoLinkerError(Exception):
    """Raised when auto-linking fails."""

    pass


class AutoLinker:
    """
    Service for automatically linking problem mentions to canonical concepts.

    Handles HIGH confidence matches (>95% similarity) by creating INSTANCE_OF
    relationships. Creates new concepts when no HIGH confidence match exists.
    """

    def __init__(
        self,
        repository: Optional[Neo4jRepository] = None,
        concept_matcher: Optional[ConceptMatcher] = None,
        embedding_service: Optional[EmbeddingService] = None,
    ):
        """
        Initialize auto-linker.

        Args:
            repository: Neo4j repository. Uses global repository if not provided.
            concept_matcher: ConceptMatcher service. Creates new instance if not provided.
            embedding_service: Embedding service. Creates new instance if not provided.
        """
        self._repo = repository or get_repository()
        self._matcher = concept_matcher or get_concept_matcher(
            repository=self._repo,
            embedding_service=embedding_service or EmbeddingService(),
        )
        self._embedder = embedding_service or EmbeddingService()

    def auto_link_high_confidence(
        self,
        mention: ProblemMention,
        trace_id: Optional[str] = None,
    ) -> Optional[ProblemConcept]:
        """
        Auto-link a mention to a concept if HIGH confidence match exists.

        Workflow:
        1. Find best matching concept using ConceptMatcher
        2. If confidence is HIGH (>95%), create INSTANCE_OF relationship
        3. Update mention workflow_state to AUTO_LINKED
        4. Update concept mention_count and updated_at
        5. Return linked concept

        If no HIGH confidence match, returns None (caller should create new concept).

        Args:
            mention: Problem mention to link.
            trace_id: Optional trace ID for request tracking.

        Returns:
            Linked ProblemConcept if HIGH confidence match found, else None.

        Raises:
            AutoLinkerError: If linking fails.
        """
        trace_id = trace_id or str(uuid.uuid4())
        logger.info(f"[{trace_id}] Auto-linking mention {mention.id}")

        # Find best matching concept
        try:
            best_candidate = self._matcher.match_mention_to_concept(
                mention, auto_link_high_confidence=False
            )
        except Exception as e:
            raise AutoLinkerError(
                f"[{trace_id}] Failed to find matching concept: {e}"
            ) from e

        # Check if HIGH confidence
        if not best_candidate or best_candidate.confidence != MatchConfidence.HIGH:
            logger.info(
                f"[{trace_id}] No HIGH confidence match for mention {mention.id} "
                f"(best: {best_candidate.confidence.value if best_candidate else 'none'})"
            )
            return None

        logger.info(
            f"[{trace_id}] HIGH confidence match found: "
            f"mention {mention.id} -> concept {best_candidate.concept_id} "
            f"(score: {best_candidate.final_score:.3f})"
        )

        # Create INSTANCE_OF relationship in transaction
        try:
            concept = self._create_instance_of_relationship(
                mention=mention,
                candidate=best_candidate,
                trace_id=trace_id,
            )
            logger.info(
                f"[{trace_id}] Successfully auto-linked mention {mention.id} "
                f"to concept {concept.id}"
            )
            return concept

        except Exception as e:
            logger.error(
                f"[{trace_id}] Failed to create INSTANCE_OF relationship: {e}",
                exc_info=True,
            )
            raise AutoLinkerError(
                f"[{trace_id}] Auto-linking failed: {e}"
            ) from e

    def create_new_concept(
        self,
        mention: ProblemMention,
        trace_id: Optional[str] = None,
    ) -> ProblemConcept:
        """
        Create a new canonical concept from a mention.

        Called when no HIGH confidence match exists. The mention becomes
        the first instance of a new canonical problem concept.

        Workflow:
        1. Create ProblemConcept with canonical_statement = mention.statement
        2. Generate embedding for concept
        3. Create INSTANCE_OF relationship linking mention to concept
        4. Set concept.mention_count = 1

        Args:
            mention: Problem mention to create concept from.
            trace_id: Optional trace ID for request tracking.

        Returns:
            Newly created ProblemConcept.

        Raises:
            AutoLinkerError: If concept creation fails.
        """
        trace_id = trace_id or str(uuid.uuid4())
        logger.info(
            f"[{trace_id}] Creating new concept from mention {mention.id}"
        )

        # Generate embedding for concept (reuse mention's statement)
        try:
            embedding = self._embedder.generate_embedding(mention.statement)
        except Exception as e:
            raise AutoLinkerError(
                f"[{trace_id}] Failed to generate concept embedding: {e}"
            ) from e

        # Create new concept
        concept = ProblemConcept(
            id=str(uuid.uuid4()),
            canonical_statement=mention.statement,  # Initially same as mention
            domain=mention.domain or "unknown",
            status=ProblemStatus.OPEN,
            assumptions=mention.assumptions,
            constraints=mention.constraints,
            datasets=mention.datasets,
            metrics=mention.metrics,
            verified_baselines=[],
            claimed_baselines=mention.baselines,
            synthesis_method="first_mention",
            synthesis_model=None,
            synthesized_at=datetime.now(timezone.utc),
            synthesized_by="auto_linker",
            human_edited=False,
            mention_count=1,
            paper_count=1,
            first_mentioned_year=None,  # TODO: Extract from paper metadata
            last_mentioned_year=None,
            embedding=embedding,
            version=1,
        )

        # Create concept and link in transaction
        try:
            self._create_concept_and_link(
                concept=concept,
                mention=mention,
                trace_id=trace_id,
            )
            logger.info(
                f"[{trace_id}] Successfully created new concept {concept.id} "
                f"from mention {mention.id}"
            )
            return concept

        except Exception as e:
            logger.error(
                f"[{trace_id}] Failed to create new concept: {e}",
                exc_info=True,
            )
            raise AutoLinkerError(
                f"[{trace_id}] Concept creation failed: {e}"
            ) from e

    def _create_instance_of_relationship(
        self,
        mention: ProblemMention,
        candidate: MatchCandidate,
        trace_id: str,
    ) -> ProblemConcept:
        """
        Create INSTANCE_OF relationship between mention and concept.

        Updates:
        - Creates (ProblemMention)-[:INSTANCE_OF]->(ProblemConcept) relationship
        - Updates mention.concept_id, match_confidence, match_score, match_method
        - Updates mention.review_status to APPROVED
        - Updates concept.mention_count += 1
        - Updates concept.updated_at

        All operations are atomic (transaction).

        Args:
            mention: Problem mention to link.
            candidate: Matching concept candidate.
            trace_id: Trace ID for logging.

        Returns:
            Updated ProblemConcept.

        Raises:
            Exception: If transaction fails (triggers rollback).
        """

        def _link(tx: ManagedTransaction) -> dict:
            # Create INSTANCE_OF relationship
            query = """
            MATCH (m:ProblemMention {id: $mention_id})
            MATCH (c:ProblemConcept {id: $concept_id})

            // Create relationship
            MERGE (m)-[r:INSTANCE_OF]->(c)
            SET r.confidence = $confidence,
                r.match_method = $match_method,
                r.matched_at = datetime($matched_at),
                r.matched_by = 'auto_linker',
                r.trace_id = $trace_id

            // Update mention
            SET m.concept_id = $concept_id,
                m.match_confidence = $match_confidence,
                m.match_score = $match_score,
                m.match_method = $match_method,
                m.review_status = $review_status,
                m.updated_at = datetime($updated_at)

            // Update concept counts
            SET c.mention_count = c.mention_count + 1,
                c.updated_at = datetime($updated_at)

            RETURN c
            """

            result = tx.run(
                query,
                mention_id=mention.id,
                concept_id=candidate.concept_id,
                confidence=candidate.final_score,
                match_method=MatchMethod.AUTO.value,
                matched_at=datetime.now(timezone.utc).isoformat(),
                trace_id=trace_id,
                match_confidence=candidate.confidence.value,
                match_score=candidate.final_score,
                review_status=ReviewStatus.APPROVED.value,
                updated_at=datetime.now(timezone.utc).isoformat(),
            )

            record = result.single()
            if not record:
                raise AutoLinkerError(
                    f"Failed to create INSTANCE_OF relationship: mention or concept not found"
                )

            return record["c"]

        with self._repo.session() as session:
            concept_data = session.execute_write(_link)

            # Convert Neo4j node to ProblemConcept
            # Note: This is a simplified conversion - full implementation would
            # deserialize all fields properly
            concept = ProblemConcept(
                id=concept_data["id"],
                canonical_statement=concept_data["canonical_statement"],
                domain=concept_data["domain"],
                status=ProblemStatus(concept_data["status"]),
                mention_count=concept_data["mention_count"],
                paper_count=concept_data.get("paper_count", 1),
            )

            return concept

    def _create_concept_and_link(
        self,
        concept: ProblemConcept,
        mention: ProblemMention,
        trace_id: str,
    ) -> None:
        """
        Create new concept node and link mention to it.

        All operations are atomic (transaction).

        Args:
            concept: New ProblemConcept to create.
            mention: Problem mention to link.
            trace_id: Trace ID for logging.

        Raises:
            Exception: If transaction fails (triggers rollback).
        """

        def _create(tx: ManagedTransaction) -> None:
            # Create concept node
            create_concept_query = """
            CREATE (c:ProblemConcept)
            SET c = $properties
            """

            concept_props = concept.to_neo4j_properties()
            tx.run(create_concept_query, properties=concept_props)

            # Create INSTANCE_OF relationship
            link_query = """
            MATCH (m:ProblemMention {id: $mention_id})
            MATCH (c:ProblemConcept {id: $concept_id})

            MERGE (m)-[r:INSTANCE_OF]->(c)
            SET r.confidence = 1.0,
                r.match_method = 'auto',
                r.matched_at = datetime($matched_at),
                r.matched_by = 'auto_linker',
                r.trace_id = $trace_id

            SET m.concept_id = $concept_id,
                m.match_confidence = 'high',
                m.match_score = 1.0,
                m.match_method = 'auto',
                m.review_status = 'approved',
                m.updated_at = datetime($updated_at)
            """

            tx.run(
                link_query,
                mention_id=mention.id,
                concept_id=concept.id,
                matched_at=datetime.now(timezone.utc).isoformat(),
                trace_id=trace_id,
                updated_at=datetime.now(timezone.utc).isoformat(),
            )

        with self._repo.session() as session:
            session.execute_write(_create)


def get_auto_linker(
    repository: Optional[Neo4jRepository] = None,
    concept_matcher: Optional[ConceptMatcher] = None,
    embedding_service: Optional[EmbeddingService] = None,
) -> AutoLinker:
    """
    Get an AutoLinker instance (convenience function).

    Args:
        repository: Neo4j repository. Uses global repository if not provided.
        concept_matcher: ConceptMatcher service. Creates new instance if not provided.
        embedding_service: Embedding service. Creates new instance if not provided.

    Returns:
        AutoLinker instance.
    """
    return AutoLinker(
        repository=repository,
        concept_matcher=concept_matcher,
        embedding_service=embedding_service,
    )
