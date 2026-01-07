"""
Relation operations for the Knowledge Graph.

Manages relationships between entities:
- Problem-to-Problem relations (extends, contradicts, depends_on, reframes)
- Problem-to-Paper relations (extracted_from)
- Paper-to-Author relations (authored_by)
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from neo4j import ManagedTransaction

from agentic_kg.knowledge_graph.models import (
    AuthoredByRelation,
    ContradictionType,
    ContradictsRelation,
    DependencyType,
    DependsOnRelation,
    ExtendsRelation,
    ExtractedFromRelation,
    Problem,
    ProblemRelation,
    ReframesRelation,
    RelationType,
)
from agentic_kg.knowledge_graph.repository import (
    Neo4jRepository,
    NotFoundError,
    RepositoryError,
    get_repository,
)

logger = logging.getLogger(__name__)


class RelationError(RepositoryError):
    """Raised when relation operation fails."""

    pass


class RelationService:
    """
    Service for managing relations in the knowledge graph.

    Handles creation, querying, and traversal of relationships
    between problems, papers, and authors.
    """

    def __init__(self, repository: Optional[Neo4jRepository] = None):
        """
        Initialize relation service.

        Args:
            repository: Neo4j repository.
        """
        self._repo = repository or get_repository()

    # =========================================================================
    # Problem-to-Problem Relations
    # =========================================================================

    def create_relation(
        self,
        from_problem_id: str,
        to_problem_id: str,
        relation_type: RelationType,
        confidence: float = 0.8,
        evidence_doi: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> ProblemRelation:
        """
        Create a relation between two problems.

        Args:
            from_problem_id: Source problem ID.
            to_problem_id: Target problem ID.
            relation_type: Type of relation.
            confidence: Confidence score (0-1).
            evidence_doi: DOI of supporting paper.
            metadata: Additional relation metadata.

        Returns:
            Created relation.

        Raises:
            NotFoundError: If either problem doesn't exist.
            RelationError: If relation already exists.
        """
        def _create(
            tx: ManagedTransaction,
            from_id: str,
            to_id: str,
            rel_type: str,
            props: dict,
        ) -> bool:
            # Verify both problems exist
            check = tx.run(
                """
                MATCH (from:Problem {id: $from_id})
                MATCH (to:Problem {id: $to_id})
                RETURN from.id, to.id
                """,
                from_id=from_id,
                to_id=to_id,
            )
            if not check.single():
                return False

            # Check if relation already exists
            existing = tx.run(
                f"""
                MATCH (from:Problem {{id: $from_id}})-[r:{rel_type}]->(to:Problem {{id: $to_id}})
                RETURN r
                """,
                from_id=from_id,
                to_id=to_id,
            )
            if existing.single():
                raise RelationError(
                    f"Relation {rel_type} already exists between {from_id} and {to_id}"
                )

            # Create relation
            tx.run(
                f"""
                MATCH (from:Problem {{id: $from_id}})
                MATCH (to:Problem {{id: $to_id}})
                CREATE (from)-[r:{rel_type}]->(to)
                SET r = $props
                """,
                from_id=from_id,
                to_id=to_id,
                props=props,
            )
            return True

        props = {
            "confidence": confidence,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if evidence_doi:
            props["evidence_doi"] = evidence_doi
        if metadata:
            props.update(metadata)

        with self._repo.session() as session:
            result = session.execute_write(
                lambda tx: _create(
                    tx, from_problem_id, to_problem_id, relation_type.value, props
                )
            )

        if not result:
            raise NotFoundError("One or both problems not found")

        logger.info(
            f"Created {relation_type.value} relation: {from_problem_id} -> {to_problem_id}"
        )

        return ProblemRelation(
            from_problem_id=from_problem_id,
            to_problem_id=to_problem_id,
            relation_type=relation_type,
            confidence=confidence,
            evidence_doi=evidence_doi,
        )

    def create_extends_relation(
        self,
        from_problem_id: str,
        to_problem_id: str,
        confidence: float = 0.8,
        inferred_by: Optional[str] = None,
    ) -> ExtendsRelation:
        """Create an EXTENDS relation (B extends/builds on A)."""
        metadata = {"inferred_by": inferred_by} if inferred_by else None
        self.create_relation(
            from_problem_id,
            to_problem_id,
            RelationType.EXTENDS,
            confidence,
            metadata=metadata,
        )
        return ExtendsRelation(
            from_problem_id=from_problem_id,
            to_problem_id=to_problem_id,
            confidence=confidence,
            inferred_by=inferred_by,
        )

    def create_contradicts_relation(
        self,
        from_problem_id: str,
        to_problem_id: str,
        contradiction_type: ContradictionType,
        confidence: float = 0.8,
        evidence_doi: Optional[str] = None,
    ) -> ContradictsRelation:
        """Create a CONTRADICTS relation (B contradicts A)."""
        self.create_relation(
            from_problem_id,
            to_problem_id,
            RelationType.CONTRADICTS,
            confidence,
            evidence_doi,
            metadata={"contradiction_type": contradiction_type.value},
        )
        return ContradictsRelation(
            from_problem_id=from_problem_id,
            to_problem_id=to_problem_id,
            contradiction_type=contradiction_type,
            confidence=confidence,
            evidence_doi=evidence_doi,
        )

    def create_depends_on_relation(
        self,
        from_problem_id: str,
        to_problem_id: str,
        dependency_type: DependencyType,
        confidence: float = 0.8,
    ) -> DependsOnRelation:
        """Create a DEPENDS_ON relation (B depends on A)."""
        self.create_relation(
            from_problem_id,
            to_problem_id,
            RelationType.DEPENDS_ON,
            confidence,
            metadata={"dependency_type": dependency_type.value},
        )
        return DependsOnRelation(
            from_problem_id=from_problem_id,
            to_problem_id=to_problem_id,
            dependency_type=dependency_type,
            confidence=confidence,
        )

    def create_reframes_relation(
        self,
        from_problem_id: str,
        to_problem_id: str,
        confidence: float = 0.8,
    ) -> ReframesRelation:
        """Create a REFRAMES relation (B reframes A)."""
        self.create_relation(
            from_problem_id,
            to_problem_id,
            RelationType.REFRAMES,
            confidence,
        )
        return ReframesRelation(
            from_problem_id=from_problem_id,
            to_problem_id=to_problem_id,
            confidence=confidence,
        )

    def get_related_problems(
        self,
        problem_id: str,
        relation_type: Optional[RelationType] = None,
        direction: str = "both",
    ) -> list[tuple[Problem, ProblemRelation]]:
        """
        Get problems related to a given problem.

        Args:
            problem_id: Problem ID.
            relation_type: Filter by relation type (None for all).
            direction: "outgoing", "incoming", or "both".

        Returns:
            List of (problem, relation) tuples.
        """
        def _get(
            tx: ManagedTransaction,
            pid: str,
            rel_type: Optional[str],
            dir: str,
        ) -> list[dict]:
            rel_pattern = f":{rel_type}" if rel_type else ""

            if dir == "outgoing":
                query = f"""
                    MATCH (p:Problem {{id: $id}})-[r{rel_pattern}]->(related:Problem)
                    RETURN related, r, type(r) as rel_type, 'outgoing' as direction
                """
            elif dir == "incoming":
                query = f"""
                    MATCH (p:Problem {{id: $id}})<-[r{rel_pattern}]-(related:Problem)
                    RETURN related, r, type(r) as rel_type, 'incoming' as direction
                """
            else:
                query = f"""
                    MATCH (p:Problem {{id: $id}})-[r{rel_pattern}]-(related:Problem)
                    RETURN related, r, type(r) as rel_type,
                        CASE WHEN startNode(r).id = $id
                            THEN 'outgoing' ELSE 'incoming' END as direction
                """

            result = tx.run(query, id=pid)
            return [
                {
                    "problem": dict(record["related"]),
                    "relation": dict(record["r"]),
                    "rel_type": record["rel_type"],
                    "direction": record["direction"],
                }
                for record in result
            ]

        rel_type_str = relation_type.value if relation_type else None

        with self._repo.session() as session:
            records = session.execute_read(
                lambda tx: _get(tx, problem_id, rel_type_str, direction)
            )

        results = []
        for record in records:
            problem = self._problem_from_neo4j(record["problem"])
            relation = ProblemRelation(
                from_problem_id=(
                    problem_id
                    if record["direction"] == "outgoing"
                    else problem.id
                ),
                to_problem_id=(
                    problem.id
                    if record["direction"] == "outgoing"
                    else problem_id
                ),
                relation_type=RelationType(record["rel_type"]),
                confidence=record["relation"].get("confidence", 0.8),
                evidence_doi=record["relation"].get("evidence_doi"),
            )
            results.append((problem, relation))

        return results

    # =========================================================================
    # Problem-to-Paper Relations
    # =========================================================================

    def link_problem_to_paper(
        self,
        problem_id: str,
        paper_doi: str,
        section: str,
    ) -> ExtractedFromRelation:
        """
        Create EXTRACTED_FROM relation between problem and paper.

        Args:
            problem_id: Problem ID.
            paper_doi: Paper DOI.
            section: Section where problem was extracted.

        Returns:
            Created relation.
        """
        def _link(
            tx: ManagedTransaction,
            pid: str,
            doi: str,
            sec: str,
        ) -> bool:
            result = tx.run(
                """
                MATCH (p:Problem {id: $id})
                MATCH (paper:Paper {doi: $doi})
                MERGE (p)-[r:EXTRACTED_FROM]->(paper)
                SET r.section = $section,
                    r.extraction_date = $date
                RETURN r
                """,
                id=pid,
                doi=doi,
                section=sec,
                date=datetime.now(timezone.utc).isoformat(),
            )
            return result.single() is not None

        with self._repo.session() as session:
            result = session.execute_write(
                lambda tx: _link(tx, problem_id, paper_doi, section)
            )

        if not result:
            raise NotFoundError("Problem or paper not found")

        logger.info(f"Linked problem {problem_id} to paper {paper_doi}")

        return ExtractedFromRelation(
            problem_id=problem_id,
            paper_doi=paper_doi,
            section=section,
        )

    def get_source_paper(self, problem_id: str) -> Optional[dict]:
        """Get the source paper for a problem."""
        def _get(tx: ManagedTransaction, pid: str) -> Optional[dict]:
            result = tx.run(
                """
                MATCH (p:Problem {id: $id})-[:EXTRACTED_FROM]->(paper:Paper)
                RETURN paper
                """,
                id=pid,
            )
            record = result.single()
            return dict(record["paper"]) if record else None

        with self._repo.session() as session:
            return session.execute_read(lambda tx: _get(tx, problem_id))

    # =========================================================================
    # Paper-to-Author Relations
    # =========================================================================

    def link_paper_to_author(
        self,
        paper_doi: str,
        author_id: str,
        author_position: int,
    ) -> AuthoredByRelation:
        """
        Create AUTHORED_BY relation between paper and author.

        Args:
            paper_doi: Paper DOI.
            author_id: Author ID.
            author_position: Author position (1 = first author).

        Returns:
            Created relation.
        """
        def _link(
            tx: ManagedTransaction,
            doi: str,
            aid: str,
            pos: int,
        ) -> bool:
            result = tx.run(
                """
                MATCH (paper:Paper {doi: $doi})
                MATCH (a:Author {id: $id})
                MERGE (paper)-[r:AUTHORED_BY]->(a)
                SET r.author_position = $position
                RETURN r
                """,
                doi=doi,
                id=aid,
                position=pos,
            )
            return result.single() is not None

        with self._repo.session() as session:
            result = session.execute_write(
                lambda tx: _link(tx, paper_doi, author_id, author_position)
            )

        if not result:
            raise NotFoundError("Paper or author not found")

        logger.info(
            f"Linked paper {paper_doi} to author {author_id} (position {author_position})"
        )

        return AuthoredByRelation(
            paper_doi=paper_doi,
            author_id=author_id,
            author_position=author_position,
        )

    def get_paper_authors(self, paper_doi: str) -> list[dict]:
        """Get all authors of a paper."""
        def _get(tx: ManagedTransaction, doi: str) -> list[dict]:
            result = tx.run(
                """
                MATCH (paper:Paper {doi: $doi})-[r:AUTHORED_BY]->(a:Author)
                RETURN a, r.author_position as position
                ORDER BY r.author_position
                """,
                doi=doi,
            )
            return [
                {"author": dict(record["a"]), "position": record["position"]}
                for record in result
            ]

        with self._repo.session() as session:
            return session.execute_read(lambda tx: _get(tx, paper_doi))

    # =========================================================================
    # Relation Inference (Placeholder)
    # =========================================================================

    def infer_relations(
        self,
        problem_id: str,
        model: str = "gpt-4",
    ) -> list[ProblemRelation]:
        """
        Infer potential relations for a problem.

        This is a placeholder for future LLM-based relation inference.

        Args:
            problem_id: Problem ID.
            model: Model to use for inference.

        Returns:
            List of inferred relations (empty for now).
        """
        logger.info(f"Relation inference not yet implemented for {problem_id}")
        return []

    def _problem_from_neo4j(self, data: dict) -> Problem:
        """Convert Neo4j node data to Problem model."""
        import json
        from datetime import datetime

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

        for field in ["created_at", "updated_at"]:
            if field in data and isinstance(data[field], str):
                data[field] = datetime.fromisoformat(data[field])

        if "extraction_metadata" in data:
            meta = data["extraction_metadata"]
            if "extracted_at" in meta and isinstance(meta["extracted_at"], str):
                meta["extracted_at"] = datetime.fromisoformat(meta["extracted_at"])
            if meta.get("reviewed_at") and isinstance(meta["reviewed_at"], str):
                meta["reviewed_at"] = datetime.fromisoformat(meta["reviewed_at"])

        return Problem(**data)


# Singleton service
_relation_service: Optional[RelationService] = None


def get_relation_service() -> RelationService:
    """Get the relation service singleton."""
    global _relation_service
    if _relation_service is None:
        _relation_service = RelationService()
    return _relation_service


def reset_relation_service() -> None:
    """Reset the relation service singleton."""
    global _relation_service
    _relation_service = None
