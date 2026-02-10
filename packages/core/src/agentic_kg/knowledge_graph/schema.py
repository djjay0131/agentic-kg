"""
Neo4j schema initialization and management.

Handles database schema setup including:
- Constraints (unique IDs, DOIs)
- Indexes (status, domain, year)
- Vector indexes for semantic search
- Schema versioning and migrations
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from neo4j import ManagedTransaction

from agentic_kg.knowledge_graph.repository import Neo4jRepository, get_repository

logger = logging.getLogger(__name__)

# Current schema version - increment when making schema changes
SCHEMA_VERSION = 2  # Added ProblemMention/ProblemConcept schema

# Schema definitions - fmt: off to allow long Cypher strings
CONSTRAINTS = [
    # Problem constraints
    (
        "problem_id_unique",
        "CREATE CONSTRAINT problem_id_unique IF NOT EXISTS "
        "FOR (p:Problem) REQUIRE p.id IS UNIQUE",
    ),
    # Paper constraints
    (
        "paper_doi_unique",
        "CREATE CONSTRAINT paper_doi_unique IF NOT EXISTS "
        "FOR (p:Paper) REQUIRE p.doi IS UNIQUE",
    ),
    # Author constraints
    (
        "author_id_unique",
        "CREATE CONSTRAINT author_id_unique IF NOT EXISTS "
        "FOR (a:Author) REQUIRE a.id IS UNIQUE",
    ),
    # ProblemMention constraints (canonical architecture)
    (
        "problem_mention_id_unique",
        "CREATE CONSTRAINT problem_mention_id_unique IF NOT EXISTS "
        "FOR (m:ProblemMention) REQUIRE m.id IS UNIQUE",
    ),
    # ProblemConcept constraints (canonical architecture)
    (
        "problem_concept_id_unique",
        "CREATE CONSTRAINT problem_concept_id_unique IF NOT EXISTS "
        "FOR (c:ProblemConcept) REQUIRE c.id IS UNIQUE",
    ),
    # Schema metadata
    (
        "schema_version_unique",
        "CREATE CONSTRAINT schema_version_unique IF NOT EXISTS "
        "FOR (s:SchemaVersion) REQUIRE s.version IS UNIQUE",
    ),
]

INDEXES = [
    # Problem indexes
    (
        "problem_status_idx",
        "CREATE INDEX problem_status_idx IF NOT EXISTS FOR (p:Problem) ON (p.status)",
    ),
    (
        "problem_domain_idx",
        "CREATE INDEX problem_domain_idx IF NOT EXISTS FOR (p:Problem) ON (p.domain)",
    ),
    (
        "problem_created_idx",
        "CREATE INDEX problem_created_idx IF NOT EXISTS "
        "FOR (p:Problem) ON (p.created_at)",
    ),
    # Paper indexes
    (
        "paper_year_idx",
        "CREATE INDEX paper_year_idx IF NOT EXISTS FOR (p:Paper) ON (p.year)",
    ),
    (
        "paper_arxiv_idx",
        "CREATE INDEX paper_arxiv_idx IF NOT EXISTS FOR (p:Paper) ON (p.arxiv_id)",
    ),
    (
        "paper_openalex_idx",
        "CREATE INDEX paper_openalex_idx IF NOT EXISTS FOR (p:Paper) ON (p.openalex_id)",
    ),
    # Author indexes
    (
        "author_orcid_idx",
        "CREATE INDEX author_orcid_idx IF NOT EXISTS FOR (a:Author) ON (a.orcid)",
    ),
    (
        "author_name_idx",
        "CREATE INDEX author_name_idx IF NOT EXISTS FOR (a:Author) ON (a.name)",
    ),
    # ProblemMention indexes (canonical architecture)
    (
        "mention_paper_idx",
        "CREATE INDEX mention_paper_idx IF NOT EXISTS FOR (m:ProblemMention) ON (m.paper_doi)",
    ),
    (
        "mention_review_status_idx",
        "CREATE INDEX mention_review_status_idx IF NOT EXISTS FOR (m:ProblemMention) ON (m.review_status)",
    ),
    (
        "mention_concept_idx",
        "CREATE INDEX mention_concept_idx IF NOT EXISTS FOR (m:ProblemMention) ON (m.concept_id)",
    ),
    # ProblemConcept indexes (canonical architecture)
    (
        "concept_domain_idx",
        "CREATE INDEX concept_domain_idx IF NOT EXISTS FOR (c:ProblemConcept) ON (c.domain)",
    ),
    (
        "concept_mention_count_idx",
        "CREATE INDEX concept_mention_count_idx IF NOT EXISTS FOR (c:ProblemConcept) ON (c.mention_count)",
    ),
    (
        "concept_status_idx",
        "CREATE INDEX concept_status_idx IF NOT EXISTS FOR (c:ProblemConcept) ON (c.status)",
    ),
]

# Vector indexes for semantic search (Neo4j 5.x)
VECTOR_INDEXES = [
    # Problem embedding index (original)
    (
        "problem_embedding_idx",
        """
        CREATE VECTOR INDEX problem_embedding_idx IF NOT EXISTS
        FOR (p:Problem)
        ON p.embedding
        OPTIONS {
            indexConfig: {
                `vector.dimensions`: 1536,
                `vector.similarity_function`: 'cosine'
            }
        }
        """
    ),
    # ProblemMention embedding index (canonical architecture)
    (
        "mention_embedding_idx",
        """
        CREATE VECTOR INDEX mention_embedding_idx IF NOT EXISTS
        FOR (m:ProblemMention)
        ON m.embedding
        OPTIONS {
            indexConfig: {
                `vector.dimensions`: 1536,
                `vector.similarity_function`: 'cosine'
            }
        }
        """
    ),
    # ProblemConcept embedding index (canonical architecture)
    (
        "concept_embedding_idx",
        """
        CREATE VECTOR INDEX concept_embedding_idx IF NOT EXISTS
        FOR (c:ProblemConcept)
        ON c.embedding
        OPTIONS {
            indexConfig: {
                `vector.dimensions`: 1536,
                `vector.similarity_function`: 'cosine'
            }
        }
        """
    ),
]


class SchemaManager:
    """
    Manages Neo4j database schema initialization and migrations.

    Provides idempotent schema setup that can be run multiple times safely.
    """

    def __init__(self, repository: Optional[Neo4jRepository] = None):
        """
        Initialize schema manager.

        Args:
            repository: Neo4j repository. Uses global repository if not provided.
        """
        self._repo = repository or get_repository()

    def initialize(self, force: bool = False) -> bool:
        """
        Initialize database schema.

        Creates all constraints, indexes, and vector indexes if they don't exist.
        Tracks schema version for migrations.

        Args:
            force: If True, skip version check and run all migrations.

        Returns:
            True if schema was initialized/updated.
        """
        current_version = self._get_current_version()

        if current_version >= SCHEMA_VERSION and not force:
            logger.info(
                f"Schema is up to date (version {current_version})"
            )
            return False

        logger.info(
            f"Initializing schema (current: {current_version}, target: {SCHEMA_VERSION})"
        )

        # Create constraints
        self._create_constraints()

        # Create indexes
        self._create_indexes()

        # Create vector indexes
        self._create_vector_indexes()

        # Update schema version
        self._set_version(SCHEMA_VERSION)

        logger.info(f"Schema initialized to version {SCHEMA_VERSION}")
        return True

    def _get_current_version(self) -> int:
        """Get current schema version from database."""
        def _get(tx: ManagedTransaction) -> int:
            result = tx.run(
                """
                MATCH (s:SchemaVersion)
                RETURN s.version as version
                ORDER BY s.version DESC
                LIMIT 1
                """
            )
            record = result.single()
            return record["version"] if record else 0

        with self._repo.session() as session:
            return session.execute_read(_get)

    def _set_version(self, version: int) -> None:
        """Set schema version in database."""
        def _set(tx: ManagedTransaction, ver: int) -> None:
            tx.run(
                """
                MERGE (s:SchemaVersion {version: $version})
                SET s.applied_at = $applied_at
                """,
                version=ver,
                applied_at=datetime.now(timezone.utc).isoformat()
            )

        with self._repo.session() as session:
            session.execute_write(lambda tx: _set(tx, version))

    def _create_constraints(self) -> None:
        """Create all constraints."""
        with self._repo.session() as session:
            for name, query in CONSTRAINTS:
                try:
                    session.run(query)
                    logger.debug(f"Created constraint: {name}")
                except Exception as e:
                    # Constraint may already exist
                    logger.debug(f"Constraint {name}: {e}")

    def _create_indexes(self) -> None:
        """Create all indexes."""
        with self._repo.session() as session:
            for name, query in INDEXES:
                try:
                    session.run(query)
                    logger.debug(f"Created index: {name}")
                except Exception as e:
                    logger.debug(f"Index {name}: {e}")

    def _create_vector_indexes(self) -> None:
        """Create vector indexes for semantic search."""
        with self._repo.session() as session:
            for name, query in VECTOR_INDEXES:
                try:
                    session.run(query)
                    logger.info(f"Created vector index: {name}")
                except Exception as e:
                    # Vector index may not be supported or may already exist
                    logger.warning(f"Vector index {name}: {e}")

    def get_schema_info(self) -> dict:
        """
        Get information about current schema.

        Returns:
            Dict with version, constraints, and indexes.
        """
        def _get_constraints(tx: ManagedTransaction) -> list[str]:
            result = tx.run("SHOW CONSTRAINTS")
            return [record["name"] for record in result]

        def _get_indexes(tx: ManagedTransaction) -> list[dict]:
            result = tx.run("SHOW INDEXES")
            return [
                {"name": r["name"], "type": r["type"], "state": r["state"]}
                for r in result
            ]

        with self._repo.session() as session:
            constraints = session.execute_read(_get_constraints)
            indexes = session.execute_read(_get_indexes)

        return {
            "version": self._get_current_version(),
            "target_version": SCHEMA_VERSION,
            "constraints": constraints,
            "indexes": indexes,
        }

    def drop_all(self, confirm: bool = False) -> bool:
        """
        Drop all data and schema (DANGEROUS - use only for testing).

        Args:
            confirm: Must be True to execute.

        Returns:
            True if dropped.
        """
        if not confirm:
            logger.warning("drop_all requires confirm=True")
            return False

        def _drop(tx: ManagedTransaction) -> None:
            # Drop all nodes and relationships
            tx.run("MATCH (n) DETACH DELETE n")

        with self._repo.session() as session:
            session.execute_write(_drop)

        logger.warning("Dropped all data from database")
        return True


def initialize_schema(force: bool = False) -> bool:
    """
    Initialize database schema (convenience function).

    Args:
        force: If True, skip version check.

    Returns:
        True if schema was initialized/updated.
    """
    manager = SchemaManager()
    return manager.initialize(force=force)


def get_schema_info() -> dict:
    """Get schema information (convenience function)."""
    manager = SchemaManager()
    return manager.get_schema_info()
