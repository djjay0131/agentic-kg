"""
Hybrid search implementation combining semantic and structured queries.

Provides:
- Semantic search using vector embeddings
- Structured search using Cypher filters
- Hybrid search combining both approaches
- Similarity detection for deduplication
"""

import logging
from dataclasses import dataclass
from typing import Any, Optional

from neo4j import ManagedTransaction

from agentic_kg.config import SearchConfig, get_config
from agentic_kg.knowledge_graph.embeddings import (
    EmbeddingService,
    get_embedding_service,
)
from agentic_kg.knowledge_graph.models import Problem, ProblemStatus
from agentic_kg.knowledge_graph.repository import (
    Neo4jRepository,
    get_repository,
)

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """A single search result with relevance score."""

    problem: Problem
    score: float
    match_type: str  # "semantic", "structured", or "hybrid"

    def __lt__(self, other: "SearchResult") -> bool:
        """Sort by score descending."""
        return self.score > other.score


class SearchService:
    """
    Service for searching problems in the knowledge graph.

    Combines vector similarity search with graph-based filtering.
    """

    def __init__(
        self,
        repository: Optional[Neo4jRepository] = None,
        embedding_service: Optional[EmbeddingService] = None,
        config: Optional[SearchConfig] = None,
    ):
        """
        Initialize search service.

        Args:
            repository: Neo4j repository.
            embedding_service: Embedding service.
            config: Search configuration.
        """
        self._repo = repository or get_repository()
        self._embeddings = embedding_service or get_embedding_service()
        self._config = config or get_config().search

    def semantic_search(
        self,
        query: str,
        top_k: Optional[int] = None,
        min_score: Optional[float] = None,
    ) -> list[SearchResult]:
        """
        Search problems by semantic similarity.

        Args:
            query: Search query text.
            top_k: Maximum results (default from config).
            min_score: Minimum similarity score (default from config).

        Returns:
            List of search results sorted by relevance.
        """
        top_k = top_k or self._config.default_top_k
        min_score = min_score or self._config.similarity_threshold

        # Generate query embedding
        try:
            query_embedding = self._embeddings.generate_embedding(query)
        except Exception as e:
            logger.error(f"Failed to generate query embedding: {e}")
            return []

        def _search(tx: ManagedTransaction, emb: list[float], k: int) -> list[dict]:
            # Neo4j vector search
            result = tx.run(
                """
                CALL db.index.vector.queryNodes(
                    'problem_embedding_idx',
                    $k,
                    $embedding
                ) YIELD node, score
                WHERE score >= $min_score
                RETURN node, score
                """,
                embedding=emb,
                k=k,
                min_score=min_score,
            )
            return [
                {"node": dict(record["node"]), "score": record["score"]}
                for record in result
            ]

        with self._repo.session() as session:
            records = session.execute_read(
                lambda tx: _search(tx, query_embedding, top_k)
            )

        results = []
        for record in records:
            problem = self._problem_from_neo4j(record["node"])
            results.append(
                SearchResult(
                    problem=problem,
                    score=record["score"],
                    match_type="semantic",
                )
            )

        logger.info(f"Semantic search for '{query[:50]}...' found {len(results)} results")
        return sorted(results)

    def structured_search(
        self,
        domain: Optional[str] = None,
        status: Optional[ProblemStatus] = None,
        has_datasets: Optional[bool] = None,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
        top_k: Optional[int] = None,
    ) -> list[SearchResult]:
        """
        Search problems using structured filters.

        Args:
            domain: Filter by domain.
            status: Filter by status.
            has_datasets: Filter by dataset availability.
            year_from: Minimum year (via source paper).
            year_to: Maximum year (via source paper).
            top_k: Maximum results.

        Returns:
            List of search results.
        """
        top_k = top_k or self._config.default_top_k

        def _search(
            tx: ManagedTransaction,
            filters: dict[str, Any],
            limit: int,
        ) -> list[dict]:
            query = "MATCH (p:Problem)"
            conditions = []
            params: dict[str, Any] = {"limit": limit}

            if filters.get("domain"):
                conditions.append("p.domain = $domain")
                params["domain"] = filters["domain"]

            if filters.get("status"):
                conditions.append("p.status = $status")
                params["status"] = filters["status"]

            if filters.get("has_datasets") is not None:
                if filters["has_datasets"]:
                    conditions.append("size(p.datasets) > 0")
                else:
                    conditions.append("size(p.datasets) = 0")

            # Year filtering requires joining with source paper
            if filters.get("year_from") or filters.get("year_to"):
                query = """
                    MATCH (p:Problem)-[:EXTRACTED_FROM]->(paper:Paper)
                """
                if filters.get("year_from"):
                    conditions.append("paper.year >= $year_from")
                    params["year_from"] = filters["year_from"]
                if filters.get("year_to"):
                    conditions.append("paper.year <= $year_to")
                    params["year_to"] = filters["year_to"]

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            query += " RETURN p ORDER BY p.created_at DESC LIMIT $limit"

            result = tx.run(query, **params)
            return [{"node": dict(record["p"]), "score": 1.0} for record in result]

        filters = {
            "domain": domain,
            "status": status.value if status else None,
            "has_datasets": has_datasets,
            "year_from": year_from,
            "year_to": year_to,
        }

        with self._repo.session() as session:
            records = session.execute_read(
                lambda tx: _search(tx, filters, top_k)
            )

        results = []
        for record in records:
            problem = self._problem_from_neo4j(record["node"])
            results.append(
                SearchResult(
                    problem=problem,
                    score=record["score"],
                    match_type="structured",
                )
            )

        logger.info(f"Structured search found {len(results)} results")
        return results

    def hybrid_search(
        self,
        query: str,
        domain: Optional[str] = None,
        status: Optional[ProblemStatus] = None,
        top_k: Optional[int] = None,
        semantic_weight: Optional[float] = None,
    ) -> list[SearchResult]:
        """
        Combined semantic and structured search.

        Uses semantic similarity for ranking and structured filters
        for filtering. Final score combines both signals.

        Args:
            query: Search query text.
            domain: Filter by domain.
            status: Filter by status.
            top_k: Maximum results.
            semantic_weight: Weight for semantic score (0-1).

        Returns:
            List of search results sorted by combined score.
        """
        top_k = top_k or self._config.default_top_k
        semantic_weight = semantic_weight or self._config.semantic_weight
        structured_weight = 1.0 - semantic_weight

        # Get semantic results (more than top_k for filtering)
        semantic_results = self.semantic_search(
            query, top_k=top_k * 3
        )

        # Apply structured filters
        filtered_results = []
        for result in semantic_results:
            if domain and result.problem.domain != domain:
                continue
            if status and result.problem.status != status:
                continue
            filtered_results.append(result)

        # Calculate hybrid scores
        for result in filtered_results:
            # Semantic score is already in result.score
            # Add structural match bonus
            structural_bonus = 0.0
            if domain and result.problem.domain == domain:
                structural_bonus += 0.5
            if status and result.problem.status == status:
                structural_bonus += 0.5

            # Normalize structural bonus
            structural_score = structural_bonus / 1.0 if domain or status else 1.0

            # Combined score
            result.score = (
                semantic_weight * result.score
                + structured_weight * structural_score
            )
            result.match_type = "hybrid"

        # Sort and limit
        filtered_results.sort()
        return filtered_results[:top_k]

    def find_similar_problems(
        self,
        problem: Problem,
        threshold: Optional[float] = None,
        exclude_self: bool = True,
    ) -> list[SearchResult]:
        """
        Find problems similar to a given problem.

        Used for deduplication detection.

        Args:
            problem: Problem to find similar problems for.
            threshold: Minimum similarity (default: deduplication threshold).
            exclude_self: Exclude the input problem from results.

        Returns:
            List of similar problems.
        """
        threshold = threshold or self._config.deduplication_threshold

        # Use problem statement as search query
        results = self.semantic_search(
            query=problem.statement,
            top_k=10,
            min_score=threshold,
        )

        if exclude_self:
            results = [r for r in results if r.problem.id != problem.id]

        logger.info(
            f"Found {len(results)} similar problems (threshold={threshold})"
        )
        return results

    def _problem_from_neo4j(self, data: dict) -> Problem:
        """Convert Neo4j node data to Problem model."""
        import json
        from datetime import datetime

        # Parse JSON strings
        for field in [
            "assumptions",
            "constraints",
            "datasets",
            "metrics",
            "baselines",
            "evidence",
            "extraction_metadata",
        ]:
            if field in data and isinstance(data[field], str):
                data[field] = json.loads(data[field])

        # Parse datetimes
        for field in ["created_at", "updated_at"]:
            if field in data and isinstance(data[field], str):
                data[field] = datetime.fromisoformat(data[field])

        # Parse nested datetime
        if "extraction_metadata" in data:
            meta = data["extraction_metadata"]
            if "extracted_at" in meta and isinstance(meta["extracted_at"], str):
                meta["extracted_at"] = datetime.fromisoformat(meta["extracted_at"])
            if meta.get("reviewed_at") and isinstance(meta["reviewed_at"], str):
                meta["reviewed_at"] = datetime.fromisoformat(meta["reviewed_at"])

        return Problem(**data)


# Singleton service
_search_service: Optional[SearchService] = None


def get_search_service() -> SearchService:
    """Get the search service singleton."""
    global _search_service
    if _search_service is None:
        _search_service = SearchService()
    return _search_service


def reset_search_service() -> None:
    """Reset the search service singleton."""
    global _search_service
    _search_service = None


# Convenience functions
def semantic_search(query: str, top_k: int = 10) -> list[SearchResult]:
    """Search problems by semantic similarity."""
    return get_search_service().semantic_search(query, top_k=top_k)


def hybrid_search(
    query: str,
    domain: Optional[str] = None,
    status: Optional[ProblemStatus] = None,
    top_k: int = 10,
) -> list[SearchResult]:
    """Combined semantic and structured search."""
    return get_search_service().hybrid_search(
        query, domain=domain, status=status, top_k=top_k
    )


def find_similar_problems(
    problem: Problem,
    threshold: float = 0.95,
) -> list[SearchResult]:
    """Find similar problems for deduplication."""
    return get_search_service().find_similar_problems(
        problem, threshold=threshold
    )
