"""Container and store fixtures for the Neo4j canonical-adapter tests.

Three skip conditions, each reported distinctly rather than folded into one
"can't run" — a suite that silently does not run is the failure this programme
keeps hitting:

* the opt-in ``migration`` extra is not installed (``kg_contracts`` absent);
* ``testcontainers`` is not installed;
* Docker is not reachable.

``NEO4J_CANONICAL_URI`` short-circuits all three for CI setups that already
provide a Neo4j.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator

import pytest

pytest.importorskip(
    "kg_contracts",
    reason=(
        "the opt-in 'migration' extra is not installed; pip install './packages/core[migration]'"
    ),
)

NEO4J_IMAGE = "neo4j:5.26-community"
NEO4J_TEST_PASSWORD = "testpassword"


def _external_neo4j() -> tuple[str, str, str] | None:
    uri = os.environ.get("NEO4J_CANONICAL_URI")
    if not uri:
        return None
    return (
        uri,
        os.environ.get("NEO4J_CANONICAL_USERNAME", "neo4j"),
        os.environ.get("NEO4J_CANONICAL_PASSWORD", NEO4J_TEST_PASSWORD),
    )


@pytest.fixture(scope="session")
def canonical_neo4j() -> Iterator[tuple[str, tuple[str, str]]]:
    """A Neo4j to run the canonical adapter against: URI plus auth tuple."""
    external = _external_neo4j()
    if external is not None:
        uri, user, password = external
        yield uri, (user, password)
        return

    try:
        from testcontainers.neo4j import Neo4jContainer
    except ImportError:  # pragma: no cover - environment-dependent
        pytest.skip("testcontainers[neo4j] not installed and NEO4J_CANONICAL_URI not set")
        return

    try:
        import docker

        docker.from_env().ping()
    except Exception:  # pragma: no cover - environment-dependent
        pytest.skip("Docker is not available and NEO4J_CANONICAL_URI not set")
        return

    container = Neo4jContainer(NEO4J_IMAGE, password=NEO4J_TEST_PASSWORD)
    container.start()
    try:
        yield container.get_connection_url(), ("neo4j", NEO4J_TEST_PASSWORD)
    finally:
        container.stop()


@pytest.fixture(scope="session")
def canonical_driver(canonical_neo4j: tuple[str, tuple[str, str]]) -> Iterator[object]:
    from neo4j import GraphDatabase

    uri, auth = canonical_neo4j
    driver = GraphDatabase.driver(uri, auth=auth)
    try:
        driver.verify_connectivity()
        yield driver
    finally:
        driver.close()


@pytest.fixture
def make_canonical_store(canonical_driver: object) -> Callable[..., object]:
    """Factory returning a **pristine** store on every call.

    Pristine is achieved by minting a fresh namespace per store rather than by
    deleting anything: every read and write in the adapter is namespace-scoped,
    so a new namespace is an empty graph by construction. That matters for the
    conformance suite — ``make_store()`` is called once per test method and
    several of those tests assert on ``current_epoch()`` starting at zero — and
    it means the suite never needs a destructive statement of its own.
    """
    from agentic_kg.migration.neo4j import Neo4jCanonicalGraphStore

    created: list[Neo4jCanonicalGraphStore] = []

    def factory(**kwargs: object) -> Neo4jCanonicalGraphStore:
        store = Neo4jCanonicalGraphStore(
            canonical_driver,  # type: ignore[arg-type]
            namespace=f"t{uuid.uuid4().hex}",
            **kwargs,  # type: ignore[arg-type]
        )
        store.ensure_schema()
        created.append(store)
        return store

    return factory
