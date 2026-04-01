"""
Knowledge Graph integration for canonical problem architecture (v2).

This module handles the new mention-to-concept workflow:
- Converting ExtractedProblem to ProblemMention entities
- Generating embeddings for mentions
- Matching mentions to concepts using ConceptMatcher
- Auto-linking HIGH confidence matches
- Agent workflows for MEDIUM/LOW confidence (Phase 2)
- Human review queue for escalated matches
- Concept refinement at mention thresholds
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
    SuggestedConceptForReview,
    AgentContextForReview,
    ReviewPriority,
)
from agentic_kg.knowledge_graph.repository import (
    Neo4jRepository,
    get_repository,
)

# Phase 2 imports: Agent workflow and concept refinement
from agentic_kg.agents.matching.workflow import process_medium_low_confidence
from agentic_kg.agents.matching.state import create_matching_state, MatchingWorkflowState
from agentic_kg.knowledge_graph.concept_refinement import (
    ConceptRefinementService,
    get_refinement_service,
)
from agentic_kg.knowledge_graph.review_queue import (
    ReviewQueueService,
    get_review_queue_service,
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
    agent_workflow_used: bool = Field(False, description="True if agent workflow processed")
    workflow_decision: Optional[str] = Field(None, description="Agent workflow decision")
    human_review_id: Optional[str] = Field(None, description="Human review queue ID if escalated")
    concept_refined: bool = Field(False, description="True if concept was refined")
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
        refinement_service: Optional[ConceptRefinementService] = None,
        review_queue_service: Optional[ReviewQueueService] = None,
        enable_agent_workflow: bool = True,
        enable_concept_refinement: bool = True,
    ):
        """
        Initialize KG integrator.

        Args:
            repository: Neo4j repository. Uses global if not provided.
            embedding_service: Embedding service. Creates new if not provided.
            concept_matcher: ConceptMatcher service. Creates new if not provided.
            auto_linker: AutoLinker service. Creates new if not provided.
            refinement_service: ConceptRefinementService. Creates new if not provided.
            review_queue_service: ReviewQueueService. Creates new if not provided.
            enable_agent_workflow: Whether to use agent workflow for MEDIUM/LOW confidence.
            enable_concept_refinement: Whether to trigger concept refinement after linking.
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
        self._refinement = refinement_service
        self._review_queue = review_queue_service
        self._enable_agent_workflow = enable_agent_workflow
        self._enable_concept_refinement = enable_concept_refinement

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

        # Step 4: Match to concepts with confidence-based routing
        try:
            # Get best match candidate with confidence classification
            best_candidate = self._matcher.match_mention_to_concept(
                mention, auto_link_high_confidence=False
            )

            # Route based on confidence level
            if not best_candidate:
                # NO_MATCH: Create new concept
                return self._handle_no_match(mention, trace_id, checkpoint_1_saved)

            confidence = best_candidate.confidence

            if confidence == MatchConfidence.HIGH:
                # HIGH confidence (>95%): Auto-link
                return self._handle_high_confidence(
                    mention, best_candidate, trace_id, checkpoint_1_saved
                )

            elif confidence in (MatchConfidence.MEDIUM, MatchConfidence.LOW):
                # MEDIUM (80-95%) or LOW (50-80%): Agent workflow
                if self._enable_agent_workflow:
                    return self._handle_agent_workflow(
                        mention, best_candidate, trace_id, checkpoint_1_saved
                    )
                else:
                    # Fallback: create new concept if workflow disabled
                    logger.info(
                        f"[{trace_id}] Agent workflow disabled, creating new concept"
                    )
                    return self._handle_no_match(mention, trace_id, checkpoint_1_saved)

            else:
                # REJECTED (<50%): Create new concept
                return self._handle_no_match(mention, trace_id, checkpoint_1_saved)

        except Exception as e:
            logger.error(f"[{trace_id}] Failed to link mention: {e}", exc_info=True)
            return MentionIntegrationResult(
                mention_id=mention.id,
                trace_id=trace_id,
                checkpoint_saved=checkpoint_1_saved,
                error=f"Linking failed: {e}",
            )

    def _handle_high_confidence(
        self,
        mention: ProblemMention,
        candidate: MatchCandidate,
        trace_id: str,
        checkpoint_saved: bool,
    ) -> MentionIntegrationResult:
        """Handle HIGH confidence match via auto-linking."""
        concept = self._linker.auto_link_high_confidence(
            mention=mention,
            trace_id=trace_id,
        )

        if concept:
            logger.info(
                f"[{trace_id}] AUTO-LINKED: mention {mention.id} -> concept {concept.id}"
            )

            # Trigger concept refinement after linking
            concept_refined = self._maybe_refine_concept(concept.id, trace_id)

            return MentionIntegrationResult(
                mention_id=mention.id,
                concept_id=concept.id,
                is_new_concept=False,
                match_confidence="high",
                match_score=candidate.final_score,
                auto_linked=True,
                concept_refined=concept_refined,
                trace_id=trace_id,
                checkpoint_saved=True,
            )

        # Fallback to creating new concept if auto-link fails
        return self._handle_no_match(mention, trace_id, checkpoint_saved)

    def _handle_no_match(
        self,
        mention: ProblemMention,
        trace_id: str,
        checkpoint_saved: bool,
    ) -> MentionIntegrationResult:
        """Handle NO_MATCH case by creating new concept."""
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

    def _handle_agent_workflow(
        self,
        mention: ProblemMention,
        candidate: MatchCandidate,
        trace_id: str,
        checkpoint_saved: bool,
    ) -> MentionIntegrationResult:
        """Handle MEDIUM/LOW confidence via agent workflow."""
        import asyncio

        logger.info(
            f"[{trace_id}] AGENT WORKFLOW: confidence={candidate.confidence.value}, "
            f"score={candidate.final_score:.3f}"
        )

        # Create workflow state
        state = create_matching_state(
            mention_id=mention.id,
            mention_statement=mention.statement,
            mention_embedding=mention.embedding or [],
            candidate_concept_id=candidate.concept_id,
            candidate_statement=candidate.concept_statement,
            similarity_score=candidate.similarity_score,
            paper_doi=mention.paper_doi,
            mention_domain=mention.domain,
            trace_id=trace_id,
        )

        # Set confidence level for routing
        state["initial_confidence"] = candidate.confidence.value.lower()
        state["final_score"] = candidate.final_score
        state["candidate_domain"] = None  # Not available in MatchCandidate
        state["candidate_mention_count"] = 0  # Not available in MatchCandidate

        # Run workflow (sync wrapper for async)
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        try:
            result_state = loop.run_until_complete(
                process_medium_low_confidence(state)
            )
        except Exception as e:
            logger.error(f"[{trace_id}] Agent workflow failed: {e}", exc_info=True)
            # Fallback: create new concept on workflow failure
            return self._handle_no_match(mention, trace_id, checkpoint_saved)

        # Process workflow result
        final_decision = result_state.get("final_decision")
        final_concept_id = result_state.get("final_concept_id")

        if final_decision == "linked":
            # Link to existing concept
            return self._finalize_agent_link(
                mention, candidate, result_state, trace_id
            )

        elif final_decision == "created_new":
            # Create new concept
            concept = self._linker.create_new_concept(
                mention=mention,
                trace_id=trace_id,
            )

            logger.info(
                f"[{trace_id}] AGENT DECISION: created new concept {concept.id}"
            )

            return MentionIntegrationResult(
                mention_id=mention.id,
                concept_id=concept.id,
                is_new_concept=True,
                match_confidence=candidate.confidence.value.lower(),
                match_score=candidate.final_score,
                auto_linked=False,
                agent_workflow_used=True,
                workflow_decision="created_new",
                trace_id=trace_id,
                checkpoint_saved=True,
            )

        elif final_decision == "escalated":
            # Escalate to human review queue
            return self._escalate_to_human_review(
                mention, candidate, result_state, trace_id
            )

        else:
            # Unknown decision - fallback to create new
            logger.warning(
                f"[{trace_id}] Unknown workflow decision: {final_decision}, "
                f"creating new concept"
            )
            return self._handle_no_match(mention, trace_id, checkpoint_saved)

    def _finalize_agent_link(
        self,
        mention: ProblemMention,
        candidate: MatchCandidate,
        result_state: MatchingWorkflowState,
        trace_id: str,
    ) -> MentionIntegrationResult:
        """Finalize linking after agent workflow approves."""
        # Create INSTANCE_OF relationship using auto_linker internals
        try:
            concept = self._linker._create_instance_of_relationship(
                mention=mention,
                candidate=candidate,
                trace_id=trace_id,
            )

            logger.info(
                f"[{trace_id}] AGENT DECISION: linked mention {mention.id} -> concept {concept.id}"
            )

            # Trigger concept refinement after linking
            concept_refined = self._maybe_refine_concept(concept.id, trace_id)

            return MentionIntegrationResult(
                mention_id=mention.id,
                concept_id=concept.id,
                is_new_concept=False,
                match_confidence=candidate.confidence.value.lower(),
                match_score=candidate.final_score,
                auto_linked=False,
                agent_workflow_used=True,
                workflow_decision="linked",
                concept_refined=concept_refined,
                trace_id=trace_id,
                checkpoint_saved=True,
            )

        except Exception as e:
            logger.error(f"[{trace_id}] Failed to finalize link: {e}", exc_info=True)
            return MentionIntegrationResult(
                mention_id=mention.id,
                trace_id=trace_id,
                agent_workflow_used=True,
                workflow_decision="linked",
                error=f"Link finalization failed: {e}",
            )

    def _escalate_to_human_review(
        self,
        mention: ProblemMention,
        candidate: MatchCandidate,
        result_state: MatchingWorkflowState,
        trace_id: str,
    ) -> MentionIntegrationResult:
        """Escalate to human review queue."""
        logger.info(
            f"[{trace_id}] ESCALATING to human review queue "
            f"(reason={result_state.get('escalation_reason')})"
        )

        # Build suggested concepts list
        suggested_concepts = [
            SuggestedConceptForReview(
                concept_id=candidate.concept_id,
                canonical_statement=candidate.concept_statement,
                similarity_score=candidate.similarity_score,
                final_score=candidate.final_score,
                agent_reasoning=result_state.get("decision_reasoning", ""),
                domain=None,  # Not available in MatchCandidate
                mention_count=0,  # Not available in MatchCandidate
            )
        ]

        # Build agent context
        agent_context = AgentContextForReview(
            escalation_reason=result_state.get("escalation_reason"),
            evaluator_decision=result_state.get("evaluator_decision"),
            evaluator_confidence=result_state.get("evaluator_result", {}).get("confidence"),
            maker_arguments=[r.get("strongest_argument") for r in result_state.get("maker_results", [])],
            hater_arguments=[r.get("strongest_argument") for r in result_state.get("hater_results", [])],
            arbiter_decision=result_state.get("arbiter_results", [{}])[-1].get("decision") if result_state.get("arbiter_results") else None,
            rounds_attempted=result_state.get("current_round", 0),
            final_confidence=result_state.get("final_confidence", 0.0),
        )

        # Enqueue to review queue
        human_review_id = None
        if self._review_queue:
            try:
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                pending_review = loop.run_until_complete(
                    self._review_queue.enqueue(
                        mention=mention,
                        suggested_concepts=suggested_concepts,
                        workflow_state=result_state,
                        priority=None,  # Auto-calculated
                    )
                )
                human_review_id = pending_review.id
                logger.info(f"[{trace_id}] Created human review: {human_review_id}")

            except Exception as e:
                logger.error(f"[{trace_id}] Failed to enqueue review: {e}", exc_info=True)

        return MentionIntegrationResult(
            mention_id=mention.id,
            match_confidence=candidate.confidence.value.lower(),
            match_score=candidate.final_score,
            auto_linked=False,
            agent_workflow_used=True,
            workflow_decision="escalated",
            human_review_id=human_review_id,
            trace_id=trace_id,
            checkpoint_saved=True,
        )

    def _maybe_refine_concept(self, concept_id: str, trace_id: str) -> bool:
        """Trigger concept refinement if enabled and at threshold."""
        if not self._enable_concept_refinement:
            return False

        # Initialize refinement service if not provided
        if self._refinement is None:
            try:
                self._refinement = get_refinement_service(repository=self._repo)
            except Exception as e:
                logger.warning(f"[{trace_id}] Could not initialize refinement service: {e}")
                return False

        try:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            result = loop.run_until_complete(
                self._refinement.check_and_refine(concept_id, trace_id)
            )

            if result:
                logger.info(
                    f"[{trace_id}] Concept {concept_id} refined (version {result.version})"
                )
                return True

        except Exception as e:
            logger.warning(f"[{trace_id}] Concept refinement failed: {e}")

        return False

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
            Constraint(text=c.text, type=c.constraint_type, confidence=c.confidence)
            for c in extracted_problem.constraints
        ]
        datasets = [
            Dataset(name=d.name, url=d.url, available=d.available)
            for d in extracted_problem.datasets
        ]
        metrics = [
            Metric(name=m.name, description=m.description, baseline_value=m.baseline_value)
            for m in extracted_problem.metrics
        ]
        baselines = [
            Baseline(name=b.name, paper_doi=b.paper_reference, performance={})
            for b in extracted_problem.baselines
        ]

        extraction_metadata = ExtractionMetadata(
            extracted_at=datetime.now(timezone.utc),
            extractor_version="1.0.0",
            extraction_model="extraction_pipeline",
            confidence_score=extracted_problem.confidence,
            human_reviewed=False,
        )

        mention = ProblemMention(
            id=str(uuid.uuid4()),
            statement=extracted_problem.statement,
            paper_doi=paper_doi,
            section="Unknown",  # Default when not in ExtractedProblem schema
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
        Store ProblemMention node in Neo4j and link to source Paper.

        Creates the ProblemMention node and an EXTRACTED_FROM relationship
        to the Paper node identified by paper_doi.

        Args:
            mention: ProblemMention to store.

        Raises:
            Exception: If storage fails.
        """
        with self._repo.session() as session:
            query = """
            CREATE (m:ProblemMention)
            SET m = $properties
            WITH m
            OPTIONAL MATCH (p:Paper {doi: $paper_doi})
            FOREACH (_ IN CASE WHEN p IS NOT NULL THEN [1] ELSE [] END |
                CREATE (m)-[:EXTRACTED_FROM]->(p)
            )
            """
            props = mention.to_neo4j_properties()
            session.run(query, properties=props, paper_doi=mention.paper_doi)

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
