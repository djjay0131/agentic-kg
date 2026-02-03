"""
E2E test utilities.

Helper functions for waiting, retrying, and managing test data.
"""

from __future__ import annotations

import asyncio
import functools
import time
from typing import TYPE_CHECKING, Any, Callable, TypeVar

import httpx

if TYPE_CHECKING:
    from neo4j import Driver, Session

T = TypeVar("T")


def retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator for retrying flaky operations."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception = None
            current_delay = delay

            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        time.sleep(current_delay)
                        current_delay *= backoff

            raise last_exception  # type: ignore[misc]

        return wrapper

    return decorator


async def async_retry(
    func: Callable[..., Any],
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Any:
    """Async helper for retrying operations."""
    last_exception = None
    current_delay = delay

    for attempt in range(max_attempts):
        try:
            return await func()
        except exceptions as e:
            last_exception = e
            if attempt < max_attempts - 1:
                await asyncio.sleep(current_delay)
                current_delay *= backoff

    raise last_exception  # type: ignore[misc]


def wait_for_neo4j(driver: "Driver", timeout: float = 30.0) -> bool:
    """Wait for Neo4j to become available."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            driver.verify_connectivity()
            return True
        except Exception:
            time.sleep(1)
    return False


def clear_test_data(session: "Session", prefix: str = "TEST_") -> int:
    """Clear test data from Neo4j (nodes with IDs starting with prefix)."""
    result = session.run(
        """
        MATCH (n)
        WHERE n.id STARTS WITH $prefix
           OR n.paper_id STARTS WITH $prefix
           OR n.problem_id STARTS WITH $prefix
        DETACH DELETE n
        RETURN count(n) as deleted
        """,
        prefix=prefix,
    )
    record = result.single()
    return record["deleted"] if record else 0


def seed_test_paper(session: "Session", paper_id: str = "TEST_paper_001") -> dict[str, Any]:
    """Seed a test paper with minimal data."""
    result = session.run(
        """
        MERGE (p:Paper {id: $paper_id})
        SET p.title = 'Test Paper for E2E',
            p.abstract = 'This is a test paper for end-to-end testing.',
            p.year = 2024,
            p.venue = 'Test Conference',
            p.citation_count = 0,
            p.created_at = datetime()
        RETURN p
        """,
        paper_id=paper_id,
    )
    record = result.single()
    return dict(record["p"]) if record else {}


def seed_test_problem(
    session: "Session",
    problem_id: str = "TEST_problem_001",
    paper_id: str = "TEST_paper_001",
) -> dict[str, Any]:
    """Seed a test problem linked to a paper."""
    result = session.run(
        """
        MATCH (paper:Paper {id: $paper_id})
        MERGE (prob:Problem {id: $problem_id})
        SET prob.title = 'Test Problem for E2E',
            prob.description = 'A research problem created for end-to-end testing.',
            prob.domain = 'testing',
            prob.status = 'open',
            prob.importance_score = 0.5,
            prob.created_at = datetime()
        MERGE (prob)-[:EXTRACTED_FROM]->(paper)
        RETURN prob
        """,
        problem_id=problem_id,
        paper_id=paper_id,
    )
    record = result.single()
    return dict(record["prob"]) if record else {}


class StagingAPIClient:
    """Wrapper for staging API with retry logic."""

    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=self.base_url, timeout=self.timeout)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    @retry(max_attempts=3, delay=1.0)
    def health_check(self) -> dict[str, Any]:
        """Check API health."""
        resp = self.client.get("/health")
        resp.raise_for_status()
        return resp.json()

    @retry(max_attempts=3, delay=1.0)
    def get_problems(self, limit: int = 10, offset: int = 0) -> list[dict[str, Any]]:
        """Get problems from API."""
        resp = self.client.get("/api/problems", params={"limit": limit, "offset": offset})
        resp.raise_for_status()
        return resp.json()

    @retry(max_attempts=3, delay=1.0)
    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search problems."""
        resp = self.client.get("/api/search", params={"q": query, "limit": limit})
        resp.raise_for_status()
        return resp.json()

    @retry(max_attempts=3, delay=1.0)
    def start_workflow(
        self,
        domain_filter: str | None = None,
        max_problems: int = 10,
    ) -> dict[str, Any]:
        """Start an agent workflow."""
        resp = self.client.post(
            "/api/agents/workflows",
            json={
                "domain_filter": domain_filter,
                "max_problems": max_problems,
            },
        )
        resp.raise_for_status()
        return resp.json()

    @retry(max_attempts=3, delay=1.0)
    def get_workflow(self, run_id: str) -> dict[str, Any]:
        """Get workflow state."""
        resp = self.client.get(f"/api/agents/workflows/{run_id}")
        resp.raise_for_status()
        return resp.json()


def count_nodes(session: "Session", label: str) -> int:
    """Count nodes with a given label."""
    result = session.run(f"MATCH (n:{label}) RETURN count(n) as count")
    record = result.single()
    return record["count"] if record else 0


def count_relationships(session: "Session", rel_type: str) -> int:
    """Count relationships of a given type."""
    result = session.run(f"MATCH ()-[r:{rel_type}]->() RETURN count(r) as count")
    record = result.single()
    return record["count"] if record else 0
