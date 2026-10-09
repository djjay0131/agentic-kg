"""Container and store fixtures for the Neo4j canonical-adapter tests.

Three skip conditions, each reported distinctly rather than folded into one
"can't run" — a suite that silently does not run is the failure this programme
keeps hitting:

* the opt-in ``migration`` extra is not installed (``kg_contracts`` absent);
* ``testcontainers`` is not installed;
* Docker is not reachable.

There is deliberately no environment variable that points this suite at an
existing Neo4j. There used to be: ``NEO4J_CANONICAL_URI`` short-circuited all
three skips, and ``make_canonical_store()`` then ran ``ensure_schema()`` --
DDL -- against whatever it named, over a raw ``GraphDatabase.driver`` that
never passes the ownership seam in ``packages/core/tests/conftest.py``. That is
the same defect class as issue #78 (credentials in the environment *enabling* a
route to a database the session does not own), in a directory ``make test-core``
collects. The adversarial review of PR #87 verified it end to end with the #78
seam live: the route still wrote a node *and* created a constraint on a stand-in
database.

So this suite, like the integration suite, has exactly one answer to "which
Neo4j?": one it started itself. It declares that container to the ownership
registry below, which is what keeps the claim in ``tests/conftest.py`` -- that
nothing under ``packages/core/tests`` lets an environment variable declare a
database -- true for this directory too. The property is held by
``TestNoConftestSelectsADatabaseFromTheEnvironment`` in
``tests/knowledge_graph/test_database_ownership_seam.py``, which fails if any
conftest in this tree reads a NEO4J-named variable again.
"""

from __future__ import annotations

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


@pytest.fixture(scope="session")
def canonical_neo4j() -> Iterator[tuple[str, tuple[str, str]]]:
    """A Neo4j to run the canonical adapter against: URI plus auth tuple.

    Always a throwaway container this session starts. See the module docstring
    for why the ``NEO4J_CANONICAL_URI`` short-circuit was removed rather than
    documented.
    """
    try:
        from testcontainers.neo4j import Neo4jContainer
    except ImportError:  # pragma: no cover - environment-dependent
        pytest.skip("testcontainers[neo4j] not installed; this suite needs an owned Neo4j")
        return

    try:
        import docker

        docker.from_env().ping()
    except Exception:  # pragma: no cover - environment-dependent
        pytest.skip("Docker is not available; this suite needs an owned Neo4j")
        return

    container = Neo4jContainer(NEO4J_IMAGE, password=NEO4J_TEST_PASSWORD)
    container.start()
    try:
        url = container.get_connection_url()
        # Starting it is what declares it. The registry lives in
        # packages/core/tests/conftest.py so that a Neo4jRepository built
        # anywhere in this session -- including by adapter code under test --
        # is measured against the same set of owned targets.
        from ...conftest import declare_session_database

        declare_session_database(
            url,
            reason="this session started this throwaway canonical-adapter container",
        )
        yield url, ("neo4j", NEO4J_TEST_PASSWORD)
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
