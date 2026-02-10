"""
Knowledge Graph integration for canonical problem architecture (v2).

This module handles the new mention-to-concept workflow:
- Converting ExtractedProblem to ProblemMention entities
- Generating embeddings for mentions
- Matching mentions to concepts using ConceptMatcher
- Auto-linking HIGH confidence matches
- Creating new concepts when no HIGH match exists
- Checkpoint saves at each stage for rollback
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from agentic_kg.extraction.schemas import ExtractedProblem
from agentic_kg.knowledge_graph.auto_linker import AutoLinker, get_auto_linker
from agentic_kg.knowledge_graph.concept_matcher import ConceptMatcher, get_concept_matcher
from agentic_kg.knowledge_graph.embeddings import EmbeddingService
from agentic_kg.knowledge_graph.models import (
    Assumption,
    Baseline,
    Constraint,
    Dataset,
    ExtractionMetadata,
    MatchCandidate,
    MatchConfidence,
    Metric,
    ProblemConcept,
    ProblemMention,
    ReviewStatus,
)
from agentic_kg.knowledge_graph.repository import (
    Neo4jRepository,
    get_repository,
)

logger = logging.getLogger(__name__)


class MentionIntegrationResult(BaseModel):
    """Result of integrating a single extracted problem as a mention."""

    mention_id: str = Field(..., description="ProblemMention ID")
    concept_id: Optional[str] = Field(None, description="Linked ProblemConcept ID")
    is_new_concept: bool = Field(False, description="True if new concept created")
    match_confidence: Optional[str] = Field(None, description="Match confidence level")
    match_score: Optional[float] = Field(None, description="Similarity score")
    auto_linked: bool = Field(False, description="True if auto-linked (HIGH confidence)")
    trace_id: str = Field(..., description="Trace ID for audit trail")
    checkpoint_saved: bool = Field(False, description="True if checkpoint saved")
    error: Optional[str] = Field(None, description="Error message if failed")


class IntegrationResultV2(BaseModel):
    """Result of integrating extraction results into Knowledge Graph (v2)."""

    paper_doi: Optional[str] = Field(None, description="Paper DOI")
    paper_title: Optional[str] = Field(None, description="Paper title")
    trace_id: str = Field(..., description="Session trace ID")

    # Mention processing
    mentions_created: int = Field(0, description="ProblemMentions created")
    mentions_linked: int = Field(0, description="Mentions linked to existing concepts")
    mentions_new_concepts: int = Field(0, description="Mentions that created new concepts")

    # Detailed results
    mention_results: list[MentionIntegrationResult] = Field(
        default_factory=list, description="Results for each mention"
    )

    # Errors
    errors: list[str] = Field(default_factory=list, description="Error messages")
    checkpoints_saved: int = Field(0, description="Number of checkpoints saved")

    @property
    def success(self) -> bool:
        """True if no fatal errors occurred."""
        return len(self.errors) == 0

    @property
    def total_concepts_created(self) -> int:
        """Count of new concepts created."""
        return self.mentions_new_concepts


class KGIntegratorV2:
    """
    Integrates extraction results into Knowledge Graph using canonical architecture.

    Uses the mention-to-concept workflow with automatic linking for HIGH confidence
    matches and concept creation for new problems.
    """

    def __init__(
        self,
        repository: Optional[Neo4jRepository] = None,
        embedding_service: Optional[EmbeddingService] = None,
        concept_matcher: Optional[ConceptMatcher] = None,
        auto_linker: Optional[AutoLinker] = None,
    ):
        """
        Initialize KG integrator.

        Args:
            repository: Neo4j repository. Uses global if not provided.
            embedding_service: Embedding service. Creates new if not provided.
            concept_matcher: ConceptMatcher service. Creates new if not provided.
            auto_linker: AutoLinker service. Creates new if not provided.
        """
        self._repo = repository or get_repository()
        self._embedder = embedding_service or EmbeddingService()
        self._matcher = concept_matcher or get_concept_matcher(
            repository=self._repo,
            embedding_service=self._embedder,
        )
        self._linker = auto_linker or get_auto_linker(
            repository=self._repo,
            concept_matcher=self._matcher,
            embedding_service=self._embedder,
        )

    def integrate_extracted_problems(
        self,
        extracted_problems: list[ExtractedProblem],
        paper_doi: str,
        paper_title: Optional[str] = None,
        session_trace_id: Optional[str] = None,
    ) -> IntegrationResultV2:
        """
        Integrate extracted problems into Knowledge Graph.

        Workflow for each problem:
        1. Create ProblemMention node with embedding
        2. Match to existing concepts using ConceptMatcher
        3. If HIGH confidence: auto-link to concept
        4. If no HIGH confidence: create new concept
        5. Save checkpoint after each mention

        Args:
            extracted_problems: Problems extracted from paper.
            paper_doi: Source paper DOI.
            paper_title: Paper title (optional).
            session_trace_id: Session-level trace ID.

        Returns:
            IntegrationResultV2 with detailed results.
        """
        session_trace_id = session_trace_id or f"session-{uuid.uuid4()}"
        result = IntegrationResultV2(
            paper_doi=paper_doi,
            paper_title=paper_title,
            trace_id=session_trace_id,
        )

        logger.info(
            f"[{session_trace_id}] Integrating {len(extracted_problems)} problems "
            f"from paper {paper_doi}"
        )

        for idx, extracted_problem in enumerate(extracted_problems):
            try:
                mention_result = self._process_extracted_problem(
                    extracted_problem=extracted_problem,
                    paper_doi=paper_doi,
                    session_trace_id=session_trace_id,
                    problem_index=idx,
                )

                result.mention_results.append(mention_result)
                result.mentions_created += 1

                if mention_result.auto_linked:
                    result.mentions_linked += 1
                if mention_result.is_new_concept:
                    result.mentions_new_concepts += 1
                if mention_result.checkpoint_saved:
                    result.checkpoints_saved += 1

            except Exception as e:
                error_msg = f"Failed to process problem {idx}: {e}"
                logger.error(f"[{session_trace_id}] {error_msg}", exc_info=True)
                result.errors.append(error_msg)

        logger.info(
            f"[{session_trace_id}] Integration complete: "
            f"{result.mentions_created} mentions created, "
            f"{result.mentions_linked} linked, "
            f"{result.mentions_new_concepts} new concepts"
        )

        return result

    def _process_extracted_problem(
        self,
        extracted_problem: ExtractedProblem,
        paper_doi: str,
        session_trace_id: str,
        problem_index: int,
    ) -> MentionIntegrationResult:
        """
        Process a single extracted problem through the mention-to-concept workflow.

        Args:
            extracted_problem: Extracted problem to process.
            paper_doi: Source paper DOI.
            session_trace_id: Session trace ID.
            problem_index: Index of problem in batch.

        Returns:
            MentionIntegrationResult with processing details.
        """
        trace_id = f"{session_trace_id}-p{problem_index}"
        logger.info(f"[{trace_id}] Processing problem: {extracted_problem.statement[:100]}...")

        # Step 1: Create ProblemMention
        mention = self._create_problem_mention(
            extracted_problem=extracted_problem,
            paper_doi=paper_doi,
            trace_id=trace_id,
        )

        # Checkpoint 1: Mention created (before matching)
        checkpoint_1_saved = self._save_checkpoint(
            trace_id=trace_id,
            stage="mention_created",
            data={"mention_id": mention.id},
        )

        # Step 2: Generate embedding for mention
        try:
            embedding = self._embedder.generate_embedding(mention.statement)
            mention.embedding = embedding
            logger.debug(f"[{trace_id}] Generated embedding ({len(embedding)} dims)")
        except Exception as e:
            logger.error(f"[{trace_id}] Failed to generate embedding: {e}")
            return MentionIntegrationResult(
                mention_id=mention.id,
                trace_id=trace_id,
                checkpoint_saved=checkpoint_1_saved,
                error=f"Embedding generation failed: {e}",
            )

        # Step 3: Store mention in Neo4j
        try:
            self._store_mention_node(mention)
            logger.info(f"[{trace_id}] Stored ProblemMention {mention.id}")
        except Exception as e:
            logger.error(f"[{trace_id}] Failed to store mention: {e}")
            return MentionIntegrationResult(
                mention_id=mention.id,
                trace_id=trace_id,
                checkpoint_saved=checkpoint_1_saved,
                error=f"Mention storage failed: {e}",
            )

        # Step 4: Match to concepts (using AUTO_LINKER)
        try:
            # Try auto-linking (returns None if no HIGH confidence match)
            concept = self._linker.auto_link_high_confidence(
                mention=mention,
                trace_id=trace_id,
            )

            if concept:
                # HIGH confidence match found and linked
                logger.info(
                    f"[{trace_id}] AUTO-LINKED: mention {mention.id} -> concept {concept.id}"
                )
                return MentionIntegrationResult(
                    mention_id=mention.id,
                    concept_id=concept.id,
                    is_new_concept=False,
                    match_confidence="high",
                    match_score=None,  # Score stored in relationship
                    auto_linked=True,
                    trace_id=trace_id,
                    checkpoint_saved=True,
                )

            # No HIGH confidence match - create new concept
            concept = self._linker.create_new_concept(
                mention=mention,
                trace_id=trace_id,
            )

            logger.info(
                f"[{trace_id}] NEW CONCEPT: created concept {concept.id} from mention {mention.id}"
            )
            return MentionIntegrationResult(
                mention_id=mention.id,
                concept_id=concept.id,
                is_new_concept=True,
                match_confidence="high",  # New concept = perfect match to itself
                match_score=1.0,
                auto_linked=True,
                trace_id=trace_id,
                checkpoint_saved=True,
            )

        except Exception as e:
            logger.error(f"[{trace_id}] Failed to link mention: {e}", exc_info=True)
            return MentionIntegrationResult(
                mention_id=mention.id,
                trace_id=trace_id,
                checkpoint_saved=checkpoint_1_saved,
                error=f"Linking failed: {e}",
            )

    def _create_problem_mention(
        self,
        extracted_problem: ExtractedProblem,
        paper_doi: str,
        trace_id: str,
    ) -> ProblemMention:
        """
        Convert ExtractedProblem to ProblemMention.

        Args:
            extracted_problem: Extracted problem from LLM.
            paper_doi: Source paper DOI.
            trace_id: Trace ID for logging.

        Returns:
            ProblemMention model.
        """
        # Convert extracted fields to model types
        assumptions = [
            Assumption(text=a.text, implicit=a.implicit, confidence=a.confidence)
            for a in extracted_problem.assumptions
        ]
        constraints = [
            Constraint(text=c.text, type=c.type, confidence=c.confidence)
            for c in extracted_problem.constraints
        ]
        datasets = [
            Dataset(name=d.name, url=d.url, available=d.available, size=d.size)
            for d in extracted_problem.datasets
        ]
        metrics = [
            Metric(name=m.name, description=m.description, baseline_value=m.baseline_value)
            for m in extracted_problem.metrics
        ]
        baselines = [
            Baseline(name=b.name, paper_doi=b.paper_doi, performance=b.performance)
            for b in extracted_problem.baselines
        ]

        extraction_metadata = ExtractionMetadata(
            extracted_at=datetime.now(timezone.utc),
            extractor_version="1.0.0",
            extraction_model=extracted_problem.metadata.model if extracted_problem.metadata else "unknown",
            confidence_score=extracted_problem.metadata.confidence if extracted_problem.metadata else 0.8,
            human_reviewed=False,
        )

        mention = ProblemMention(
            id=str(uuid.uuid4()),
            statement=extracted_problem.statement,
            paper_doi=paper_doi,
            section=extracted_problem.section,
            domain=extracted_problem.domain,
            assumptions=assumptions,
            constraints=constraints,
            datasets=datasets,
            metrics=metrics,
            baselines=baselines,
            quoted_text=extracted_problem.quoted_text or extracted_problem.statement,
            extraction_metadata=extraction_metadata,
            embedding=None,  # Will be generated next
            concept_id=None,
            match_confidence=None,
            match_score=None,
            match_method=None,
            review_status=ReviewStatus.PENDING,
        )

        logger.debug(f"[{trace_id}] Created ProblemMention {mention.id}")
        return mention

    def _store_mention_node(self, mention: ProblemMention) -> None:
        """
        Store ProblemMention node in Neo4j.

        Args:
            mention: ProblemMention to store.

        Raises:
            Exception: If storage fails.
        """
        with self._repo.session() as session:
            query = """
            CREATE (m:ProblemMention)
            SET m = $properties
            """
            props = mention.to_neo4j_properties()
            session.run(query, properties=props)

    def _save_checkpoint(
        self,
        trace_id: str,
        stage: str,
        data: dict,
    ) -> bool:
        """
        Save checkpoint for rollback capability.

        In production, this would store checkpoint data in Neo4j or Redis.
        For now, just log the checkpoint.

        Args:
            trace_id: Trace ID for checkpoint.
            stage: Stage name (e.g., "mention_created", "concept_matched").
            data: Checkpoint data to save.

        Returns:
            True if checkpoint saved successfully.
        """
        logger.debug(f"[{trace_id}] CHECKPOINT [{stage}]: {data}")
        # TODO: Implement actual checkpoint storage (Neo4j or Redis)
        return True


def integrate_extraction_results_v2(
    extracted_problems: list[ExtractedProblem],
    paper_doi: str,
    paper_title: Optional[str] = None,
    session_trace_id: Optional[str] = None,
    repository: Optional[Neo4jRepository] = None,
) -> IntegrationResultV2:
    """
    Integrate extraction results using canonical architecture (convenience function).

    Args:
        extracted_problems: Problems extracted from paper.
        paper_doi: Source paper DOI.
        paper_title: Paper title (optional).
        session_trace_id: Session-level trace ID.
        repository: Neo4j repository. Uses global if not provided.

    Returns:
        IntegrationResultV2 with detailed results.
    """
    integrator = KGIntegratorV2(repository=repository)
    return integrator.integrate_extracted_problems(
        extracted_problems=extracted_problems,
        paper_doi=paper_doi,
        paper_title=paper_title,
        session_trace_id=session_trace_id,
    )
