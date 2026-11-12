"""Backfill embeddings for problems that were written without one.

Semantic search (and therefore the default ``POST /api/search``) needs an
embedding on both the query and the stored node. Ingestion degrades gracefully
when ``OPENAI_API_KEY`` is absent (or a call fails), creating nodes **without**
an embedding. Once the key is configured, those nodes stay invisible to vector
search until their embeddings are generated. This module is that migration
path; ``agentic-kg backfill-embeddings`` is the CLI wrapper.

It is deliberately small and provider-agnostic: it reads nodes whose
``embedding`` is null, builds the same text the write path embeds, and stores
the vector. Failures are counted and reported; one bad node never aborts the
whole run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from neo4j import ManagedTransaction

from agentic_kg.knowledge_graph.embeddings import (
    EmbeddingService,
    get_embedding_service,
)
from agentic_kg.knowledge_graph.repository import Neo4jRepository, get_repository


@dataclass
class BackfillReport:
    """Outcome of one backfill run (all counts are honest, never estimated)."""

    concepts_seen: int = 0
    concepts_updated: int = 0
    problems_seen: int = 0
    problems_updated: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def updated(self) -> int:
        return self.concepts_updated + self.problems_updated

    @property
    def seen(self) -> int:
        return self.concepts_seen + self.problems_seen

    def to_dict(self) -> dict:
        return {
            "concepts_seen": self.concepts_seen,
            "concepts_updated": self.concepts_updated,
            "problems_seen": self.problems_seen,
            "problems_updated": self.problems_updated,
            "updated": self.updated,
            "failures": self.failures,
        }


def _missing_concepts(repository: Neo4jRepository, limit: int) -> list[dict]:
    def _run(tx: ManagedTransaction) -> list[dict]:
        return [
            dict(record["c"])
            for record in tx.run(
                """
                MATCH (c:ProblemConcept)
                WHERE c.embedding IS NULL
                RETURN c
                LIMIT $limit
                """,
                limit=limit,
            )
        ]

    with repository.session() as session:
        return session.execute_read(_run)


def _missing_problems(repository: Neo4jRepository, limit: int) -> list[dict]:
    def _run(tx: ManagedTransaction) -> list[dict]:
        return [
            dict(record["p"])
            for record in tx.run(
                """
                MATCH (p:Problem)
                WHERE p.embedding IS NULL
                RETURN p
                LIMIT $limit
                """,
                limit=limit,
            )
        ]

    with repository.session() as session:
        return session.execute_read(_run)


def _set_concept_embedding(
    repository: Neo4jRepository, concept_id: str, embedding: list[float]
) -> None:
    def _write(tx: ManagedTransaction) -> None:
        tx.run(
            "MATCH (c:ProblemConcept {id: $id}) SET c.embedding = $embedding",
            id=concept_id,
            embedding=embedding,
        )

    with repository.session() as session:
        session.execute_write(_write)


def _set_problem_embedding(
    repository: Neo4jRepository, problem_id: str, embedding: list[float]
) -> None:
    def _write(tx: ManagedTransaction) -> None:
        tx.run(
            "MATCH (p:Problem {id: $id}) SET p.embedding = $embedding",
            id=problem_id,
            embedding=embedding,
        )

    with repository.session() as session:
        session.execute_write(_write)


def backfill_problem_embeddings(
    repository: Optional[Neo4jRepository] = None,
    *,
    limit: int = 500,
    dry_run: bool = False,
    embedding_service: Optional[EmbeddingService] = None,
) -> BackfillReport:
    """Generate and store embeddings for concepts/problems that lack one.

    Args:
        repository: Neo4j repository (default: process repository).
        limit: Maximum nodes per label per run. Re-run until ``seen == 0``.
        dry_run: Report what would be updated without calling the provider.
        embedding_service: Injectable service (tests); defaults to the shared
            OpenAI embedding service.

    Returns:
        A :class:`BackfillReport`. Provider failures are recorded in
        ``failures`` and the corresponding node is left unchanged.
    """
    repo = repository or get_repository()
    service = embedding_service or get_embedding_service()
    report = BackfillReport()

    concepts = _missing_concepts(repo, limit)
    report.concepts_seen = len(concepts)
    for concept in concepts:
        text = (concept.get("canonical_statement") or "").strip()
        if not text:
            report.failures.append(
                f"ProblemConcept {concept.get('id')}: empty canonical_statement"
            )
            continue
        if dry_run:
            report.concepts_updated += 1
            continue
        try:
            embedding = service.generate_embedding(text)
        except Exception as exc:  # noqa: BLE001 - reported per node
            report.failures.append(
                f"ProblemConcept {concept.get('id')}: {type(exc).__name__}: {exc}"
            )
            continue
        _set_concept_embedding(repo, concept["id"], embedding)
        report.concepts_updated += 1

    problems = _missing_problems(repo, limit)
    report.problems_seen = len(problems)
    for problem in problems:
        text = (problem.get("statement") or "").strip()
        if not text:
            report.failures.append(f"Problem {problem.get('id')}: empty statement")
            continue
        if dry_run:
            report.problems_updated += 1
            continue
        try:
            embedding = service.generate_embedding(text)
        except Exception as exc:  # noqa: BLE001 - reported per node
            report.failures.append(
                f"Problem {problem.get('id')}: {type(exc).__name__}: {exc}"
            )
            continue
        _set_problem_embedding(repo, problem["id"], embedding)
        report.problems_updated += 1

    return report


__all__ = ["BackfillReport", "backfill_problem_embeddings"]
