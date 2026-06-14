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
    Method,
    Model,
    Paper,
    Problem,
    ProblemStatus,
    ResearchConcept,
    Topic,
    TopicLevel,
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


def decode_json_field(value: Any, default: Any) -> Any:
    """
    Decode a JSON-encoded Neo4j property back to a Python object.

    Tolerates legacy nodes whose nested fields were double-encoded by an
    earlier serialization bug: repeatedly json.loads until the value is no
    longer a string (capped to avoid pathological input).
    """
    if value is None:
        return default
    for _ in range(3):
        if not isinstance(value, str):
            break
        value = json.loads(value)
    return value


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

    def _find_duplicate_problem(
        self,
        statement: str,
    ) -> Optional[dict]:
        """
        Check if a problem with similar statement already exists.

        Uses exact match on normalized statement across the graph.
        """
        normalized = statement.lower().strip()

        def _check(tx: ManagedTransaction) -> Optional[dict]:
            result = tx.run(
                """
                MATCH (p:Problem)
                WHERE toLower(trim(p.statement)) = $statement
                RETURN p.id as id, p.statement as statement, p.status as status
                LIMIT 1
                """,
                statement=normalized,
            )
            record = result.single()
            return dict(record) if record else None

        with self.session() as session:
            return session.execute_read(_check)

    def create_problem(
        self,
        problem: Problem,
        generate_embedding: bool = True,
        skip_duplicate_check: bool = False,
    ) -> Problem:
        """
        Create a new Problem node.

        Args:
            problem: Problem to create.
            generate_embedding: If True, auto-generate embedding for the problem.
                Set to False for batch operations where embeddings are generated
                separately.
            skip_duplicate_check: If True, skip checking for duplicate problem statements.
                Use with caution - may create duplicates.

        Returns:
            Created problem with any server-generated values (including embedding).

        Raises:
            DuplicateError: If problem with same ID or statement already exists.
        """
        # Check for duplicate problem statement (before generating embedding)
        if not skip_duplicate_check:
            existing = self._find_duplicate_problem(problem.statement)
            if existing:
                raise DuplicateError(
                    f"Problem with similar statement already exists (ID: {existing['id']}). "
                    f"Existing: '{existing['statement']}'"
                )

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

        # to_neo4j_properties() already JSON-serializes nested fields
        props = problem.to_neo4j_properties()

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

        # to_neo4j_properties() already JSON-serializes nested fields
        props = problem.to_neo4j_properties()

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
        limit: int = 100,
        offset: int = 0,
    ) -> list[Problem]:
        """
        List problems with optional filtering.

        Args:
            status: Filter by status.
            limit: Maximum results.
            offset: Skip first N results.

        Returns:
            List of problems.
        """
        def _list(
            tx: ManagedTransaction,
            status_val: Optional[str],
            lim: int,
            off: int,
        ) -> list[dict]:
            query = "MATCH (p:Problem)"
            params: dict[str, Any] = {"limit": lim, "offset": off}

            if status_val:
                query += " WHERE p.status = $status"
                params["status"] = status_val

            query += " RETURN p ORDER BY p.created_at DESC SKIP $offset LIMIT $limit"

            result = tx.run(query, **params)
            return [dict(record["p"]) for record in result]

        status_str = status.value if status else None

        with self.session() as session:
            records = session.execute_read(
                lambda tx: _list(tx, status_str, limit, offset)
            )

        # A single malformed (e.g. legacy-serialized) node must not break
        # the whole listing — skip and log instead.
        problems = []
        for record in records:
            try:
                problems.append(self._problem_from_neo4j(record))
            except Exception as e:
                logger.warning("Skipping unreadable Problem node: %s", e)
        return problems

    def _problem_from_neo4j(self, data: dict) -> Problem:
        """Convert Neo4j node data to Problem model."""
        # Parse JSON strings back to objects (tolerates legacy double-encoding)
        data["assumptions"] = decode_json_field(data.get("assumptions"), [])
        data["constraints"] = decode_json_field(data.get("constraints"), [])
        data["datasets"] = decode_json_field(data.get("datasets"), [])
        data["metrics"] = decode_json_field(data.get("metrics"), [])
        data["baselines"] = decode_json_field(data.get("baselines"), [])
        data["evidence"] = decode_json_field(data.get("evidence"), {})
        data["extraction_metadata"] = decode_json_field(
            data.get("extraction_metadata"), {}
        )

        # Parse datetimes
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        if isinstance(data.get("updated_at"), str):
            data["updated_at"] = datetime.fromisoformat(data["updated_at"])

        # Parse nested model datetimes
        if isinstance(data.get("extraction_metadata"), dict) and (
            "extracted_at" in data["extraction_metadata"]
        ):
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

    def link_paper_to_author(
        self, paper_doi: str, author_id: str, position: int = 1
    ) -> None:
        """
        Create an AUTHORED_BY relationship between a Paper and an Author.

        Args:
            paper_doi: Paper DOI.
            author_id: Author ID.
            position: Author position (1-indexed).
        """
        def _link(tx: ManagedTransaction, doi: str, aid: str, pos: int) -> None:
            tx.run(
                """
                MATCH (p:Paper {doi: $doi})
                MATCH (a:Author {id: $aid})
                MERGE (p)-[r:AUTHORED_BY]->(a)
                SET r.position = $pos
                """,
                doi=doi,
                aid=aid,
                pos=pos,
            )

        with self.session() as session:
            self._execute_with_retry(session, _link, paper_doi, author_id, position)

        logger.debug(f"Linked paper {paper_doi} to author {author_id} (position {position})")

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

    # =========================================================================
    # Topic Operations (E-1)
    # =========================================================================

    _ASSIGN_RELATIONSHIPS = {
        "Problem": ("BELONGS_TO", "problem_count"),
        "ProblemMention": ("BELONGS_TO", "problem_count"),
        "ProblemConcept": ("BELONGS_TO", "problem_count"),
        "Paper": ("RESEARCHES", "paper_count"),
    }

    def _topic_props(self, topic: Topic) -> dict:
        """Serialize a Topic for Neo4j, including embedding when present."""
        props = topic.to_neo4j_properties()
        if topic.embedding is not None:
            props["embedding"] = topic.embedding
        return props

    def _topic_from_neo4j(self, data: dict) -> Topic:
        """Convert a Neo4j node record back into a Topic model."""
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        if isinstance(data.get("updated_at"), str):
            data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        if isinstance(data.get("level"), str):
            data["level"] = TopicLevel(data["level"])
        return Topic(**data)

    def create_topic(
        self,
        topic: Topic,
        generate_embedding: bool = True,
    ) -> Topic:
        """
        Create a new Topic node.

        Args:
            topic: Topic to create.
            generate_embedding: If True and `topic.embedding` is None, generate
                an embedding from `{name}: {description}`.

        Returns:
            Created topic (with embedding populated if generated).

        Raises:
            DuplicateError: If a Topic with the same ID already exists.
        """
        if generate_embedding and topic.embedding is None:
            try:
                from agentic_kg.knowledge_graph.embeddings import (
                    generate_topic_embedding,
                )
                topic.embedding = generate_topic_embedding(
                    topic.name, topic.description
                )
                logger.debug(f"Generated embedding for topic {topic.id}")
            except Exception as e:
                logger.warning(
                    f"Failed to generate embedding for topic {topic.id}: {e}. "
                    "Topic will be created without embedding."
                )

        def _create(tx: ManagedTransaction, props: dict) -> None:
            check = tx.run(
                "MATCH (t:Topic {id: $id}) RETURN t.id",
                id=props["id"],
            )
            if check.single():
                raise DuplicateError(
                    f"Topic with ID {props['id']} already exists"
                )
            tx.run(
                """
                CREATE (t:Topic)
                SET t = $props
                """,
                props=props,
            )

        props = self._topic_props(topic)

        with self.session() as session:
            self._execute_with_retry(session, _create, props)

        if topic.parent_id:
            self.link_topic_parent(topic.id, topic.parent_id)

        logger.info(f"Created topic: {topic.id} ({topic.name})")
        return topic

    def merge_topic(
        self,
        topic: Topic,
        generate_embedding: bool = True,
    ) -> Topic:
        """
        Idempotently upsert a Topic by (name, level, parent_id).

        Used by the seed taxonomy loader to keep imports idempotent.
        Returns the existing Topic if one matches, otherwise creates a new
        one. Updates embedding and description on the existing node.

        Args:
            topic: Topic to merge.
            generate_embedding: If True and `topic.embedding` is None, generate
                an embedding before upserting.

        Returns:
            The merged Topic (may be the pre-existing DB row).
        """
        if generate_embedding and topic.embedding is None:
            try:
                from agentic_kg.knowledge_graph.embeddings import (
                    generate_topic_embedding,
                )
                topic.embedding = generate_topic_embedding(
                    topic.name, topic.description
                )
            except Exception as e:
                logger.warning(
                    f"Failed to generate embedding for topic {topic.name}: {e}"
                )

        def _merge(tx: ManagedTransaction, props: dict) -> dict:
            # Match on the natural identity. Neo4j 5.x rejects MERGE on a
            # null property value, so root topics (parent_id IS NULL) use a
            # different MERGE pattern that omits parent_id from the keys;
            # parent_id is set explicitly in ON CREATE. Domain-level Topics
            # are required to have a unique (name, level=domain) per the
            # Topic.validate_domain_has_no_parent invariant, so name+level
            # is a stable identity for the root tier.
            parent_id = props.get("parent_id")
            if parent_id is None:
                cypher = """
                MERGE (t:Topic {name: $name, level: $level})
                  ON CREATE SET
                      t.id = $id,
                      t.parent_id = NULL,
                      t.description = $description,
                      t.source = $source,
                      t.openalex_id = $openalex_id,
                      t.embedding = $embedding,
                      t.problem_count = $problem_count,
                      t.paper_count = $paper_count,
                      t.created_at = $created_at,
                      t.updated_at = $updated_at
                  ON MATCH SET
                      t.description = coalesce($description, t.description),
                      t.openalex_id = coalesce($openalex_id, t.openalex_id),
                      t.embedding = coalesce($embedding, t.embedding),
                      t.updated_at = $updated_at
                RETURN t
                """
            else:
                cypher = """
                MERGE (t:Topic {
                    name: $name,
                    level: $level,
                    parent_id: $parent_id
                })
                ON CREATE SET
                    t.id = $id,
                    t.description = $description,
                    t.source = $source,
                    t.openalex_id = $openalex_id,
                    t.embedding = $embedding,
                    t.problem_count = $problem_count,
                    t.paper_count = $paper_count,
                    t.created_at = $created_at,
                    t.updated_at = $updated_at
                ON MATCH SET
                    t.description = coalesce($description, t.description),
                    t.openalex_id = coalesce($openalex_id, t.openalex_id),
                    t.embedding = coalesce($embedding, t.embedding),
                    t.updated_at = $updated_at
                RETURN t
                """

            result = tx.run(
                cypher,
                name=props["name"],
                level=props["level"],
                parent_id=parent_id,
                id=props["id"],
                description=props.get("description"),
                source=props.get("source", "manual"),
                openalex_id=props.get("openalex_id"),
                embedding=props.get("embedding"),
                problem_count=props.get("problem_count", 0),
                paper_count=props.get("paper_count", 0),
                created_at=props["created_at"],
                updated_at=props["updated_at"],
            )
            record = result.single()
            return dict(record["t"]) if record else None

        props = self._topic_props(topic)

        with self.session() as session:
            data = self._execute_with_retry(session, _merge, props)

        if topic.parent_id:
            # Ensure the SUBTOPIC_OF edge exists (idempotent)
            self.link_topic_parent(data["id"], topic.parent_id)

        logger.debug(f"Merged topic: {data['id']} ({data['name']})")
        return self._topic_from_neo4j(data)

    def get_topic(self, topic_id: str) -> Topic:
        """
        Fetch a Topic by ID.

        Raises NotFoundError when no Topic matches.
        """
        def _get(tx: ManagedTransaction, tid: str) -> Optional[dict]:
            result = tx.run(
                "MATCH (t:Topic {id: $id}) RETURN t",
                id=tid,
            )
            record = result.single()
            return dict(record["t"]) if record else None

        with self.session() as session:
            data = session.execute_read(lambda tx: _get(tx, topic_id))

        if data is None:
            raise NotFoundError(f"Topic not found: {topic_id}")

        return self._topic_from_neo4j(data)

    def get_topic_by_name(self, name: str) -> Topic:
        """Fetch a Topic by its (case-sensitive) name.

        Used by ``integrate_paper_entities`` after the LLM emits a closed-set
        ``topic_name`` from the taxonomy snapshot. If the taxonomy has been
        mutated mid-batch and the name no longer exists, this surfaces as a
        ``NotFoundError`` — the caller is expected to log and skip rather
        than crash the whole paper.

        Raises ``NotFoundError`` when no Topic matches. If multiple Topics
        share the name across levels, the lowest-level (most specific) one
        is returned, then alphabetical by id as a deterministic tiebreaker.
        """
        def _get(tx: ManagedTransaction, nm: str) -> Optional[dict]:
            result = tx.run(
                """
                MATCH (t:Topic {name: $name})
                RETURN t
                ORDER BY
                  CASE t.level
                    WHEN 'subtopic' THEN 0
                    WHEN 'area' THEN 1
                    WHEN 'domain' THEN 2
                    ELSE 3
                  END,
                  t.id
                LIMIT 1
                """,
                name=nm,
            )
            record = result.single()
            return dict(record["t"]) if record else None

        with self.session() as session:
            data = session.execute_read(lambda tx: _get(tx, name))

        if data is None:
            raise NotFoundError(f"Topic not found: {name}")

        return self._topic_from_neo4j(data)

    def update_topic(
        self,
        topic: Topic,
        regenerate_embedding: bool = False,
    ) -> Topic:
        """
        Update an existing Topic.

        Does not change the SUBTOPIC_OF edge — use link_topic_parent for that.
        """
        if regenerate_embedding:
            try:
                from agentic_kg.knowledge_graph.embeddings import (
                    generate_topic_embedding,
                )
                topic.embedding = generate_topic_embedding(
                    topic.name, topic.description
                )
            except Exception as e:
                logger.warning(
                    f"Failed to regenerate embedding for topic {topic.id}: {e}"
                )

        topic.updated_at = datetime.now(timezone.utc)
        props = self._topic_props(topic)

        def _update(tx: ManagedTransaction, tid: str, p: dict) -> bool:
            result = tx.run(
                """
                MATCH (t:Topic {id: $id})
                SET t += $props
                RETURN t.id
                """,
                id=tid,
                props=p,
            )
            return result.single() is not None

        with self.session() as session:
            found = self._execute_with_retry(session, _update, topic.id, props)

        if not found:
            raise NotFoundError(f"Topic not found: {topic.id}")

        logger.info(f"Updated topic: {topic.id}")
        return topic

    def delete_topic(self, topic_id: str) -> bool:
        """
        Delete a Topic and all its edges.

        Raises NotFoundError if the Topic does not exist.
        """
        def _delete(tx: ManagedTransaction, tid: str) -> bool:
            result = tx.run(
                """
                MATCH (t:Topic {id: $id})
                DETACH DELETE t
                RETURN count(*) as deleted
                """,
                id=tid,
            )
            record = result.single()
            return record["deleted"] > 0 if record else False

        with self.session() as session:
            deleted = self._execute_with_retry(session, _delete, topic_id)

        if not deleted:
            raise NotFoundError(f"Topic not found: {topic_id}")

        logger.info(f"Deleted topic: {topic_id}")
        return True

    def link_topic_parent(self, child_id: str, parent_id: str) -> None:
        """
        Create (or reuse) a SUBTOPIC_OF edge from child to parent.

        Idempotent via MERGE. Raises NotFoundError if either node is missing.
        """
        def _link(tx: ManagedTransaction, cid: str, pid: str) -> int:
            result = tx.run(
                """
                MATCH (child:Topic {id: $child_id})
                MATCH (parent:Topic {id: $parent_id})
                MERGE (child)-[:SUBTOPIC_OF]->(parent)
                SET child.parent_id = $parent_id,
                    child.updated_at = $now
                RETURN count(*) as linked
                """,
                child_id=cid,
                parent_id=pid,
                now=datetime.now(timezone.utc).isoformat(),
            )
            record = result.single()
            return record["linked"] if record else 0

        with self.session() as session:
            linked = self._execute_with_retry(session, _link, child_id, parent_id)

        if linked == 0:
            raise NotFoundError(
                f"Cannot link: topic {child_id} or parent {parent_id} not found"
            )

        logger.debug(f"Linked topic {child_id} SUBTOPIC_OF {parent_id}")

    def get_topic_children(self, topic_id: str) -> list[Topic]:
        """Return direct children (via SUBTOPIC_OF) of a Topic."""
        def _children(tx: ManagedTransaction, tid: str) -> list[dict]:
            result = tx.run(
                """
                MATCH (c:Topic)-[:SUBTOPIC_OF]->(p:Topic {id: $id})
                RETURN c
                ORDER BY c.name
                """,
                id=tid,
            )
            return [dict(r["c"]) for r in result]

        with self.session() as session:
            records = session.execute_read(lambda tx: _children(tx, topic_id))

        return [self._topic_from_neo4j(r) for r in records]

    def get_topics_by_level(self, level: TopicLevel) -> list[Topic]:
        """Return all Topics at a given hierarchy level."""
        def _by_level(tx: ManagedTransaction, lvl: str) -> list[dict]:
            result = tx.run(
                """
                MATCH (t:Topic {level: $level})
                RETURN t
                ORDER BY t.name
                """,
                level=lvl,
            )
            return [dict(r["t"]) for r in result]

        with self.session() as session:
            records = session.execute_read(
                lambda tx: _by_level(tx, level.value)
            )

        return [self._topic_from_neo4j(r) for r in records]

    def get_topic_tree(
        self, root_id: Optional[str] = None
    ) -> list[dict]:
        """
        Return the topic hierarchy as a nested tree.

        If `root_id` is None, returns every domain-level root with its
        descendants. Each node is a dict with topic properties plus a
        `children` list.
        """
        def _tree(tx: ManagedTransaction, rid: Optional[str]) -> list[dict]:
            if rid is None:
                query = """
                MATCH (t:Topic {level: 'domain'})
                RETURN t
                ORDER BY t.name
                """
                params: dict[str, Any] = {}
            else:
                query = "MATCH (t:Topic {id: $id}) RETURN t"
                params = {"id": rid}
            result = tx.run(query, **params)
            return [dict(r["t"]) for r in result]

        with self.session() as session:
            roots = session.execute_read(lambda tx: _tree(tx, root_id))

        def build(node_data: dict) -> dict:
            topic = self._topic_from_neo4j(dict(node_data))
            children = self.get_topic_children(topic.id)
            payload = topic.model_dump(exclude={"embedding"})
            payload["level"] = topic.level.value
            payload["children"] = [build(c.model_dump()) for c in children]
            return payload

        return [build(r) for r in roots]

    def search_topics_by_embedding(
        self,
        embedding: list[float],
        limit: int = 10,
        level: Optional[TopicLevel] = None,
    ) -> list[tuple[Topic, float]]:
        """
        Vector similarity search over Topic embeddings.

        Uses the topic_embedding_idx vector index. Returns (topic, score)
        pairs ordered by descending cosine similarity.
        """
        def _search(
            tx: ManagedTransaction,
            emb: list[float],
            lim: int,
            lvl: Optional[str],
        ) -> list[dict]:
            query = """
            CALL db.index.vector.queryNodes('topic_embedding_idx', $limit, $embedding)
            YIELD node, score
            """
            params: dict[str, Any] = {"embedding": emb, "limit": lim}
            if lvl:
                query += "WHERE node.level = $level\n"
                params["level"] = lvl
            query += "RETURN node as t, score ORDER BY score DESC"
            result = tx.run(query, **params)
            return [{"topic": dict(r["t"]), "score": r["score"]} for r in result]

        with self.session() as session:
            records = session.execute_read(
                lambda tx: _search(tx, embedding, limit, level.value if level else None)
            )

        return [(self._topic_from_neo4j(r["topic"]), r["score"]) for r in records]

    def assign_entity_to_topic(
        self,
        entity_id: str,
        topic_id: str,
        entity_label: str,
    ) -> bool:
        """
        Link an entity (Problem, ProblemMention, ProblemConcept, Paper) to a Topic.

        Uses BELONGS_TO for problem-side nodes and RESEARCHES for papers.
        Idempotent via MERGE; when the edge is newly created, increments the
        matching denormalized count on the Topic (transactional delta).

        Args:
            entity_id: The entity's unique identifier (Paper uses its DOI).
            topic_id: Target Topic ID.
            entity_label: Neo4j label of the source node (Problem,
                ProblemMention, ProblemConcept, or Paper).

        Returns:
            True if a new edge was created; False if the edge already existed.
        """
        if entity_label not in self._ASSIGN_RELATIONSHIPS:
            raise ValueError(
                f"Unsupported entity_label {entity_label!r}; "
                f"expected one of {sorted(self._ASSIGN_RELATIONSHIPS)}"
            )

        rel_type, count_field = self._ASSIGN_RELATIONSHIPS[entity_label]
        match_field = "doi" if entity_label == "Paper" else "id"

        def _assign(
            tx: ManagedTransaction,
            eid: str,
            tid: str,
        ) -> bool:
            # Detect whether the MERGE creates the edge so we only
            # increment the count once.
            query = f"""
            MATCH (e:{entity_label} {{{match_field}: $eid}})
            MATCH (t:Topic {{id: $tid}})
            OPTIONAL MATCH (e)-[existing:{rel_type}]->(t)
            WITH e, t, existing
            FOREACH (_ IN CASE WHEN existing IS NULL THEN [1] ELSE [] END |
                CREATE (e)-[:{rel_type}]->(t)
                SET t.{count_field} = t.{count_field} + 1,
                    t.updated_at = $now
            )
            RETURN existing IS NULL as created
            """
            result = tx.run(
                query,
                eid=eid,
                tid=tid,
                now=datetime.now(timezone.utc).isoformat(),
            )
            record = result.single()
            if record is None:
                raise NotFoundError(
                    f"Cannot assign: {entity_label} {eid!r} or Topic {tid!r} not found"
                )
            return bool(record["created"])

        with self.session() as session:
            created = self._execute_with_retry(session, _assign, entity_id, topic_id)

        logger.info(
            f"Assigned {entity_label} {entity_id} to Topic {topic_id} "
            f"(created={created})"
        )
        return created

    def unassign_entity_from_topic(
        self,
        entity_id: str,
        topic_id: str,
        entity_label: str,
    ) -> bool:
        """
        Remove a topic assignment edge and decrement the matching count.

        Returns True if an edge was removed, False if none existed.
        """
        if entity_label not in self._ASSIGN_RELATIONSHIPS:
            raise ValueError(
                f"Unsupported entity_label {entity_label!r}; "
                f"expected one of {sorted(self._ASSIGN_RELATIONSHIPS)}"
            )

        rel_type, count_field = self._ASSIGN_RELATIONSHIPS[entity_label]
        match_field = "doi" if entity_label == "Paper" else "id"

        def _unassign(
            tx: ManagedTransaction,
            eid: str,
            tid: str,
        ) -> bool:
            query = f"""
            MATCH (e:{entity_label} {{{match_field}: $eid}})
                  -[r:{rel_type}]->(t:Topic {{id: $tid}})
            DELETE r
            SET t.{count_field} = CASE
                WHEN t.{count_field} > 0 THEN t.{count_field} - 1
                ELSE 0
            END,
            t.updated_at = $now
            RETURN count(r) as removed
            """
            result = tx.run(
                query,
                eid=eid,
                tid=tid,
                now=datetime.now(timezone.utc).isoformat(),
            )
            record = result.single()
            return (record["removed"] if record else 0) > 0

        with self.session() as session:
            removed = self._execute_with_retry(session, _unassign, entity_id, topic_id)

        logger.info(
            f"Unassigned {entity_label} {entity_id} from Topic {topic_id} "
            f"(removed={removed})"
        )
        return removed

    def reconcile_topic_counts(self) -> list[dict]:
        """
        Recompute denormalized counts on every Topic from actual edges.

        Returns a list of {topic_id, name, problem_count, paper_count} for
        topics whose stored counts drifted from the recomputed values.
        Safe to run on a live graph — a single transactional pass.
        """
        def _reconcile(tx: ManagedTransaction) -> list[dict]:
            result = tx.run(
                """
                MATCH (t:Topic)
                OPTIONAL MATCH (t)<-[:BELONGS_TO]-(p)
                WHERE p:Problem OR p:ProblemMention OR p:ProblemConcept
                WITH t, count(DISTINCT p) AS pc
                OPTIONAL MATCH (t)<-[:RESEARCHES]-(paper:Paper)
                WITH t, pc, count(DISTINCT paper) AS pac
                WHERE t.problem_count <> pc OR t.paper_count <> pac
                SET t.problem_count = pc,
                    t.paper_count = pac,
                    t.updated_at = $now
                RETURN t.id as id, t.name as name,
                       pc as problem_count, pac as paper_count
                """,
                now=datetime.now(timezone.utc).isoformat(),
            )
            return [dict(r) for r in result]

        with self.session() as session:
            drift = session.execute_write(_reconcile)

        if drift:
            logger.info(f"Reconciled {len(drift)} Topic count drifts")
        else:
            logger.debug("Topic counts were already consistent")
        return drift

    # =========================================================================
    # ResearchConcept Operations (E-2)
    # =========================================================================

    DEFAULT_CONCEPT_DEDUP_THRESHOLD = 0.90

    def _research_concept_props(self, concept: ResearchConcept) -> dict:
        """Serialize a ResearchConcept for Neo4j (embedding included when set)."""
        props = concept.to_neo4j_properties()
        if concept.embedding is not None:
            props["embedding"] = concept.embedding
        return props

    def _research_concept_from_neo4j(self, data: dict) -> ResearchConcept:
        """Convert a Neo4j node record back into a ResearchConcept model."""
        aliases = data.get("aliases", [])
        if isinstance(aliases, str):
            aliases = json.loads(aliases)
        data["aliases"] = aliases
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        if isinstance(data.get("updated_at"), str):
            data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        # Drop any stray embedding — we never round-trip it through the model.
        data.pop("embedding", None)
        return ResearchConcept(**data)

    def create_research_concept(
        self,
        concept: ResearchConcept,
        generate_embedding: bool = True,
    ) -> ResearchConcept:
        """
        Create a new ResearchConcept node.

        Generates an embedding for ``{name}: {description}`` if
        ``generate_embedding`` is True and ``concept.embedding`` is None.
        Raises ``DuplicateError`` if a ResearchConcept with the same id
        already exists.
        """
        if generate_embedding and concept.embedding is None:
            try:
                from agentic_kg.knowledge_graph.embeddings import (
                    generate_research_concept_embedding,
                )
                concept.embedding = generate_research_concept_embedding(
                    concept.name, concept.description
                )
                logger.debug(f"Generated embedding for concept {concept.id}")
            except Exception as e:
                logger.warning(
                    f"Failed to generate embedding for concept {concept.id}: {e}. "
                    "Concept will be created without embedding."
                )

        def _create(tx: ManagedTransaction, props: dict) -> None:
            check = tx.run(
                "MATCH (rc:ResearchConcept {id: $id}) RETURN rc.id",
                id=props["id"],
            )
            if check.single():
                raise DuplicateError(
                    f"ResearchConcept with ID {props['id']} already exists"
                )
            tx.run(
                """
                CREATE (rc:ResearchConcept)
                SET rc = $props
                """,
                props=props,
            )

        props = self._research_concept_props(concept)

        with self.session() as session:
            self._execute_with_retry(session, _create, props)

        logger.info(f"Created research concept: {concept.id} ({concept.name})")
        return concept

    def get_research_concept(self, concept_id: str) -> ResearchConcept:
        """Fetch a ResearchConcept by ID. Raises NotFoundError when missing."""
        def _get(tx: ManagedTransaction, cid: str) -> Optional[dict]:
            result = tx.run(
                "MATCH (rc:ResearchConcept {id: $id}) RETURN rc",
                id=cid,
            )
            record = result.single()
            return dict(record["rc"]) if record else None

        with self.session() as session:
            data = session.execute_read(lambda tx: _get(tx, concept_id))

        if data is None:
            raise NotFoundError(f"ResearchConcept not found: {concept_id}")

        return self._research_concept_from_neo4j(data)

    def update_research_concept(
        self,
        concept_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        aliases: Optional[list[str]] = None,
        embedding: Optional[list[float]] = None,
        regenerate_embedding: bool = False,
    ) -> ResearchConcept:
        """
        Partial update of a ResearchConcept.

        Any field left as ``None`` is left untouched. When
        ``regenerate_embedding`` is True (and no explicit embedding was
        passed) the embedding is recomputed from the *current* persisted
        name + description (after any updates in this call).
        """
        existing = self.get_research_concept(concept_id)

        next_name = name if name is not None else existing.name
        next_description = (
            description if description is not None else existing.description
        )
        next_aliases = aliases if aliases is not None else existing.aliases

        if embedding is None and regenerate_embedding:
            try:
                from agentic_kg.knowledge_graph.embeddings import (
                    generate_research_concept_embedding,
                )
                embedding = generate_research_concept_embedding(
                    next_name, next_description
                )
            except Exception as e:
                logger.warning(
                    f"Failed to regenerate embedding for concept {concept_id}: {e}"
                )

        now = datetime.now(timezone.utc).isoformat()
        aliases_json = json.dumps(next_aliases)

        def _update(tx: ManagedTransaction) -> bool:
            query = """
            MATCH (rc:ResearchConcept {id: $id})
            SET rc.name = $name,
                rc.description = $description,
                rc.aliases = $aliases,
                rc.updated_at = $now
            """
            params = {
                "id": concept_id,
                "name": next_name,
                "description": next_description,
                "aliases": aliases_json,
                "now": now,
            }
            if embedding is not None:
                query += ", rc.embedding = $embedding"
                params["embedding"] = embedding
            query += " RETURN rc.id"
            result = tx.run(query, **params)
            return result.single() is not None

        with self.session() as session:
            found = self._execute_with_retry(session, lambda tx: _update(tx))

        if not found:
            raise NotFoundError(f"ResearchConcept not found: {concept_id}")

        logger.info(f"Updated research concept: {concept_id}")
        return self.get_research_concept(concept_id)

    def delete_research_concept(self, concept_id: str) -> bool:
        """Detach-delete a ResearchConcept. Raises NotFoundError when missing."""
        def _delete(tx: ManagedTransaction, cid: str) -> bool:
            result = tx.run(
                """
                MATCH (rc:ResearchConcept {id: $id})
                DETACH DELETE rc
                RETURN count(*) as deleted
                """,
                id=cid,
            )
            record = result.single()
            return record["deleted"] > 0 if record else False

        with self.session() as session:
            deleted = self._execute_with_retry(session, _delete, concept_id)

        if not deleted:
            raise NotFoundError(f"ResearchConcept not found: {concept_id}")

        logger.info(f"Deleted research concept: {concept_id}")
        return True

    def search_research_concepts_by_embedding(
        self,
        embedding: list[float],
        top_k: int = 10,
        min_score: Optional[float] = None,
    ) -> list[tuple[ResearchConcept, float]]:
        """
        Vector similarity search over the research_concept_embedding_idx.

        Returns (concept, score) pairs ordered by descending cosine
        similarity. If ``min_score`` is provided, results below the
        threshold are filtered out.
        """
        def _search(
            tx: ManagedTransaction,
            emb: list[float],
            lim: int,
            floor: Optional[float],
        ) -> list[dict]:
            query = """
            CALL db.index.vector.queryNodes(
                'research_concept_embedding_idx', $top_k, $embedding
            ) YIELD node, score
            """
            params: dict[str, Any] = {"embedding": emb, "top_k": lim}
            if floor is not None:
                query += "WHERE score >= $min_score\n"
                params["min_score"] = floor
            query += "RETURN node as rc, score ORDER BY score DESC"
            result = tx.run(query, **params)
            return [
                {"concept": dict(r["rc"]), "score": r["score"]}
                for r in result
            ]

        with self.session() as session:
            records = session.execute_read(
                lambda tx: _search(tx, embedding, top_k, min_score)
            )

        return [
            (self._research_concept_from_neo4j(r["concept"]), r["score"])
            for r in records
        ]

    def create_or_merge_research_concept(
        self,
        name: str,
        description: Optional[str] = None,
        aliases: Optional[list[str]] = None,
        threshold: Optional[float] = None,
        embedding: Optional[list[float]] = None,
        generate_description: bool = False,
        llm_client: Optional[Any] = None,
    ) -> tuple[ResearchConcept, bool]:
        """
        Embedding-based create-or-merge for ResearchConcepts.

        Embeds ``{name}: {description}``, searches the vector index, and
        if a candidate scores at or above ``threshold`` the incoming name
        and aliases are merged into the existing concept's alias list
        (existing concept returned). Otherwise a new concept is created.

        E-6: ``generate_description`` is only supported on the async
        sibling ``acreate_or_merge_research_concept``. Calling this sync
        method with ``generate_description=True`` raises
        ``NotImplementedError`` per the spec's QA Q2 review.

        Returns (concept, created) — ``created`` is True when a new node
        was inserted, False when an existing node was reused.
        """
        if generate_description:
            raise NotImplementedError(
                "generate_description=True requires the async sibling "
                "acreate_or_merge_research_concept. The sync method "
                "cannot safely run async LLM calls. "
                "See E-6 spec, AC-5 / QA Q2 review."
            )
        # llm_client is accepted but unused on the sync path so callers can
        # pass the same kwargs to either sibling without conditional code.
        _ = llm_client

        threshold = (
            threshold if threshold is not None else self.DEFAULT_CONCEPT_DEDUP_THRESHOLD
        )

        if embedding is None:
            try:
                from agentic_kg.knowledge_graph.embeddings import (
                    generate_research_concept_embedding,
                )
                embedding = generate_research_concept_embedding(name, description)
            except Exception as e:
                logger.warning(
                    f"Embedding failed for '{name}': {e}. "
                    "Falling back to create-without-embedding."
                )

        if embedding is not None:
            candidates = self.search_research_concepts_by_embedding(
                embedding=embedding,
                top_k=5,
                min_score=threshold,
            )
            if candidates:
                best, score = candidates[0]
                logger.info(
                    f"Dedup merge: '{name}' -> '{best.name}' (score={score:.3f})"
                )
                merged_aliases = set(best.aliases)
                merged_aliases.update(aliases or [])
                if name and name != best.name:
                    merged_aliases.add(name)
                # Prefer a richer description if the incoming call has one
                # and the existing concept does not.
                next_description = best.description or description
                self.update_research_concept(
                    best.id,
                    description=next_description,
                    aliases=sorted(merged_aliases),
                )
                return self.get_research_concept(best.id), False

        concept = ResearchConcept(
            name=name,
            description=description,
            aliases=list(aliases or []),
            embedding=embedding,
        )
        self.create_research_concept(concept, generate_embedding=False)
        return concept, True

    # Generalized link relationships (E-3 Tech Lead Q5 review).
    # Tuple shape: (source_label, source_id_field, target_label, target_count_field).
    # The target uses ``id`` as its key in every current relationship.
    _NODE_LINK_RELATIONSHIPS = {
        "INVOLVES_CONCEPT": ("ProblemConcept", "id", "ResearchConcept", "mention_count"),
        "DISCUSSES": ("Paper", "doi", "ResearchConcept", "paper_count"),
        "USES_MODEL": ("Paper", "doi", "Model", "usage_count"),
        "APPLIES_METHOD": ("Paper", "doi", "Method", "usage_count"),  # E-4
    }

    def _link_entity_to_node(
        self,
        entity_id: str,
        target_id: str,
        relationship: str,
    ) -> bool:
        """
        Generalized link helper: MERGE an edge (relationship) from a source
        entity to a target node, incrementing the target's denormalized
        count only when a new edge is created. Supersedes the E-2
        ``_link_entity_to_node`` helper.
        """
        if relationship not in self._NODE_LINK_RELATIONSHIPS:
            raise ValueError(
                f"Unsupported relationship {relationship!r}; "
                f"expected one of {sorted(self._NODE_LINK_RELATIONSHIPS)}"
            )
        src_label, src_field, target_label, count_field = (
            self._NODE_LINK_RELATIONSHIPS[relationship]
        )

        def _link(tx: ManagedTransaction, eid: str, cid: str) -> bool:
            query = f"""
            MATCH (src:{src_label} {{{src_field}: $eid}})
            MATCH (rc:{target_label} {{id: $cid}})
            OPTIONAL MATCH (src)-[existing:{relationship}]->(rc)
            WITH src, rc, existing
            FOREACH (_ IN CASE WHEN existing IS NULL THEN [1] ELSE [] END |
                CREATE (src)-[:{relationship}]->(rc)
                SET rc.{count_field} = rc.{count_field} + 1,
                    rc.updated_at = $now
            )
            RETURN existing IS NULL as created
            """
            result = tx.run(
                query,
                eid=eid,
                cid=cid,
                now=datetime.now(timezone.utc).isoformat(),
            )
            record = result.single()
            if record is None:
                raise NotFoundError(
                    f"Cannot link: {src_label} {eid!r} or "
                    f"{target_label} {cid!r} not found"
                )
            return bool(record["created"])

        with self.session() as session:
            return self._execute_with_retry(session, _link, entity_id, target_id)

    def _unlink_entity_from_node(
        self,
        entity_id: str,
        target_id: str,
        relationship: str,
    ) -> bool:
        if relationship not in self._NODE_LINK_RELATIONSHIPS:
            raise ValueError(
                f"Unsupported relationship {relationship!r}; "
                f"expected one of {sorted(self._NODE_LINK_RELATIONSHIPS)}"
            )
        src_label, src_field, target_label, count_field = (
            self._NODE_LINK_RELATIONSHIPS[relationship]
        )

        def _unlink(tx: ManagedTransaction, eid: str, cid: str) -> bool:
            query = f"""
            MATCH (src:{src_label} {{{src_field}: $eid}})
                  -[r:{relationship}]->(rc:{target_label} {{id: $cid}})
            DELETE r
            SET rc.{count_field} = CASE
                WHEN rc.{count_field} > 0 THEN rc.{count_field} - 1
                ELSE 0
            END,
            rc.updated_at = $now
            RETURN count(r) as removed
            """
            result = tx.run(
                query,
                eid=eid,
                cid=cid,
                now=datetime.now(timezone.utc).isoformat(),
            )
            record = result.single()
            return (record["removed"] if record else 0) > 0

        with self.session() as session:
            return self._execute_with_retry(session, _unlink, entity_id, target_id)

    def link_problem_to_concept(
        self, problem_concept_id: str, research_concept_id: str
    ) -> bool:
        """Link a ProblemConcept → ResearchConcept via INVOLVES_CONCEPT."""
        return self._link_entity_to_node(
            entity_id=problem_concept_id,
            target_id=research_concept_id,
            relationship="INVOLVES_CONCEPT",
        )

    def unlink_problem_from_concept(
        self, problem_concept_id: str, research_concept_id: str
    ) -> bool:
        """Remove a ProblemConcept → ResearchConcept INVOLVES_CONCEPT edge."""
        return self._unlink_entity_from_node(
            entity_id=problem_concept_id,
            target_id=research_concept_id,
            relationship="INVOLVES_CONCEPT",
        )

    def link_paper_to_concept(
        self, paper_doi: str, research_concept_id: str
    ) -> bool:
        """Link a Paper → ResearchConcept via DISCUSSES."""
        return self._link_entity_to_node(
            entity_id=paper_doi,
            target_id=research_concept_id,
            relationship="DISCUSSES",
        )

    def unlink_paper_from_concept(
        self, paper_doi: str, research_concept_id: str
    ) -> bool:
        """Remove a Paper → ResearchConcept DISCUSSES edge."""
        return self._unlink_entity_from_node(
            entity_id=paper_doi,
            target_id=research_concept_id,
            relationship="DISCUSSES",
        )

    def get_problems_for_concept(
        self, concept_id: str, limit: int = 50
    ) -> list[dict]:
        """
        Return ProblemConcept nodes linked to ``concept_id`` via
        INVOLVES_CONCEPT, ordered by mention_count descending.
        """
        def _fetch(tx: ManagedTransaction, cid: str, lim: int) -> list[dict]:
            result = tx.run(
                """
                MATCH (pc:ProblemConcept)-[:INVOLVES_CONCEPT]
                      ->(rc:ResearchConcept {id: $cid})
                RETURN pc
                ORDER BY pc.mention_count DESC
                LIMIT $limit
                """,
                cid=cid,
                limit=lim,
            )
            return [dict(r["pc"]) for r in result]

        with self.session() as session:
            return session.execute_read(
                lambda tx: _fetch(tx, concept_id, limit)
            )

    def get_papers_for_concept(
        self, concept_id: str, limit: int = 50
    ) -> list[dict]:
        """
        Return Paper nodes linked to ``concept_id`` via DISCUSSES, ordered
        by year descending (most recent first).
        """
        def _fetch(tx: ManagedTransaction, cid: str, lim: int) -> list[dict]:
            result = tx.run(
                """
                MATCH (p:Paper)-[:DISCUSSES]->(rc:ResearchConcept {id: $cid})
                RETURN p
                ORDER BY coalesce(p.year, 0) DESC
                LIMIT $limit
                """,
                cid=cid,
                limit=lim,
            )
            return [dict(r["p"]) for r in result]

        with self.session() as session:
            return session.execute_read(
                lambda tx: _fetch(tx, concept_id, limit)
            )

    def reconcile_research_concept_counts(self) -> list[dict]:
        """
        Recompute denormalized counts on every ResearchConcept from actual
        edges. Returns a list of {id, name, mention_count, paper_count}
        for concepts whose stored counts drifted from the recomputed ones.
        """
        def _reconcile(tx: ManagedTransaction) -> list[dict]:
            result = tx.run(
                """
                MATCH (rc:ResearchConcept)
                OPTIONAL MATCH (rc)<-[:INVOLVES_CONCEPT]-(pc:ProblemConcept)
                WITH rc, count(DISTINCT pc) AS mc
                OPTIONAL MATCH (rc)<-[:DISCUSSES]-(paper:Paper)
                WITH rc, mc, count(DISTINCT paper) AS pac
                WHERE rc.mention_count <> mc OR rc.paper_count <> pac
                SET rc.mention_count = mc,
                    rc.paper_count = pac,
                    rc.updated_at = $now
                RETURN rc.id as id, rc.name as name,
                       mc as mention_count, pac as paper_count
                """,
                now=datetime.now(timezone.utc).isoformat(),
            )
            return [dict(r) for r in result]

        with self.session() as session:
            drift = session.execute_write(_reconcile)

        if drift:
            logger.info(f"Reconciled {len(drift)} ResearchConcept count drifts")
        else:
            logger.debug("ResearchConcept counts were already consistent")
        return drift


    # =========================================================================
    # Model Operations (E-3)
    # =========================================================================

    DEFAULT_MODEL_DEDUP_THRESHOLD = 0.95

    def _model_props(self, model: Model) -> dict:
        """Serialize a Model for Neo4j storage (embedding included when set)."""
        props = model.to_neo4j_properties()
        if model.embedding is not None:
            props["embedding"] = model.embedding
        return props

    def _model_from_neo4j(self, data: dict) -> Model:
        """Hydrate a Model from a Neo4j node dict (aliases JSON-decoded)."""
        from datetime import datetime as _datetime

        aliases = data.get("aliases")
        if isinstance(aliases, str):
            aliases = json.loads(aliases) if aliases else []
        elif aliases is None:
            aliases = []

        return Model(
            id=data["id"],
            name=data["name"],
            description=data.get("description"),
            aliases=aliases,
            architecture=data.get("architecture"),
            model_type=data.get("model_type"),
            year_introduced=data.get("year_introduced"),
            introducing_paper_doi=data.get("introducing_paper_doi"),
            is_canonical=bool(data.get("is_canonical", False)),
            embedding=data.get("embedding"),
            usage_count=int(data.get("usage_count", 0) or 0),
            created_at=_datetime.fromisoformat(data["created_at"]),
            updated_at=_datetime.fromisoformat(data["updated_at"]),
        )

    def create_model(
        self,
        model: Model,
        generate_embedding: bool = True,
    ) -> Model:
        """Create a new Model node. Raises DuplicateError on id collision."""
        if generate_embedding and model.embedding is None:
            try:
                from agentic_kg.knowledge_graph.embeddings import (
                    generate_model_embedding,
                )
                model.embedding = generate_model_embedding(
                    model.name, model.description
                )
            except Exception as e:
                logger.warning(
                    f"Failed to generate embedding for model {model.id}: {e}. "
                    "Model will be created without embedding."
                )

        def _create(tx: ManagedTransaction, props: dict) -> None:
            check = tx.run(
                "MATCH (m:Model {id: $id}) RETURN m.id",
                id=props["id"],
            )
            if check.single():
                raise DuplicateError(
                    f"Model with ID {props['id']} already exists"
                )
            tx.run(
                """
                CREATE (m:Model)
                SET m = $props
                """,
                props=props,
            )

        props = self._model_props(model)

        with self.session() as session:
            self._execute_with_retry(session, _create, props)

        logger.info(f"Created model: {model.id} ({model.name})")
        return model

    def get_model(self, model_id: str) -> Model:
        """Fetch a Model by ID. Raises NotFoundError when missing."""
        def _get(tx: ManagedTransaction, mid: str) -> Optional[dict]:
            result = tx.run(
                "MATCH (m:Model {id: $id}) RETURN m",
                id=mid,
            )
            record = result.single()
            return dict(record["m"]) if record else None

        with self.session() as session:
            data = session.execute_read(lambda tx: _get(tx, model_id))

        if data is None:
            raise NotFoundError(f"Model not found: {model_id}")

        return self._model_from_neo4j(data)

    def get_model_by_name(self, name: str) -> Model:
        """Fetch a Model by name. When multiple Models share a name, the
        canonical one wins; otherwise the alphabetically-first id breaks
        the tie deterministically. Raises NotFoundError when none match.
        """
        def _get(tx: ManagedTransaction, nm: str) -> Optional[dict]:
            result = tx.run(
                """
                MATCH (m:Model {name: $name})
                RETURN m
                ORDER BY m.is_canonical DESC, m.id
                LIMIT 1
                """,
                name=nm,
            )
            record = result.single()
            return dict(record["m"]) if record else None

        with self.session() as session:
            data = session.execute_read(lambda tx: _get(tx, name))

        if data is None:
            raise NotFoundError(f"Model not found: {name}")

        return self._model_from_neo4j(data)

    def update_model(
        self,
        model_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        aliases: Optional[list[str]] = None,
        architecture: Optional[str] = None,
        model_type: Optional[str] = None,
        year_introduced: Optional[int] = None,
        introducing_paper_doi: Optional[str] = None,
        is_canonical: Optional[bool] = None,
        embedding: Optional[list[float]] = None,
        regenerate_embedding: bool = False,
    ) -> Model:
        """Partial update of a Model. None-valued kwargs leave fields untouched."""
        existing = self.get_model(model_id)

        next_name = name if name is not None else existing.name
        next_description = (
            description if description is not None else existing.description
        )
        next_aliases = aliases if aliases is not None else existing.aliases
        next_architecture = (
            architecture if architecture is not None else existing.architecture
        )
        next_model_type = (
            model_type if model_type is not None else existing.model_type
        )
        next_year = (
            year_introduced
            if year_introduced is not None
            else existing.year_introduced
        )
        next_doi = (
            introducing_paper_doi
            if introducing_paper_doi is not None
            else existing.introducing_paper_doi
        )
        next_canonical = (
            is_canonical if is_canonical is not None else existing.is_canonical
        )

        if embedding is None and regenerate_embedding:
            try:
                from agentic_kg.knowledge_graph.embeddings import (
                    generate_model_embedding,
                )
                embedding = generate_model_embedding(next_name, next_description)
            except Exception as e:
                logger.warning(
                    f"Failed to regenerate embedding for model {model_id}: {e}"
                )

        now = datetime.now(timezone.utc).isoformat()
        aliases_json = json.dumps(next_aliases)

        def _update(tx: ManagedTransaction) -> bool:
            query = """
            MATCH (m:Model {id: $id})
            SET m.name = $name,
                m.description = $description,
                m.aliases = $aliases,
                m.architecture = $architecture,
                m.model_type = $model_type,
                m.year_introduced = $year_introduced,
                m.introducing_paper_doi = $introducing_paper_doi,
                m.is_canonical = $is_canonical,
                m.updated_at = $now
            """
            params = {
                "id": model_id,
                "name": next_name,
                "description": next_description,
                "aliases": aliases_json,
                "architecture": next_architecture,
                "model_type": next_model_type,
                "year_introduced": next_year,
                "introducing_paper_doi": next_doi,
                "is_canonical": next_canonical,
                "now": now,
            }
            if embedding is not None:
                query += ", m.embedding = $embedding"
                params["embedding"] = embedding
            query += " RETURN m.id"
            result = tx.run(query, **params)
            return result.single() is not None

        with self.session() as session:
            found = self._execute_with_retry(session, lambda tx: _update(tx))

        if not found:
            raise NotFoundError(f"Model not found: {model_id}")

        logger.info(f"Updated model: {model_id}")
        return self.get_model(model_id)

    def delete_model(self, model_id: str, force: bool = False) -> bool:
        """DETACH DELETE a Model. Refuses canonical Models unless force=True.

        QA Q4 review decision: rebuild-over-migrate — the node and all
        incident USES_MODEL edges are removed in one shot, no audit log.
        Re-extraction recreates the edge structure if needed.
        """
        existing = self.get_model(model_id)
        if existing.is_canonical and not force:
            raise ValueError(
                f"Refusing to delete canonical Model {model_id!r} ({existing.name!r}). "
                "Pass force=True to override."
            )

        def _delete(tx: ManagedTransaction, mid: str) -> bool:
            result = tx.run(
                """
                MATCH (m:Model {id: $id})
                DETACH DELETE m
                RETURN count(*) as deleted
                """,
                id=mid,
            )
            record = result.single()
            return record["deleted"] > 0 if record else False

        with self.session() as session:
            deleted = self._execute_with_retry(session, _delete, model_id)

        if not deleted:
            raise NotFoundError(f"Model not found: {model_id}")

        logger.info(f"Deleted model: {model_id}")
        return True

    def search_models_by_embedding(
        self,
        embedding: list[float],
        top_k: int = 10,
        min_score: Optional[float] = None,
    ) -> list[tuple[Model, float]]:
        """Vector similarity search over the model_embedding_idx."""
        def _search(
            tx: ManagedTransaction,
            emb: list[float],
            lim: int,
            floor: Optional[float],
        ) -> list[dict]:
            query = """
            CALL db.index.vector.queryNodes(
                'model_embedding_idx', $top_k, $embedding
            ) YIELD node, score
            """
            params: dict[str, Any] = {"embedding": emb, "top_k": lim}
            if floor is not None:
                query += "WHERE score >= $min_score\n"
                params["min_score"] = floor
            query += "RETURN node as m, score ORDER BY score DESC"
            result = tx.run(query, **params)
            return [
                {"model": dict(r["m"]), "score": r["score"]}
                for r in result
            ]

        with self.session() as session:
            records = session.execute_read(
                lambda tx: _search(tx, embedding, top_k, min_score)
            )

        return [
            (self._model_from_neo4j(r["model"]), r["score"]) for r in records
        ]

    def link_paper_to_model(
        self, paper_doi: str, model_id: str
    ) -> bool:
        """Link a Paper → Model via USES_MODEL (idempotent MERGE)."""
        return self._link_entity_to_node(
            entity_id=paper_doi,
            target_id=model_id,
            relationship="USES_MODEL",
        )

    def unlink_paper_from_model(
        self, paper_doi: str, model_id: str
    ) -> bool:
        """Remove a Paper → Model USES_MODEL edge (decrements usage_count)."""
        return self._unlink_entity_from_node(
            entity_id=paper_doi,
            target_id=model_id,
            relationship="USES_MODEL",
        )

    def get_papers_for_model(
        self, model_id: str, limit: int = 50
    ) -> list[dict]:
        """Return Paper rows linked to ``model_id`` via USES_MODEL."""
        def _fetch(tx: ManagedTransaction, mid: str, lim: int) -> list[dict]:
            result = tx.run(
                """
                MATCH (p:Paper)-[:USES_MODEL]->(m:Model {id: $mid})
                RETURN p
                ORDER BY p.title
                LIMIT $limit
                """,
                mid=mid,
                limit=lim,
            )
            return [dict(r["p"]) for r in result]

        with self.session() as session:
            return session.execute_read(
                lambda tx: _fetch(tx, model_id, limit)
            )

    def create_or_merge_model(
        self,
        name: str,
        description: Optional[str] = None,
        aliases: Optional[list[str]] = None,
        architecture: Optional[str] = None,
        model_type: Optional[str] = None,
        year_introduced: Optional[int] = None,
        introducing_paper_doi: Optional[str] = None,
        is_canonical: bool = False,
        threshold: Optional[float] = None,
        embedding: Optional[list[float]] = None,
        generate_description: bool = False,
        llm_client: Optional[Any] = None,
    ) -> tuple[Model, bool]:
        """Embedding-based create-or-merge with canonical protection.

        E-6: ``generate_description=True`` is only supported on the async
        sibling ``acreate_or_merge_model`` and raises ``NotImplementedError``
        here (spec QA Q2 review).

        Decision matrix when an existing candidate scores ≥ threshold:

        - existing canonical, incoming non-canonical → preserve canonical
          name, add incoming name to aliases, keep is_canonical=True.
        - existing non-canonical, incoming canonical → promote: name
          overwritten with the incoming canonical name, prior name moves
          to aliases, is_canonical flipped to True. usage_count survives
          the rename (no edge changes).
        - both canonical → idempotent (no rename); aliases merge.
        - both non-canonical → standard E-2-style alias merge (existing
          name wins).

        On embedding service failure: falls back to create-without-embedding,
        dedup is skipped, new node carries embedding=None. Logged at WARN
        (AC-13).

        Returns ``(model, created)`` — created=True for new nodes.
        """
        if generate_description:
            raise NotImplementedError(
                "generate_description=True requires the async sibling "
                "acreate_or_merge_model. The sync method cannot safely "
                "run async LLM calls. See E-6 spec, AC-5 / QA Q2 review."
            )
        _ = llm_client  # accepted for kwarg parity with the async sibling

        threshold = (
            threshold
            if threshold is not None
            else self.DEFAULT_MODEL_DEDUP_THRESHOLD
        )

        if embedding is None:
            try:
                from agentic_kg.knowledge_graph.embeddings import (
                    generate_model_embedding,
                )
                embedding = generate_model_embedding(name, description)
            except Exception as e:
                logger.warning(
                    f"Embedding failed for model '{name}': {e}. "
                    "Falling back to create-without-embedding (dedup skipped)."
                )

        if embedding is not None:
            candidates = self.search_models_by_embedding(
                embedding=embedding,
                top_k=5,
                min_score=threshold,
            )
            if candidates:
                best, score = candidates[0]
                logger.info(
                    f"Model dedup: '{name}' -> '{best.name}' (score={score:.3f})"
                )

                # Spec edge-case: incoming canonical entry merges into another
                # canonical entry. The curator probably intended two distinct
                # nodes — log a WARN so seed-load review catches it.
                if is_canonical and best.is_canonical and best.name != name:
                    logger.warning(
                        "Canonical-canonical merge: seed entry %r merged into "
                        "existing canonical %r (score=%.3f). The curator "
                        "likely intended two distinct nodes; consider "
                        "renaming one in the seed YAML.",
                        name,
                        best.name,
                        score,
                    )

                # Canonical-protection rules.
                if is_canonical and not best.is_canonical:
                    # Promote existing → canonical. Prior name moves to aliases.
                    next_name = name
                    next_aliases = sorted(
                        set(best.aliases)
                        | set(aliases or [])
                        | ({best.name} if best.name != name else set())
                    )
                    next_canonical = True
                else:
                    # Either existing canonical wins, OR both non-canonical
                    # (existing wins). Incoming name appended as alias.
                    next_name = best.name
                    next_aliases = sorted(
                        set(best.aliases)
                        | set(aliases or [])
                        | ({name} if name != best.name else set())
                    )
                    next_canonical = best.is_canonical or is_canonical

                self.update_model(
                    best.id,
                    name=next_name,
                    aliases=next_aliases,
                    description=best.description or description,
                    architecture=best.architecture or architecture,
                    model_type=best.model_type or model_type,
                    year_introduced=(
                        best.year_introduced
                        if best.year_introduced is not None
                        else year_introduced
                    ),
                    introducing_paper_doi=(
                        best.introducing_paper_doi or introducing_paper_doi
                    ),
                    is_canonical=next_canonical,
                )
                return self.get_model(best.id), False

        model = Model(
            name=name,
            description=description,
            aliases=list(aliases or []),
            architecture=architecture,
            model_type=model_type,
            year_introduced=year_introduced,
            introducing_paper_doi=introducing_paper_doi,
            is_canonical=is_canonical,
            embedding=embedding,
        )
        self.create_model(model, generate_embedding=False)
        return model, True


    # =========================================================================
    # Method Operations (E-4)
    # =========================================================================

    DEFAULT_METHOD_DEDUP_THRESHOLD = 0.90  # E-2 baseline; not E-3's 0.95

    def _method_props(self, method: Method) -> dict:
        """Serialize a Method for Neo4j storage (embedding included when set)."""
        props = method.to_neo4j_properties()
        if method.embedding is not None:
            props["embedding"] = method.embedding
        return props

    def _method_from_neo4j(self, data: dict) -> Method:
        """Hydrate a Method from a Neo4j node dict (aliases JSON-decoded)."""
        from datetime import datetime as _datetime

        aliases = data.get("aliases")
        if isinstance(aliases, str):
            aliases = json.loads(aliases) if aliases else []
        elif aliases is None:
            aliases = []

        return Method(
            id=data["id"],
            name=data["name"],
            description=data.get("description"),
            aliases=aliases,
            method_type=data.get("method_type"),
            embedding=data.get("embedding"),
            usage_count=int(data.get("usage_count", 0) or 0),
            created_at=_datetime.fromisoformat(data["created_at"]),
            updated_at=_datetime.fromisoformat(data["updated_at"]),
        )

    def create_method(
        self,
        method: Method,
        generate_embedding: bool = True,
    ) -> Method:
        """Create a new Method node. Raises DuplicateError on id collision."""
        if generate_embedding and method.embedding is None:
            try:
                from agentic_kg.knowledge_graph.embeddings import (
                    generate_method_embedding,
                )
                method.embedding = generate_method_embedding(
                    method.name, method.description
                )
            except Exception as e:
                logger.warning(
                    f"Failed to generate embedding for method {method.id}: {e}. "
                    "Method will be created without embedding."
                )

        def _create(tx: ManagedTransaction, props: dict) -> None:
            check = tx.run(
                "MATCH (m:Method {id: $id}) RETURN m.id",
                id=props["id"],
            )
            if check.single():
                raise DuplicateError(
                    f"Method with ID {props['id']} already exists"
                )
            tx.run(
                """
                CREATE (m:Method)
                SET m = $props
                """,
                props=props,
            )

        props = self._method_props(method)

        with self.session() as session:
            self._execute_with_retry(session, _create, props)

        logger.info(f"Created method: {method.id} ({method.name})")
        return method

    def get_method(self, method_id: str) -> Method:
        """Fetch a Method by ID. Raises NotFoundError when missing."""
        def _get(tx: ManagedTransaction, mid: str) -> Optional[dict]:
            result = tx.run(
                "MATCH (m:Method {id: $id}) RETURN m",
                id=mid,
            )
            record = result.single()
            return dict(record["m"]) if record else None

        with self.session() as session:
            data = session.execute_read(lambda tx: _get(tx, method_id))

        if data is None:
            raise NotFoundError(f"Method not found: {method_id}")

        return self._method_from_neo4j(data)

    def get_method_by_name(self, name: str) -> Method:
        """Fetch a Method by name. Deterministic tie-break on alphabetical id
        when multiple Methods share the name (rare; only happens when dedup
        was skipped during an embedding-service outage). No canonical
        preference — E-4 has no is_canonical field.
        """
        def _get(tx: ManagedTransaction, nm: str) -> Optional[dict]:
            result = tx.run(
                """
                MATCH (m:Method {name: $name})
                RETURN m
                ORDER BY m.id
                LIMIT 1
                """,
                name=nm,
            )
            record = result.single()
            return dict(record["m"]) if record else None

        with self.session() as session:
            data = session.execute_read(lambda tx: _get(tx, name))

        if data is None:
            raise NotFoundError(f"Method not found: {name}")

        return self._method_from_neo4j(data)

    def update_method(
        self,
        method_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        aliases: Optional[list[str]] = None,
        method_type: Optional[str] = None,
        embedding: Optional[list[float]] = None,
        regenerate_embedding: bool = False,
    ) -> Method:
        """Partial update of a Method. None-valued kwargs leave fields untouched."""
        existing = self.get_method(method_id)

        next_name = name if name is not None else existing.name
        next_description = (
            description if description is not None else existing.description
        )
        next_aliases = aliases if aliases is not None else existing.aliases
        next_method_type = (
            method_type if method_type is not None else existing.method_type
        )

        if embedding is None and regenerate_embedding:
            try:
                from agentic_kg.knowledge_graph.embeddings import (
                    generate_method_embedding,
                )
                embedding = generate_method_embedding(next_name, next_description)
            except Exception as e:
                logger.warning(
                    f"Failed to regenerate embedding for method {method_id}: {e}"
                )

        now = datetime.now(timezone.utc).isoformat()
        aliases_json = json.dumps(next_aliases)

        def _update(tx: ManagedTransaction) -> bool:
            query = """
            MATCH (m:Method {id: $id})
            SET m.name = $name,
                m.description = $description,
                m.aliases = $aliases,
                m.method_type = $method_type,
                m.updated_at = $now
            """
            params = {
                "id": method_id,
                "name": next_name,
                "description": next_description,
                "aliases": aliases_json,
                "method_type": next_method_type,
                "now": now,
            }
            if embedding is not None:
                query += ", m.embedding = $embedding"
                params["embedding"] = embedding
            query += " RETURN m.id"
            result = tx.run(query, **params)
            return result.single() is not None

        with self.session() as session:
            found = self._execute_with_retry(session, lambda tx: _update(tx))

        if not found:  # pragma: no cover
            # Defensive: get_method above already raised NotFoundError if
            # the row was missing at the start of update_method. This path
            # only triggers under a TOCTOU race where the node is deleted
            # between get_method and _update — not reproducible in tests
            # without mocking the inner Cypher transaction.
            raise NotFoundError(f"Method not found: {method_id}")

        logger.info(f"Updated method: {method_id}")
        return self.get_method(method_id)

    def delete_method(self, method_id: str) -> bool:
        """DETACH DELETE a Method. No force flag — Method has no
        is_canonical to protect. Same rebuild-over-migrate ethos as E-3.
        """
        def _delete(tx: ManagedTransaction, mid: str) -> bool:
            result = tx.run(
                """
                MATCH (m:Method {id: $id})
                DETACH DELETE m
                RETURN count(*) as deleted
                """,
                id=mid,
            )
            record = result.single()
            return record["deleted"] > 0 if record else False

        with self.session() as session:
            deleted = self._execute_with_retry(session, _delete, method_id)

        if not deleted:
            raise NotFoundError(f"Method not found: {method_id}")

        logger.info(f"Deleted method: {method_id}")
        return True

    def search_methods_by_embedding(
        self,
        embedding: list[float],
        top_k: int = 10,
        min_score: Optional[float] = None,
    ) -> list[tuple[Method, float]]:
        """Vector similarity search over the method_embedding_idx."""
        def _search(
            tx: ManagedTransaction,
            emb: list[float],
            lim: int,
            floor: Optional[float],
        ) -> list[dict]:
            query = """
            CALL db.index.vector.queryNodes(
                'method_embedding_idx', $top_k, $embedding
            ) YIELD node, score
            """
            params: dict[str, Any] = {"embedding": emb, "top_k": lim}
            if floor is not None:
                query += "WHERE score >= $min_score\n"
                params["min_score"] = floor
            query += "RETURN node as m, score ORDER BY score DESC"
            result = tx.run(query, **params)
            return [
                {"method": dict(r["m"]), "score": r["score"]}
                for r in result
            ]

        with self.session() as session:
            records = session.execute_read(
                lambda tx: _search(tx, embedding, top_k, min_score)
            )

        return [
            (self._method_from_neo4j(r["method"]), r["score"]) for r in records
        ]

    def link_paper_to_method(
        self, paper_doi: str, method_id: str
    ) -> bool:
        """Link a Paper → Method via APPLIES_METHOD (idempotent MERGE)."""
        return self._link_entity_to_node(
            entity_id=paper_doi,
            target_id=method_id,
            relationship="APPLIES_METHOD",
        )

    def unlink_paper_from_method(
        self, paper_doi: str, method_id: str
    ) -> bool:
        """Remove a Paper → Method APPLIES_METHOD edge (decrements usage_count)."""
        return self._unlink_entity_from_node(
            entity_id=paper_doi,
            target_id=method_id,
            relationship="APPLIES_METHOD",
        )

    def get_papers_for_method(
        self, method_id: str, limit: int = 50
    ) -> list[dict]:
        """Return Paper rows linked to ``method_id`` via APPLIES_METHOD."""
        def _fetch(tx: ManagedTransaction, mid: str, lim: int) -> list[dict]:
            result = tx.run(
                """
                MATCH (p:Paper)-[:APPLIES_METHOD]->(m:Method {id: $mid})
                RETURN p
                ORDER BY p.title
                LIMIT $limit
                """,
                mid=mid,
                limit=lim,
            )
            return [dict(r["p"]) for r in result]

        with self.session() as session:
            return session.execute_read(
                lambda tx: _fetch(tx, method_id, limit)
            )

    def create_or_merge_method(
        self,
        name: str,
        description: Optional[str] = None,
        aliases: Optional[list[str]] = None,
        method_type: Optional[str] = None,
        threshold: Optional[float] = None,
        embedding: Optional[list[float]] = None,
        generate_description: bool = False,
        llm_client: Optional[Any] = None,
    ) -> tuple[Method, bool]:
        """Embedding-dedup'd create — E-2 ResearchConcept shape.

        Standard alias merge: existing name wins, incoming joins the
        aliases set. Description and method_type fill from incoming only
        when existing is None. **No canonical protection**, no force
        flags — Method has no is_canonical field.

        E-4 QA Q2 review: passing ``threshold=1.01`` forces the dedup
        search to return no matches (cosine ≤ 1.0), so a new node is
        always created. This is the operator escape valve for unwanted-
        merge scenarios.

        E-6 QA Q2 review: ``generate_description=True`` is only
        supported on the async sibling ``acreate_or_merge_method`` and
        raises ``NotImplementedError`` here.

        On embedding service failure: falls back to create-without-
        embedding, logs WARN, dedup is skipped (AC-12).
        """
        if generate_description:
            raise NotImplementedError(
                "generate_description=True requires the async sibling "
                "acreate_or_merge_method. The sync method cannot safely "
                "run async LLM calls. See E-6 spec, AC-5 / QA Q2 review."
            )
        _ = llm_client  # accepted for kwarg parity with the async sibling

        threshold = (
            threshold
            if threshold is not None
            else self.DEFAULT_METHOD_DEDUP_THRESHOLD
        )

        if embedding is None:
            try:
                from agentic_kg.knowledge_graph.embeddings import (
                    generate_method_embedding,
                )
                embedding = generate_method_embedding(name, description)
            except Exception as e:
                logger.warning(
                    f"Embedding failed for method '{name}': {e}. "
                    "Falling back to create-without-embedding (dedup skipped)."
                )

        if embedding is not None:
            candidates = self.search_methods_by_embedding(
                embedding=embedding,
                top_k=5,
                min_score=threshold,
            )
            if candidates:
                best, score = candidates[0]
                logger.info(
                    f"Method dedup: '{name}' -> '{best.name}' (score={score:.3f})"
                )
                merged_aliases = sorted(
                    set(best.aliases)
                    | set(aliases or [])
                    | ({name} if name != best.name else set())
                )
                self.update_method(
                    best.id,
                    aliases=merged_aliases,
                    description=best.description or description,
                    method_type=best.method_type or method_type,
                )
                return self.get_method(best.id), False

        method = Method(
            name=name,
            description=description,
            aliases=list(aliases or []),
            method_type=method_type,
            embedding=embedding,
        )
        self.create_method(method, generate_embedding=False)
        return method, True


    # =========================================================================
    # E-6 Async siblings — create_or_merge_X with generate_description support
    # =========================================================================

    async def _aresolve_description(
        self,
        *,
        entity_type: str,
        name: str,
        description: Optional[str],
        aliases: Optional[list[str]],
        generate_description: bool,
        llm_client: Optional[Any],
    ) -> Optional[str]:
        """Resolve the final description value for an async-create call.

        - Explicit description wins (no LLM call).
        - generate_description=False or no llm_client → return description unchanged.
        - Otherwise: invoke the LLM helper; return its result (which may be
          None on validation rejection / LLM failure).
        """
        if description is not None and description != "":
            return description
        if not generate_description:
            return description
        if llm_client is None:
            logger.warning(
                "%s description generation requested but no llm_client "
                "provided for %r; proceeding without description.",
                entity_type, name,
            )
            return description

        from agentic_kg.knowledge_graph.description_generation import (
            generate_description_with_self_check,
        )
        generated = await generate_description_with_self_check(
            entity_type=entity_type,  # type: ignore[arg-type]
            name=name,
            aliases=list(aliases or []),
            llm_client=llm_client,
        )
        # Either the validated description, or None if rejected/failed.
        return generated

    async def acreate_or_merge_research_concept(
        self,
        name: str,
        description: Optional[str] = None,
        aliases: Optional[list[str]] = None,
        threshold: Optional[float] = None,
        embedding: Optional[list[float]] = None,
        generate_description: bool = False,
        llm_client: Optional[Any] = None,
    ) -> tuple[ResearchConcept, bool]:
        """Async sibling supporting LLM description generation (E-6)."""
        description = await self._aresolve_description(
            entity_type="concept",
            name=name,
            description=description,
            aliases=aliases,
            generate_description=generate_description,
            llm_client=llm_client,
        )
        return self.create_or_merge_research_concept(
            name=name,
            description=description,
            aliases=aliases,
            threshold=threshold,
            embedding=embedding,
            generate_description=False,
        )

    async def acreate_or_merge_model(
        self,
        name: str,
        description: Optional[str] = None,
        aliases: Optional[list[str]] = None,
        architecture: Optional[str] = None,
        model_type: Optional[str] = None,
        year_introduced: Optional[int] = None,
        introducing_paper_doi: Optional[str] = None,
        is_canonical: bool = False,
        threshold: Optional[float] = None,
        embedding: Optional[list[float]] = None,
        generate_description: bool = False,
        llm_client: Optional[Any] = None,
    ) -> tuple[Model, bool]:
        """Async sibling supporting LLM description generation (E-6)."""
        description = await self._aresolve_description(
            entity_type="model",
            name=name,
            description=description,
            aliases=aliases,
            generate_description=generate_description,
            llm_client=llm_client,
        )
        return self.create_or_merge_model(
            name=name,
            description=description,
            aliases=aliases,
            architecture=architecture,
            model_type=model_type,
            year_introduced=year_introduced,
            introducing_paper_doi=introducing_paper_doi,
            is_canonical=is_canonical,
            threshold=threshold,
            embedding=embedding,
            generate_description=False,
        )

    async def acreate_or_merge_method(
        self,
        name: str,
        description: Optional[str] = None,
        aliases: Optional[list[str]] = None,
        method_type: Optional[str] = None,
        threshold: Optional[float] = None,
        embedding: Optional[list[float]] = None,
        generate_description: bool = False,
        llm_client: Optional[Any] = None,
    ) -> tuple[Method, bool]:
        """Async sibling supporting LLM description generation (E-6)."""
        description = await self._aresolve_description(
            entity_type="method",
            name=name,
            description=description,
            aliases=aliases,
            generate_description=generate_description,
            llm_client=llm_client,
        )
        return self.create_or_merge_method(
            name=name,
            description=description,
            aliases=aliases,
            method_type=method_type,
            threshold=threshold,
            embedding=embedding,
            generate_description=False,
        )

    # =========================================================================
    # Citation Graph (E-5)
    # =========================================================================

    def link_paper_cites_paper(
        self, source_doi: str, target_doi: str,
    ) -> bool:
        """Create a Paper-CITES->Paper edge. Idempotent. Increments
        source.reference_count and target.citation_count atomically when
        the edge is new.

        Self-citation (source == target) is allowed per spec edge case.

        Raises NotFoundError when either Paper is missing.
        """
        def _link(tx: ManagedTransaction, s_doi: str, t_doi: str) -> bool:
            result = tx.run(
                """
                MATCH (src:Paper {doi: $s_doi})
                MATCH (tgt:Paper {doi: $t_doi})
                OPTIONAL MATCH (src)-[existing:CITES]->(tgt)
                WITH src, tgt, existing
                FOREACH (_ IN CASE WHEN existing IS NULL THEN [1] ELSE [] END |
                    CREATE (src)-[:CITES]->(tgt)
                    SET src.reference_count = coalesce(src.reference_count, 0) + 1,
                        tgt.citation_count   = coalesce(tgt.citation_count, 0) + 1
                )
                RETURN existing IS NULL AS created
                """,
                s_doi=s_doi, t_doi=t_doi,
            )
            record = result.single()
            if record is None:
                raise NotFoundError(
                    f"Cannot link CITES: source Paper {s_doi!r} or "
                    f"target Paper {t_doi!r} not found"
                )
            return bool(record["created"])

        with self.session() as session:
            return self._execute_with_retry(
                session, _link, source_doi, target_doi,
            )

    def unlink_paper_cites_paper(
        self, source_doi: str, target_doi: str,
    ) -> bool:
        """Remove a Paper-CITES->Paper edge. Decrements both counters
        (clamped at 0). Returns True if an edge was removed, False if
        no edge existed."""
        def _unlink(tx: ManagedTransaction, s_doi: str, t_doi: str) -> bool:
            result = tx.run(
                """
                MATCH (src:Paper {doi: $s_doi})
                      -[r:CITES]->
                      (tgt:Paper {doi: $t_doi})
                DELETE r
                SET src.reference_count = CASE
                    WHEN coalesce(src.reference_count, 0) > 0
                    THEN src.reference_count - 1 ELSE 0 END,
                    tgt.citation_count = CASE
                    WHEN coalesce(tgt.citation_count, 0) > 0
                    THEN tgt.citation_count - 1 ELSE 0 END
                RETURN count(r) AS removed
                """,
                s_doi=s_doi, t_doi=t_doi,
            )
            record = result.single()
            return (record["removed"] if record else 0) > 0

        with self.session() as session:
            return self._execute_with_retry(
                session, _unlink, source_doi, target_doi,
            )

    def create_or_promote_paper_stub(
        self,
        doi: str,
        title: str,
        year: Optional[int] = None,
    ) -> tuple[Paper, bool]:
        """Idempotent Paper-stub creator (E-5 AC-4).

        - If a Paper exists with this DOI (stub or full), returns it unchanged.
          `created=False`. **The existing title is NOT overwritten** — that is
          the role of the promotion path via PaperImporter.
        - If no Paper exists, creates a stub with is_stub=True. `created=True`.
        """
        try:
            existing = self.get_paper(doi)
            return existing, False
        except NotFoundError:
            pass

        stub = Paper(
            doi=doi, title=title, year=year, is_stub=True, authors=[],
        )
        self.create_paper(stub)
        return stub, True

    def _promote_paper_stub(self, doi: str, full_paper: Paper) -> Paper:
        """Promote a stub Paper to a full Paper. Scalar properties are
        overwritten with the full payload; relationships and the
        citation_count counter are preserved (counter is what came in
        from accumulated edges; reference_count starts at 0 if the full
        paper hasn't created its own outbound edges yet, which it will
        in PaperImporter._fetch_and_link_references).
        """
        props = full_paper.to_neo4j_properties()
        # Preserve accumulated counters from the stub period.
        props.pop("citation_count", None)
        props.pop("reference_count", None)
        # The promotion always flips is_stub to False.
        props["is_stub"] = False

        def _promote(tx: ManagedTransaction) -> None:
            tx.run(
                """
                MATCH (p:Paper {doi: $doi})
                SET p += $props
                """,
                doi=doi, props=props,
            )

        with self.session() as session:
            self._execute_with_retry(session, lambda tx: _promote(tx))

        logger.info(f"Promoted stub Paper {doi} to full Paper")
        return self.get_paper(doi)

    def get_references(
        self, paper_doi: str, limit: int = 50,
    ) -> list[dict]:
        """Return Paper rows linked from ``paper_doi`` via outbound CITES."""
        def _fetch(tx: ManagedTransaction, doi: str, lim: int) -> list[dict]:
            result = tx.run(
                """
                MATCH (p:Paper {doi: $doi})-[:CITES]->(r:Paper)
                RETURN r
                ORDER BY r.title
                LIMIT $limit
                """,
                doi=doi, limit=lim,
            )
            return [dict(rec["r"]) for rec in result]

        with self.session() as session:
            return session.execute_read(
                lambda tx: _fetch(tx, paper_doi, limit),
            )

    def get_citing_papers(
        self, paper_doi: str, limit: int = 50,
    ) -> list[dict]:
        """Return Paper rows that link to ``paper_doi`` via inbound CITES."""
        def _fetch(tx: ManagedTransaction, doi: str, lim: int) -> list[dict]:
            result = tx.run(
                """
                MATCH (c:Paper)-[:CITES]->(p:Paper {doi: $doi})
                RETURN c
                ORDER BY c.title
                LIMIT $limit
                """,
                doi=doi, limit=lim,
            )
            return [dict(rec["c"]) for rec in result]

        with self.session() as session:
            return session.execute_read(
                lambda tx: _fetch(tx, paper_doi, limit),
            )

    def count_citations(self, paper_doi: str) -> int:
        """Returns the denormalized citation_count from the Paper node.

        Raises NotFoundError when the Paper doesn't exist."""
        paper = self.get_paper(paper_doi)
        return paper.citation_count


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
