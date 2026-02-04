"""
Knowledge Graph integration for extracted problems.

This module handles:
- Converting ExtractedProblem to Problem entities
- Checking for duplicates before insertion
- Creating EXTRACTED_FROM relations
- Storing problem-to-problem relations from extraction
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from agentic_kg.extraction.pipeline import PaperProcessingResult
from agentic_kg.extraction.relation_extractor import ExtractedRelation, RelationType
from agentic_kg.extraction.schemas import ExtractedProblem, extracted_to_kg_problem
from agentic_kg.knowledge_graph.models import Paper, Problem, RelationType as KGRelationType
from agentic_kg.knowledge_graph.relations import (
    RelationError,
    RelationService,
    get_relation_service,
)
from agentic_kg.knowledge_graph.repository import (
    DuplicateError,
    Neo4jRepository,
    NotFoundError,
    get_repository,
)

logger = logging.getLogger(__name__)


class StoredProblem(BaseModel):
    """Result of storing a problem in the Knowledge Graph."""

    problem_id: str = Field(..., description="ID of the stored problem")
    is_new: bool = Field(..., description="True if newly created, False if existing")
    is_duplicate: bool = Field(
        default=False, description="True if detected as duplicate"
    )
    duplicate_of: Optional[str] = Field(
        default=None, description="ID of existing duplicate problem"
    )
    extraction_linked: bool = Field(
        default=False, description="True if EXTRACTED_FROM relation created"
    )


class IntegrationResult(BaseModel):
    """Result of integrating extraction results into Knowledge Graph."""

    paper_doi: Optional[str] = Field(default=None, description="Paper DOI")
    paper_title: Optional[str] = Field(default=None, description="Paper title")

    # Problem storage
    problems_stored: list[StoredProblem] = Field(
        default_factory=list, description="Results for each stored problem"
    )
    problems_skipped: int = Field(
        default=0, description="Problems skipped (duplicates, low confidence)"
    )

    # Relation storage
    relations_created: int = Field(
        default=0, description="Problem-to-problem relations created"
    )
    relations_skipped: int = Field(
        default=0, description="Relations skipped (already exist, missing endpoints)"
    )

    # Errors
    errors: list[str] = Field(default_factory=list, description="Error messages")

    @property
    def total_new_problems(self) -> int:
        """Count of newly created problems."""
        return sum(1 for p in self.problems_stored if p.is_new)

    @property
    def success(self) -> bool:
        """True if no fatal errors occurred."""
        return len(self.errors) == 0


@dataclass
class IntegrationConfig:
    """Configuration for Knowledge Graph integration."""

    # Deduplication
    check_duplicates: bool = True
    similarity_threshold: float = 0.95  # For embedding-based dedup
    use_embedding_dedup: bool = False  # Whether to use embedding similarity

    # Minimum confidence to store
    min_confidence: float = 0.5

    # Create paper if not exists
    create_paper_if_missing: bool = True

    # Store relations
    store_relations: bool = True

    # Generate embeddings on insert
    generate_embeddings: bool = True


@dataclass
class KnowledgeGraphIntegrator:
    """
    Integrates extraction results into the Knowledge Graph.

    Handles conversion, deduplication, and storage of extracted
    problems and their relationships.
    """

    repository: Optional[Neo4jRepository] = field(default=None)
    relation_service: Optional[RelationService] = field(default=None)
    config: IntegrationConfig = field(default_factory=IntegrationConfig)

    def __post_init__(self):
        """Initialize services lazily."""
        if self.repository is None:
            self.repository = get_repository()
        if self.relation_service is None:
            self.relation_service = get_relation_service()

    def integrate_extraction_result(
        self,
        result: PaperProcessingResult,
    ) -> IntegrationResult:
        """
        Integrate a full paper extraction result into the Knowledge Graph.

        Args:
            result: Paper processing result containing extracted problems

        Returns:
            IntegrationResult with details of what was stored
        """
        integration = IntegrationResult(
            paper_doi=result.paper_doi,
            paper_title=result.paper_title,
        )

        if not result.success:
            integration.errors.append("Extraction result was not successful")
            return integration

        # Ensure paper exists in the graph
        paper_exists = True
        if result.paper_doi:
            paper_exists = self._ensure_paper_exists(
                doi=result.paper_doi,
                title=result.paper_title,
                authors=result.paper_authors,
                integration=integration,
            )

        # Store problems
        problem_id_map: dict[str, str] = {}  # Map extracted statement -> stored ID

        for extracted_problem in result.get_problems():
            if extracted_problem.confidence < self.config.min_confidence:
                integration.problems_skipped += 1
                continue

            # Get section from problem metadata if available
            section = getattr(extracted_problem, 'section', 'unknown')

            stored = self._store_problem(
                problem=extracted_problem,
                paper_doi=result.paper_doi if paper_exists else None,
                paper_title=result.paper_title,
                section=section,
            )

            integration.problems_stored.append(stored)
            problem_id_map[extracted_problem.statement[:100]] = stored.problem_id

        # Store relations if enabled and we have extracted relations
        if (
            self.config.store_relations
            and result.relation_result
            and len(problem_id_map) >= 2
        ):
            self._store_relations(
                relations=result.relation_result.relations,
                problem_id_map=problem_id_map,
                integration=integration,
            )

        return integration

    def store_single_problem(
        self,
        problem: ExtractedProblem,
        paper_doi: Optional[str] = None,
        paper_title: Optional[str] = None,
        section: str = "unknown",
    ) -> StoredProblem:
        """
        Store a single extracted problem.

        Args:
            problem: Extracted problem to store
            paper_doi: Optional paper DOI for linking
            paper_title: Optional paper title
            section: Section where problem was extracted

        Returns:
            StoredProblem with storage result
        """
        return self._store_problem(
            problem=problem,
            paper_doi=paper_doi,
            paper_title=paper_title,
            section=section,
        )

    def _store_problem(
        self,
        problem: ExtractedProblem,
        paper_doi: Optional[str],
        paper_title: Optional[str],
        section: str = "unknown",
    ) -> StoredProblem:
        """Internal method to store a problem."""
        # Check for duplicates
        if self.config.check_duplicates:
            duplicate_id = self._find_duplicate(problem)
            if duplicate_id:
                logger.info(
                    f"Skipping duplicate problem, matches {duplicate_id}"
                )
                return StoredProblem(
                    problem_id=duplicate_id,
                    is_new=False,
                    is_duplicate=True,
                    duplicate_of=duplicate_id,
                )

        # Convert to Knowledge Graph Problem
        kg_problem = extracted_to_kg_problem(
            extracted=problem,
            paper_doi=paper_doi,
            paper_title=paper_title,
            section=section,
        )

        # Store in repository
        try:
            self.repository.create_problem(
                kg_problem,
                generate_embedding=self.config.generate_embeddings,
            )
            logger.info(f"Stored problem: {kg_problem.id}")

            # Create EXTRACTED_FROM relation if we have a paper
            extraction_linked = False
            if paper_doi:
                try:
                    # Use the section we were given
                    self.relation_service.link_problem_to_paper(
                        problem_id=kg_problem.id,
                        paper_doi=paper_doi,
                        section=section,
                    )
                    extraction_linked = True
                except NotFoundError as e:
                    logger.warning(f"Could not link to paper: {e}")
                except Exception as e:
                    logger.error(f"Error linking problem to paper: {e}")

            return StoredProblem(
                problem_id=kg_problem.id,
                is_new=True,
                is_duplicate=False,
                extraction_linked=extraction_linked,
            )

        except DuplicateError:
            # Problem with same ID already exists
            logger.warning(f"Problem {kg_problem.id} already exists")
            return StoredProblem(
                problem_id=kg_problem.id,
                is_new=False,
                is_duplicate=True,
                duplicate_of=kg_problem.id,
            )

    def _find_duplicate(self, problem: ExtractedProblem) -> Optional[str]:
        """
        Check if a similar problem already exists.

        Args:
            problem: Problem to check

        Returns:
            ID of duplicate problem if found, None otherwise
        """
        # Strategy 1: Check for exact quoted_text match from same paper
        # This would require storing and querying quoted_text, which we could add

        # Strategy 2: Check for embedding similarity
        if self.config.use_embedding_dedup:
            # This would use vector search - placeholder for now
            pass

        # For now, just check for very similar statements using fuzzy matching
        # In production, use embeddings for semantic deduplication
        return None

    def _ensure_paper_exists(
        self,
        doi: str,
        title: Optional[str],
        authors: list[str],
        integration: IntegrationResult,
    ) -> bool:
        """
        Ensure the paper exists in the Knowledge Graph.

        Args:
            doi: Paper DOI
            title: Paper title
            authors: Author names
            integration: Result object for error tracking

        Returns:
            True if paper exists or was created
        """
        try:
            self.repository.get_paper(doi)
            return True
        except NotFoundError:
            if not self.config.create_paper_if_missing:
                integration.errors.append(f"Paper {doi} not found in Knowledge Graph")
                return False

            # Create minimal paper record
            try:
                paper = Paper(
                    doi=doi,
                    title=title or "Unknown",
                    authors=authors,
                    year=datetime.now(timezone.utc).year,  # Default to current year
                )
                self.repository.create_paper(paper)
                logger.info(f"Created paper record: {doi}")
                return True
            except Exception as e:
                integration.errors.append(f"Failed to create paper {doi}: {e}")
                return False

    def _store_relations(
        self,
        relations: list[ExtractedRelation],
        problem_id_map: dict[str, str],
        integration: IntegrationResult,
    ) -> None:
        """
        Store extracted problem-to-problem relations.

        Args:
            relations: Extracted relations
            problem_id_map: Map from extracted statement to stored problem ID
            integration: Result object to update
        """
        for relation in relations:
            try:
                # Find source and target problem IDs
                source_id = self._find_problem_id(
                    relation.source_problem_id, problem_id_map
                )
                target_id = self._find_problem_id(
                    relation.target_problem_id, problem_id_map
                )

                if not source_id or not target_id:
                    integration.relations_skipped += 1
                    continue

                # Map extraction relation type to KG relation type
                kg_relation_type = self._map_relation_type(relation.relation_type)
                if not kg_relation_type:
                    integration.relations_skipped += 1
                    continue

                # Create the relation
                self.relation_service.create_relation(
                    from_problem_id=source_id,
                    to_problem_id=target_id,
                    relation_type=kg_relation_type,
                    confidence=relation.confidence,
                    metadata={
                        "evidence": relation.evidence,
                        "extraction_method": relation.extraction_method,
                    },
                )
                integration.relations_created += 1

            except RelationError:
                # Relation already exists
                integration.relations_skipped += 1
            except NotFoundError:
                integration.relations_skipped += 1
            except Exception as e:
                logger.error(f"Error creating relation: {e}")
                integration.relations_skipped += 1

    def _find_problem_id(
        self,
        statement: str,
        problem_id_map: dict[str, str],
    ) -> Optional[str]:
        """Find problem ID from statement or partial match."""
        # Direct match
        if statement in problem_id_map:
            return problem_id_map[statement]

        # Prefix match (for truncated statements)
        for key, pid in problem_id_map.items():
            if key.startswith(statement[:50]) or statement.startswith(key[:50]):
                return pid

        return None

    def _map_relation_type(
        self,
        extraction_type: RelationType,
    ) -> Optional[KGRelationType]:
        """Map extraction relation type to KG relation type."""
        mapping = {
            RelationType.EXTENDS: KGRelationType.EXTENDS,
            RelationType.CONTRADICTS: KGRelationType.CONTRADICTS,
            RelationType.DEPENDS_ON: KGRelationType.DEPENDS_ON,
            RelationType.REFRAMES: KGRelationType.REFRAMES,
        }
        return mapping.get(extraction_type)


# Module-level singleton
_integrator: Optional[KnowledgeGraphIntegrator] = None


def get_kg_integrator(
    config: Optional[IntegrationConfig] = None,
) -> KnowledgeGraphIntegrator:
    """
    Get the singleton KnowledgeGraphIntegrator instance.

    Args:
        config: Optional configuration

    Returns:
        KnowledgeGraphIntegrator instance
    """
    global _integrator

    if _integrator is None:
        _integrator = KnowledgeGraphIntegrator(
            config=config or IntegrationConfig(),
        )

    return _integrator


def reset_kg_integrator() -> None:
    """Reset the singleton (useful for testing)."""
    global _integrator
    _integrator = None
