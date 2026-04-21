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
            # Match on the natural identity: name + level + parent_id.
            # parent_id is NULL for domains, so use coalesce() to treat
            # missing vs null consistently.
            result = tx.run(
                """
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
                """,
                name=props["name"],
                level=props["level"],
                parent_id=props.get("parent_id"),
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
    ) -> tuple[ResearchConcept, bool]:
        """
        Embedding-based create-or-merge for ResearchConcepts.

        Embeds ``{name}: {description}``, searches the vector index, and
        if a candidate scores at or above ``threshold`` the incoming name
        and aliases are merged into the existing concept's alias list
        (existing concept returned). Otherwise a new concept is created.

        Returns (concept, created) — ``created`` is True when a new node
        was inserted, False when an existing node was reused.
        """
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

    _CONCEPT_RELATIONSHIPS = {
        "INVOLVES_CONCEPT": ("ProblemConcept", "id", "mention_count"),
        "DISCUSSES": ("Paper", "doi", "paper_count"),
    }

    def _link_entity_to_concept(
        self,
        entity_id: str,
        concept_id: str,
        relationship: str,
    ) -> bool:
        """
        Shared helper: MERGE an edge (relationship) from a source entity to
        the given ResearchConcept, incrementing the matching denormalized
        count only when a new edge is created.
        """
        if relationship not in self._CONCEPT_RELATIONSHIPS:
            raise ValueError(
                f"Unsupported relationship {relationship!r}; "
                f"expected one of {sorted(self._CONCEPT_RELATIONSHIPS)}"
            )
        src_label, src_field, count_field = self._CONCEPT_RELATIONSHIPS[relationship]

        def _link(tx: ManagedTransaction, eid: str, cid: str) -> bool:
            query = f"""
            MATCH (src:{src_label} {{{src_field}: $eid}})
            MATCH (rc:ResearchConcept {{id: $cid}})
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
                    f"ResearchConcept {cid!r} not found"
                )
            return bool(record["created"])

        with self.session() as session:
            return self._execute_with_retry(session, _link, entity_id, concept_id)

    def _unlink_entity_from_concept(
        self,
        entity_id: str,
        concept_id: str,
        relationship: str,
    ) -> bool:
        if relationship not in self._CONCEPT_RELATIONSHIPS:
            raise ValueError(
                f"Unsupported relationship {relationship!r}; "
                f"expected one of {sorted(self._CONCEPT_RELATIONSHIPS)}"
            )
        src_label, src_field, count_field = self._CONCEPT_RELATIONSHIPS[relationship]

        def _unlink(tx: ManagedTransaction, eid: str, cid: str) -> bool:
            query = f"""
            MATCH (src:{src_label} {{{src_field}: $eid}})
                  -[r:{relationship}]->(rc:ResearchConcept {{id: $cid}})
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
            return self._execute_with_retry(session, _unlink, entity_id, concept_id)

    def link_problem_to_concept(
        self, problem_concept_id: str, research_concept_id: str
    ) -> bool:
        """Link a ProblemConcept → ResearchConcept via INVOLVES_CONCEPT."""
        return self._link_entity_to_concept(
            entity_id=problem_concept_id,
            concept_id=research_concept_id,
            relationship="INVOLVES_CONCEPT",
        )

    def unlink_problem_from_concept(
        self, problem_concept_id: str, research_concept_id: str
    ) -> bool:
        """Remove a ProblemConcept → ResearchConcept INVOLVES_CONCEPT edge."""
        return self._unlink_entity_from_concept(
            entity_id=problem_concept_id,
            concept_id=research_concept_id,
            relationship="INVOLVES_CONCEPT",
        )

    def link_paper_to_concept(
        self, paper_doi: str, research_concept_id: str
    ) -> bool:
        """Link a Paper → ResearchConcept via DISCUSSES."""
        return self._link_entity_to_concept(
            entity_id=paper_doi,
            concept_id=research_concept_id,
            relationship="DISCUSSES",
        )

    def unlink_paper_from_concept(
        self, paper_doi: str, research_concept_id: str
    ) -> bool:
        """Remove a Paper → ResearchConcept DISCUSSES edge."""
        return self._unlink_entity_from_concept(
            entity_id=paper_doi,
            concept_id=research_concept_id,
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
