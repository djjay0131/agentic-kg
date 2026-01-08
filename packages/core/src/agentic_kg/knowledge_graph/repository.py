"""
Neo4j repository for Knowledge Graph CRUD operations.

Provides connection management, transaction support, and entity operations
for Problem, Paper, and Author nodes.
"""

import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator, Optional

from neo4j import GraphDatabase, ManagedTransaction, Session
from neo4j.exceptions import (
    ServiceUnavailable,
    SessionExpired,
    TransientError,
)

from agentic_kg.config import Neo4jConfig, get_config
from agentic_kg.knowledge_graph.models import (
    Author,
    Paper,
    Problem,
    ProblemStatus,
)

logger = logging.getLogger(__name__)


class RepositoryError(Exception):
    """Base exception for repository operations."""

    pass


class ConnectionError(RepositoryError):
    """Raised when database connection fails."""

    pass


class NotFoundError(RepositoryError):
    """Raised when entity is not found."""

    pass


class DuplicateError(RepositoryError):
    """Raised when entity already exists."""

    pass


class Neo4jRepository:
    """
    Repository for Neo4j knowledge graph operations.

    Handles connection pooling, retry logic, and CRUD operations
    for Problem, Paper, and Author entities.
    """

    def __init__(self, config: Optional[Neo4jConfig] = None):
        """
        Initialize repository with Neo4j connection.

        Args:
            config: Neo4j configuration. Uses global config if not provided.
        """
        self._config = config or get_config().neo4j
        self._driver = None

    @property
    def driver(self):
        """Lazy-load the Neo4j driver."""
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                self._config.uri,
                auth=(self._config.username, self._config.password),
                max_connection_lifetime=self._config.max_connection_lifetime,
                max_connection_pool_size=self._config.max_connection_pool_size,
                connection_acquisition_timeout=self._config.connection_acquisition_timeout,
            )
        return self._driver

    def close(self) -> None:
        """Close the database connection."""
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def verify_connectivity(self) -> bool:
        """
        Verify database connectivity.

        Returns:
            True if connection successful.

        Raises:
            ConnectionError: If connection fails after retries.
        """
        for attempt in range(self._config.max_retries):
            try:
                self.driver.verify_connectivity()
                logger.info("Neo4j connection verified")
                return True
            except ServiceUnavailable as e:
                if attempt < self._config.max_retries - 1:
                    delay = self._config.retry_delay * (2**attempt)
                    logger.warning(
                        f"Connection attempt {attempt + 1} failed, retrying in {delay}s"
                    )
                    time.sleep(delay)
                else:
                    raise ConnectionError(f"Failed to connect to Neo4j: {e}") from e
        return False

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        """
        Get a database session with automatic cleanup.

        Yields:
            Neo4j session.
        """
        session = self.driver.session(database=self._config.database)
        try:
            yield session
        finally:
            session.close()

    def _execute_with_retry(
        self, session: Session, work: callable, *args, **kwargs
    ) -> Any:
        """
        Execute work with retry logic for transient errors.

        Args:
            session: Neo4j session.
            work: Function to execute (should accept tx as first arg).
            *args: Additional args for work function.
            **kwargs: Additional kwargs for work function.

        Returns:
            Result of work function.
        """
        for attempt in range(self._config.max_retries):
            try:
                return session.execute_write(
                    lambda tx: work(tx, *args, **kwargs)
                )
            except (TransientError, SessionExpired) as e:
                if attempt < self._config.max_retries - 1:
                    delay = self._config.retry_delay * (2**attempt)
                    logger.warning(
                        f"Transient error on attempt {attempt + 1}, retrying in {delay}s: {e}"
                    )
                    time.sleep(delay)
                else:
                    raise RepositoryError(f"Operation failed after retries: {e}") from e
        return None

    # =========================================================================
    # Problem Operations
    # =========================================================================

    def create_problem(
        self,
        problem: Problem,
        generate_embedding: bool = True,
    ) -> Problem:
        """
        Create a new Problem node.

        Args:
            problem: Problem to create.
            generate_embedding: If True, auto-generate embedding for the problem.
                Set to False for batch operations where embeddings are generated
                separately.

        Returns:
            Created problem with any server-generated values (including embedding).

        Raises:
            DuplicateError: If problem with same ID exists.
        """
        # Generate embedding if requested and not already present
        if generate_embedding and problem.embedding is None:
            try:
                from agentic_kg.knowledge_graph.embeddings import (
                    generate_problem_embedding,
                )
                problem.embedding = generate_problem_embedding(problem)
                logger.debug(f"Generated embedding for problem {problem.id}")
            except Exception as e:
                # Graceful degradation: log warning but continue without embedding
                # Problem can be embedded later via batch processing
                logger.warning(
                    f"Failed to generate embedding for problem {problem.id}: {e}. "
                    "Problem will be created without embedding."
                )

        def _create(tx: ManagedTransaction, props: dict) -> dict:
            # Check for duplicate
            check = tx.run(
                "MATCH (p:Problem {id: $id}) RETURN p.id",
                id=props["id"]
            )
            if check.single():
                raise DuplicateError(f"Problem with ID {props['id']} already exists")

            # Create node
            result = tx.run(
                """
                CREATE (p:Problem)
                SET p = $props
                RETURN p
                """,
                props=props
            )
            record = result.single()
            return dict(record["p"]) if record else None

        props = problem.to_neo4j_properties()
        # Convert nested dicts to JSON strings for Neo4j storage
        props["assumptions"] = json.dumps(props["assumptions"])
        props["constraints"] = json.dumps(props["constraints"])
        props["datasets"] = json.dumps(props["datasets"])
        props["metrics"] = json.dumps(props["metrics"])
        props["baselines"] = json.dumps(props["baselines"])
        props["evidence"] = json.dumps(props["evidence"])
        props["extraction_metadata"] = json.dumps(props["extraction_metadata"])

        # Add embedding if present (excluded by to_neo4j_properties for size)
        if problem.embedding is not None:
            props["embedding"] = problem.embedding

        with self.session() as session:
            self._execute_with_retry(session, _create, props)

        logger.info(f"Created problem: {problem.id}")
        return problem

    def get_problem(self, problem_id: str) -> Problem:
        """
        Get a Problem by ID.

        Args:
            problem_id: Problem ID.

        Returns:
            Problem instance.

        Raises:
            NotFoundError: If problem not found.
        """
        def _get(tx: ManagedTransaction, pid: str) -> Optional[dict]:
            result = tx.run(
                "MATCH (p:Problem {id: $id}) RETURN p",
                id=pid
            )
            record = result.single()
            return dict(record["p"]) if record else None

        with self.session() as session:
            data = session.execute_read(lambda tx: _get(tx, problem_id))

        if data is None:
            raise NotFoundError(f"Problem not found: {problem_id}")

        return self._problem_from_neo4j(data)

    def update_problem(
        self,
        problem: Problem,
        regenerate_embedding: bool = False,
    ) -> Problem:
        """
        Update an existing Problem.

        Args:
            problem: Problem with updated values.
            regenerate_embedding: If True, regenerate embedding (use when
                statement has changed).

        Returns:
            Updated problem.

        Raises:
            NotFoundError: If problem not found.
        """
        def _update(tx: ManagedTransaction, pid: str, props: dict) -> bool:
            result = tx.run(
                """
                MATCH (p:Problem {id: $id})
                SET p += $props
                RETURN p.id
                """,
                id=pid,
                props=props
            )
            return result.single() is not None

        # Regenerate embedding if requested
        if regenerate_embedding:
            try:
                from agentic_kg.knowledge_graph.embeddings import (
                    generate_problem_embedding,
                )
                problem.embedding = generate_problem_embedding(problem)
                logger.debug(f"Regenerated embedding for problem {problem.id}")
            except Exception as e:
                logger.warning(
                    f"Failed to regenerate embedding for problem {problem.id}: {e}"
                )

        # Update timestamp and version
        problem.updated_at = datetime.now(timezone.utc)
        problem.version += 1

        props = problem.to_neo4j_properties()
        props["assumptions"] = json.dumps(props["assumptions"])
        props["constraints"] = json.dumps(props["constraints"])
        props["datasets"] = json.dumps(props["datasets"])
        props["metrics"] = json.dumps(props["metrics"])
        props["baselines"] = json.dumps(props["baselines"])
        props["evidence"] = json.dumps(props["evidence"])
        props["extraction_metadata"] = json.dumps(props["extraction_metadata"])

        # Add embedding if present
        if problem.embedding is not None:
            props["embedding"] = problem.embedding

        with self.session() as session:
            found = self._execute_with_retry(session, _update, problem.id, props)

        if not found:
            raise NotFoundError(f"Problem not found: {problem.id}")

        logger.info(f"Updated problem: {problem.id} (v{problem.version})")
        return problem

    def delete_problem(self, problem_id: str, soft: bool = True) -> bool:
        """
        Delete a Problem (soft delete by default).

        Args:
            problem_id: Problem ID.
            soft: If True, set status to DEPRECATED instead of removing.

        Returns:
            True if deleted.

        Raises:
            NotFoundError: If problem not found.
        """
        if soft:
            # Soft delete: change status to deprecated
            problem = self.get_problem(problem_id)
            problem.status = ProblemStatus.DEPRECATED
            self.update_problem(problem)
            logger.info(f"Soft deleted problem: {problem_id}")
            return True

        def _delete(tx: ManagedTransaction, pid: str) -> bool:
            result = tx.run(
                """
                MATCH (p:Problem {id: $id})
                DETACH DELETE p
                RETURN count(*) as deleted
                """,
                id=pid
            )
            record = result.single()
            return record["deleted"] > 0 if record else False

        with self.session() as session:
            deleted = self._execute_with_retry(session, _delete, problem_id)

        if not deleted:
            raise NotFoundError(f"Problem not found: {problem_id}")

        logger.info(f"Hard deleted problem: {problem_id}")
        return True

    def list_problems(
        self,
        status: Optional[ProblemStatus] = None,
        domain: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Problem]:
        """
        List problems with optional filtering.

        Args:
            status: Filter by status.
            domain: Filter by domain.
            limit: Maximum results.
            offset: Skip first N results.

        Returns:
            List of problems.
        """
        def _list(
            tx: ManagedTransaction,
            status_val: Optional[str],
            domain_val: Optional[str],
            lim: int,
            off: int,
        ) -> list[dict]:
            query = "MATCH (p:Problem)"
            conditions = []
            params: dict[str, Any] = {"limit": lim, "offset": off}

            if status_val:
                conditions.append("p.status = $status")
                params["status"] = status_val
            if domain_val:
                conditions.append("p.domain = $domain")
                params["domain"] = domain_val

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            query += " RETURN p ORDER BY p.created_at DESC SKIP $offset LIMIT $limit"

            result = tx.run(query, **params)
            return [dict(record["p"]) for record in result]

        status_str = status.value if status else None

        with self.session() as session:
            records = session.execute_read(
                lambda tx: _list(tx, status_str, domain, limit, offset)
            )

        return [self._problem_from_neo4j(r) for r in records]

    def _problem_from_neo4j(self, data: dict) -> Problem:
        """Convert Neo4j node data to Problem model."""
        # Parse JSON strings back to objects
        data["assumptions"] = json.loads(data.get("assumptions", "[]"))
        data["constraints"] = json.loads(data.get("constraints", "[]"))
        data["datasets"] = json.loads(data.get("datasets", "[]"))
        data["metrics"] = json.loads(data.get("metrics", "[]"))
        data["baselines"] = json.loads(data.get("baselines", "[]"))
        data["evidence"] = json.loads(data.get("evidence", "{}"))
        data["extraction_metadata"] = json.loads(
            data.get("extraction_metadata", "{}")
        )

        # Parse datetimes
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        if isinstance(data.get("updated_at"), str):
            data["updated_at"] = datetime.fromisoformat(data["updated_at"])

        # Parse nested model datetimes
        if "extracted_at" in data.get("extraction_metadata", {}):
            ext_meta = data["extraction_metadata"]
            if isinstance(ext_meta["extracted_at"], str):
                ext_meta["extracted_at"] = datetime.fromisoformat(
                    ext_meta["extracted_at"]
                )
            if ext_meta.get("reviewed_at") and isinstance(
                ext_meta["reviewed_at"], str
            ):
                ext_meta["reviewed_at"] = datetime.fromisoformat(
                    ext_meta["reviewed_at"]
                )

        return Problem(**data)

    # =========================================================================
    # Paper Operations
    # =========================================================================

    def create_paper(self, paper: Paper) -> Paper:
        """
        Create a new Paper node.

        Args:
            paper: Paper to create.

        Returns:
            Created paper.

        Raises:
            DuplicateError: If paper with same DOI exists.
        """
        def _create(tx: ManagedTransaction, props: dict) -> dict:
            check = tx.run(
                "MATCH (p:Paper {doi: $doi}) RETURN p.doi",
                doi=props["doi"]
            )
            if check.single():
                raise DuplicateError(f"Paper with DOI {props['doi']} already exists")

            result = tx.run(
                """
                CREATE (p:Paper)
                SET p = $props
                RETURN p
                """,
                props=props
            )
            record = result.single()
            return dict(record["p"]) if record else None

        props = paper.to_neo4j_properties()

        with self.session() as session:
            self._execute_with_retry(session, _create, props)

        logger.info(f"Created paper: {paper.doi}")
        return paper

    def get_paper(self, doi: str) -> Paper:
        """
        Get a Paper by DOI.

        Args:
            doi: Paper DOI.

        Returns:
            Paper instance.

        Raises:
            NotFoundError: If paper not found.
        """
        def _get(tx: ManagedTransaction, paper_doi: str) -> Optional[dict]:
            result = tx.run(
                "MATCH (p:Paper {doi: $doi}) RETURN p",
                doi=paper_doi
            )
            record = result.single()
            return dict(record["p"]) if record else None

        with self.session() as session:
            data = session.execute_read(lambda tx: _get(tx, doi))

        if data is None:
            raise NotFoundError(f"Paper not found: {doi}")

        return self._paper_from_neo4j(data)

    def update_paper(self, paper: Paper) -> Paper:
        """
        Update an existing Paper.

        Args:
            paper: Paper with updated values.

        Returns:
            Updated paper.

        Raises:
            NotFoundError: If paper not found.
        """
        def _update(tx: ManagedTransaction, doi: str, props: dict) -> bool:
            result = tx.run(
                """
                MATCH (p:Paper {doi: $doi})
                SET p += $props
                RETURN p.doi
                """,
                doi=doi,
                props=props
            )
            return result.single() is not None

        props = paper.to_neo4j_properties()

        with self.session() as session:
            found = self._execute_with_retry(session, _update, paper.doi, props)

        if not found:
            raise NotFoundError(f"Paper not found: {paper.doi}")

        logger.info(f"Updated paper: {paper.doi}")
        return paper

    def delete_paper(self, doi: str) -> bool:
        """
        Delete a Paper.

        Args:
            doi: Paper DOI.

        Returns:
            True if deleted.

        Raises:
            NotFoundError: If paper not found.
        """
        def _delete(tx: ManagedTransaction, paper_doi: str) -> bool:
            result = tx.run(
                """
                MATCH (p:Paper {doi: $doi})
                DETACH DELETE p
                RETURN count(*) as deleted
                """,
                doi=paper_doi
            )
            record = result.single()
            return record["deleted"] > 0 if record else False

        with self.session() as session:
            deleted = self._execute_with_retry(session, _delete, doi)

        if not deleted:
            raise NotFoundError(f"Paper not found: {doi}")

        logger.info(f"Deleted paper: {doi}")
        return True

    def _paper_from_neo4j(self, data: dict) -> Paper:
        """Convert Neo4j node data to Paper model."""
        if isinstance(data.get("ingested_at"), str):
            data["ingested_at"] = datetime.fromisoformat(data["ingested_at"])
        return Paper(**data)

    # =========================================================================
    # Author Operations
    # =========================================================================

    def create_author(self, author: Author) -> Author:
        """
        Create a new Author node.

        Args:
            author: Author to create.

        Returns:
            Created author.

        Raises:
            DuplicateError: If author with same ID exists.
        """
        def _create(tx: ManagedTransaction, props: dict) -> dict:
            check = tx.run(
                "MATCH (a:Author {id: $id}) RETURN a.id",
                id=props["id"]
            )
            if check.single():
                raise DuplicateError(f"Author with ID {props['id']} already exists")

            result = tx.run(
                """
                CREATE (a:Author)
                SET a = $props
                RETURN a
                """,
                props=props
            )
            record = result.single()
            return dict(record["a"]) if record else None

        props = author.to_neo4j_properties()

        with self.session() as session:
            self._execute_with_retry(session, _create, props)

        logger.info(f"Created author: {author.id} ({author.name})")
        return author

    def get_author(self, author_id: str) -> Author:
        """
        Get an Author by ID.

        Args:
            author_id: Author ID.

        Returns:
            Author instance.

        Raises:
            NotFoundError: If author not found.
        """
        def _get(tx: ManagedTransaction, aid: str) -> Optional[dict]:
            result = tx.run(
                "MATCH (a:Author {id: $id}) RETURN a",
                id=aid
            )
            record = result.single()
            return dict(record["a"]) if record else None

        with self.session() as session:
            data = session.execute_read(lambda tx: _get(tx, author_id))

        if data is None:
            raise NotFoundError(f"Author not found: {author_id}")

        return Author(**data)

    def update_author(self, author: Author) -> Author:
        """
        Update an existing Author.

        Args:
            author: Author with updated values.

        Returns:
            Updated author.

        Raises:
            NotFoundError: If author not found.
        """
        def _update(tx: ManagedTransaction, aid: str, props: dict) -> bool:
            result = tx.run(
                """
                MATCH (a:Author {id: $id})
                SET a += $props
                RETURN a.id
                """,
                id=aid,
                props=props
            )
            return result.single() is not None

        props = author.to_neo4j_properties()

        with self.session() as session:
            found = self._execute_with_retry(session, _update, author.id, props)

        if not found:
            raise NotFoundError(f"Author not found: {author.id}")

        logger.info(f"Updated author: {author.id}")
        return author

    def get_papers_by_author(self, author_id: str) -> list[Paper]:
        """
        Get all papers by an author.

        Args:
            author_id: Author ID.

        Returns:
            List of papers.
        """
        def _get(tx: ManagedTransaction, aid: str) -> list[dict]:
            result = tx.run(
                """
                MATCH (a:Author {id: $id})<-[:AUTHORED_BY]-(p:Paper)
                RETURN p
                ORDER BY p.year DESC
                """,
                id=aid
            )
            return [dict(record["p"]) for record in result]

        with self.session() as session:
            records = session.execute_read(lambda tx: _get(tx, author_id))

        return [self._paper_from_neo4j(r) for r in records]


# Module-level convenience functions
_repository: Optional[Neo4jRepository] = None


def get_repository() -> Neo4jRepository:
    """Get the repository singleton."""
    global _repository
    if _repository is None:
        _repository = Neo4jRepository()
    return _repository


def reset_repository() -> None:
    """Reset the repository singleton (useful for testing)."""
    global _repository
    if _repository is not None:
        _repository.close()
        _repository = None
