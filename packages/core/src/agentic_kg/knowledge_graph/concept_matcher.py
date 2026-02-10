"""
ConceptMatcher service for matching problem mentions to canonical concepts.

Provides embedding-based similarity search with multi-threshold confidence
classification and citation boost logic.
"""

import logging
from typing import Optional

from neo4j import ManagedTransaction

from agentic_kg.knowledge_graph.embeddings import EmbeddingService
from agentic_kg.knowledge_graph.models import (
    MatchCandidate,
    MatchConfidence,
    ProblemConcept,
    ProblemMention,
)
from agentic_kg.knowledge_graph.repository import Neo4jRepository, get_repository

logger = logging.getLogger(__name__)


class MatcherError(Exception):
    """Raised when concept matching fails."""

    pass


class ConceptMatcher:
    """
    Service for matching problem mentions to canonical problem concepts.

    Uses vector similarity search on Neo4j with confidence classification
    and citation boost logic to find the best matching concepts.
    """

    # Confidence thresholds
    HIGH_CONFIDENCE_THRESHOLD = 0.95  # >95% - auto-link
    MEDIUM_CONFIDENCE_THRESHOLD = 0.80  # 80-95% - agent review
    LOW_CONFIDENCE_THRESHOLD = 0.50  # 50-80% - multi-agent consensus
    # Below 50% = NO_MATCH

    # Citation boost settings
    MAX_CITATION_BOOST = 0.20  # Maximum 20% boost from citations

    def __init__(
        self,
        repository: Optional[Neo4jRepository] = None,
        embedding_service: Optional[EmbeddingService] = None,
    ):
        """
        Initialize concept matcher.

        Args:
            repository: Neo4j repository. Uses global repository if not provided.
            embedding_service: Embedding service. Creates new instance if not provided.
        """
        self._repo = repository or get_repository()
        self._embedder = embedding_service or EmbeddingService()

    def generate_embedding(self, text: str) -> list[float]:
        """
        Generate embedding for a text using OpenAI.

        Args:
            text: Text to embed (problem statement).

        Returns:
            1536-dimensional embedding vector.

        Raises:
            MatcherError: If embedding generation fails.
        """
        try:
            return self._embedder.generate_embedding(text)
        except Exception as e:
            raise MatcherError(f"Failed to generate embedding: {e}") from e

    def find_candidate_concepts(
        self,
        mention: ProblemMention,
        top_k: int = 10,
        include_citation_boost: bool = True,
    ) -> list[MatchCandidate]:
        """
        Find candidate concepts for a problem mention using vector similarity.

        Args:
            mention: Problem mention to match.
            top_k: Number of top candidates to return (default: 10).
            include_citation_boost: Whether to apply citation boost (default: True).

        Returns:
            List of MatchCandidate objects sorted by final score (descending).

        Raises:
            MatcherError: If search fails.
        """
        if not mention.embedding:
            raise MatcherError("ProblemMention must have embedding before matching")

        def _search(tx: ManagedTransaction) -> list[dict]:
            # Neo4j vector similarity query using VECTOR index
            query = """
            CALL db.index.vector.queryNodes(
                'concept_embedding_idx',
                $top_k,
                $embedding
            ) YIELD node, score
            RETURN
                node.id AS concept_id,
                node.canonical_statement AS statement,
                node.domain AS domain,
                node.mention_count AS mention_count,
                score AS similarity_score
            """
            result = tx.run(
                query,
                embedding=mention.embedding,
                top_k=top_k,
            )
            return [record.data() for record in result]

        try:
            with self._repo.session() as session:
                results = session.execute_read(_search)
        except Exception as e:
            raise MatcherError(f"Vector similarity search failed: {e}") from e

        # Convert to MatchCandidate objects with confidence classification
        candidates = []
        for result in results:
            similarity_score = result["similarity_score"]

            # Calculate citation boost if enabled
            citation_boost = 0.0
            if include_citation_boost:
                citation_boost = self._calculate_citation_boost(
                    mention, result["concept_id"]
                )

            # Classify confidence based on similarity score
            confidence = self.classify_confidence(similarity_score)

            # Check domain match
            domain_match = (
                mention.domain == result["domain"]
                if mention.domain and result["domain"]
                else False
            )

            candidate = MatchCandidate(
                concept_id=result["concept_id"],
                concept_statement=result["statement"],
                similarity_score=similarity_score,
                confidence=confidence,
                citation_boost=citation_boost,
                domain_match=domain_match,
                metadata_overlap={},  # TODO: Calculate in future iteration
            )

            candidates.append(candidate)

        # Sort by final score (similarity + citation boost)
        candidates.sort(key=lambda c: c.final_score, reverse=True)

        logger.info(
            f"Found {len(candidates)} candidates for mention {mention.id} "
            f"(top score: {candidates[0].final_score:.3f})"
            if candidates
            else f"No candidates found for mention {mention.id}"
        )

        return candidates

    def classify_confidence(self, similarity_score: float) -> MatchConfidence:
        """
        Classify confidence level based on similarity score.

        Thresholds:
        - HIGH: >95% similarity - auto-link
        - MEDIUM: 80-95% similarity - agent review
        - LOW: 50-80% similarity - multi-agent consensus
        - REJECTED: <50% similarity - no match

        Args:
            similarity_score: Cosine similarity score (0-1).

        Returns:
            MatchConfidence level.
        """
        if similarity_score >= self.HIGH_CONFIDENCE_THRESHOLD:
            return MatchConfidence.HIGH
        elif similarity_score >= self.MEDIUM_CONFIDENCE_THRESHOLD:
            return MatchConfidence.MEDIUM
        elif similarity_score >= self.LOW_CONFIDENCE_THRESHOLD:
            return MatchConfidence.LOW
        else:
            return MatchConfidence.REJECTED

    def _calculate_citation_boost(
        self, mention: ProblemMention, concept_id: str
    ) -> float:
        """
        Calculate citation boost if mention's paper cites any paper linked to concept.

        Citation boost provides up to 0.20 additional score if papers have
        citation relationships.

        Args:
            mention: Problem mention to check.
            concept_id: Candidate concept ID.

        Returns:
            Boost amount (0.0 to 0.20).
        """

        def _check_citations(tx: ManagedTransaction) -> bool:
            # Check if mention's paper cites any paper that has mentions of this concept
            query = """
            MATCH (mention_paper:Paper {doi: $mention_doi})
            MATCH (concept:ProblemConcept {id: $concept_id})
            MATCH (concept)<-[:INSTANCE_OF]-(other_mention:ProblemMention)
            MATCH (other_paper:Paper {doi: other_mention.paper_doi})
            MATCH (mention_paper)-[:CITES]->(other_paper)
            RETURN count(*) > 0 AS has_citations
            LIMIT 1
            """
            result = tx.run(
                query,
                mention_doi=mention.paper_doi,
                concept_id=concept_id,
            )
            record = result.single()
            return record["has_citations"] if record else False

        try:
            with self._repo.session() as session:
                has_citations = session.execute_read(_check_citations)
                if has_citations:
                    logger.debug(
                        f"Citation boost applied for mention {mention.id} -> concept {concept_id}"
                    )
                    return self.MAX_CITATION_BOOST
        except Exception as e:
            # Don't fail the entire match if citation check fails
            logger.warning(f"Citation boost check failed: {e}")

        return 0.0

    def match_mention_to_concept(
        self,
        mention: ProblemMention,
        auto_link_high_confidence: bool = False,
    ) -> Optional[MatchCandidate]:
        """
        Find the best matching concept for a mention.

        Args:
            mention: Problem mention to match.
            auto_link_high_confidence: If True, automatically link HIGH confidence matches.

        Returns:
            Best MatchCandidate, or None if no suitable match found.
        """
        candidates = self.find_candidate_concepts(mention, top_k=10)

        if not candidates:
            logger.info(f"No candidates found for mention {mention.id}")
            return None

        best_candidate = candidates[0]

        if best_candidate.confidence == MatchConfidence.REJECTED:
            logger.info(
                f"Best candidate rejected (score: {best_candidate.similarity_score:.3f}) "
                f"for mention {mention.id}"
            )
            return None

        # Log match decision
        logger.info(
            f"Matched mention {mention.id} to concept {best_candidate.concept_id} "
            f"(confidence: {best_candidate.confidence.value}, "
            f"score: {best_candidate.final_score:.3f})"
        )

        # Auto-link logic (if enabled)
        if (
            auto_link_high_confidence
            and best_candidate.confidence == MatchConfidence.HIGH
        ):
            logger.info(
                f"Auto-linking HIGH confidence match: "
                f"mention {mention.id} -> concept {best_candidate.concept_id}"
            )
            # TODO: Create INSTANCE_OF relationship in next task

        return best_candidate


def get_concept_matcher(
    repository: Optional[Neo4jRepository] = None,
    embedding_service: Optional[EmbeddingService] = None,
) -> ConceptMatcher:
    """
    Get a ConceptMatcher instance (convenience function).

    Args:
        repository: Neo4j repository. Uses global repository if not provided.
        embedding_service: Embedding service. Creates new instance if not provided.

    Returns:
        ConceptMatcher instance.
    """
    return ConceptMatcher(repository=repository, embedding_service=embedding_service)
