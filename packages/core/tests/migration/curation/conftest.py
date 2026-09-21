"""Fixtures for the KGCS curation suite.

Two tiers, kept apart on purpose.

The **fast tier** needs nothing but the opt-in ``migration`` extra: it runs the
real KGIS shadow pipeline over the committed corpus against a replay client,
feeds the resulting candidates through the real KGCS engine, and applies the
plan to ``kg_contracts.testing.memory.MemoryGraphStore`` — the reference
in-memory ``GraphMutationStore``. No Docker, no database, no network, no clock.

The **integration tier** (``test_neo4j_curation.py``) applies the same plan to
the real :class:`Neo4jCanonicalGraphStore`. Its container fixtures are
duplicated from ``tests/migration/neo4j/conftest.py`` rather than imported: a
fixture is only visible below the ``conftest`` that defines it, so sharing one
container between two sibling directories means hoisting it to
``tests/migration/conftest.py`` — a file this change does not own. The
duplication is noted here so it is a known copy rather than an accident; the two
must be changed together, and hoisting is the real fix.

**The cost of the copy is a second Neo4j in the same job**, so this one is
explicitly sized down (:data:`NEO4J_MEMORY_ENV`) rather than left on Neo4j 5's
defaults, which reserve heap *and* page cache as if they were the only database
on the host. Two default-sized instances contend, and the symptom is not a clean
failure — it is a connection refused at fixture setup that reads like a bug in
the adapter. Connectivity is also retried for a bounded window: ``start()``
returning is not the same fact as the bolt port accepting connections, and a
single attempt turns a slow boot into a red suite.

A skipped suite and a passing suite look identical in a green check. The
``migration-canonical-adapter`` CI job runs the whole ``tests/migration`` tree
with the extra installed and its gate rejects *any* skip in the report, so a
whole-suite ``importorskip`` here would turn that job red rather than green.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

pytest.importorskip(
    "kgcs",
    reason=(
        "the opt-in 'migration' extra is not installed; "
        "pip install './packages/core[migration]'"
    ),
)

from agentic_kg.migration.config import MigrationConfig  # noqa: E402
from agentic_kg.migration.ingestion import (  # noqa: E402
    ShadowStores,
    importer_replay_client,
    load_corpus,
)
from agentic_kg.migration.ingestion.corpus import CorpusPaper  # noqa: E402
from agentic_kg.migration.ingestion.pipeline import (  # noqa: E402
    ShadowRunResult,
    run_shadow_ingestion,
)

#: The committed ground-truth chain, as the evaluation suite's own conftest
#: resolves it. Named here rather than re-derived so the arm-consumption test
#: grades against the same reconciled gold the runner does.
REPO_ROOT = Path(__file__).resolve().parents[5]
CHAIN_ROOT = REPO_ROOT / "packages/core/tests/extraction/fixtures/ground_truth_chain"

NEO4J_IMAGE = "neo4j:5.26-community"
NEO4J_TEST_PASSWORD = "testpassword"

#: Heap and page cache for the *second* Neo4j in the job. Small on purpose: this
#: suite writes a handful of identities and assertions per test, so the default
#: sizing buys nothing and costs coexistence with the canonical-adapter suite's
#: container.
NEO4J_MEMORY_ENV = {
    "NEO4J_server_memory_heap_initial__size": "256m",
    "NEO4J_server_memory_heap_max__size": "512m",
    "NEO4J_server_memory_pagecache_size": "128m",
}

#: How long to keep retrying the bolt handshake after ``start()`` returns.
CONNECT_TIMEOUT_SECONDS = 90.0


@pytest.fixture(scope="session")
def corpus() -> tuple[CorpusPaper, ...]:
    """All eight committed papers, loaded once."""
    return load_corpus()


@pytest.fixture
def enabled_config() -> MigrationConfig:
    """A config with the KGCS flag explicitly on.

    Constructed with keywords rather than through ``monkeypatch.setenv``:
    ADR-0004 decision 4 requires downstream code to take an *injected* config,
    and a test that enabled the flag through the environment would pass just as
    well against code that read the environment directly — proving nothing
    about the property it claims to check.
    """
    return MigrationConfig(use_kgis_ingestion=True, use_kgcs_resolution=True)


@pytest.fixture(scope="session")
def shadow_run() -> Iterator[ShadowRunResult]:
    """One KGIS shadow run over the whole committed corpus.

    Session-scoped because it is the expensive fixture here and because it is
    deterministic — fixed clock, fixed run id, replay client — so every test
    sees byte-identical candidates.
    """
    papers = load_corpus()
    stores = ShadowStores.in_memory()
    try:
        yield run_shadow_ingestion(
            papers,
            config=MigrationConfig(use_kgis_ingestion=True, use_kgcs_resolution=False),
            client=importer_replay_client(papers),
            stores=stores,
        )
    finally:
        stores.close()


@pytest.fixture(scope="session")
def shadow_candidates(shadow_run: ShadowRunResult) -> tuple[object, ...]:
    """Exactly what ``run_shadow_ingestion`` produced, in its own order.

    Both arms, structured first, matching ``ShadowRunResult``'s own ordering.
    The order is part of the input: ``CurationPlanner`` preserves input order
    through operations, ``candidate_ids``, preconditions and ``evidence_ids``,
    so re-sorting here would silently change every derived plan id.
    """
    return (*shadow_run.paper_candidates, *shadow_run.candidates)


@pytest.fixture(scope="session")
def chain_root() -> Path:
    assert CHAIN_ROOT.is_dir(), f"ground-truth chain fixtures missing at {CHAIN_ROOT}"
    return CHAIN_ROOT


@pytest.fixture(scope="session")
def doi_to_slug(corpus: tuple[CorpusPaper, ...]) -> dict[str, str]:
    """Normalised DOI -> slug, the join the ingestion arm export already uses."""
    return {paper.doi.casefold(): paper.slug for paper in corpus}


@pytest.fixture
def memory_store() -> object:
    """A pristine reference ``GraphMutationStore`` (and ``GraphReader``)."""
    from kg_contracts.testing.memory import MemoryGraphStore

    return MemoryGraphStore()


# --------------------------------------------------------------------------
# Integration tier: a real Neo4j. Duplicated from tests/migration/neo4j/conftest.
# --------------------------------------------------------------------------


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
def curation_neo4j() -> Iterator[tuple[str, tuple[str, str]]]:
    """A Neo4j to curate into: URI plus auth tuple."""
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
    for key, value in NEO4J_MEMORY_ENV.items():
        container = container.with_env(key, value)
    container.start()
    try:
        yield container.get_connection_url(), ("neo4j", NEO4J_TEST_PASSWORD)
    finally:
        container.stop()


@pytest.fixture(scope="session")
def curation_driver(curation_neo4j: tuple[str, tuple[str, str]]) -> Iterator[object]:
    """A driver whose connectivity is retried, not assumed.

    ``Neo4jContainer.start()`` returning means the container is up; it does not
    mean bolt is accepting connections, and under contention the gap is seconds.
    A single ``verify_connectivity()`` turns that gap into four errored tests
    whose message points at the adapter rather than at the wait.
    """
    import time

    from neo4j import GraphDatabase
    from neo4j.exceptions import Neo4jError, ServiceUnavailable

    uri, auth = curation_neo4j
    driver = GraphDatabase.driver(uri, auth=auth)
    deadline = time.monotonic() + CONNECT_TIMEOUT_SECONDS
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            driver.verify_connectivity()
            last = None
            break
        except (ServiceUnavailable, Neo4jError, OSError) as exc:  # pragma: no cover
            last = exc
            time.sleep(1.0)
    if last is not None:  # pragma: no cover - environment-dependent
        driver.close()
        raise RuntimeError(
            f"the curation suite's Neo4j at {uri} never accepted a connection "
            f"within {CONNECT_TIMEOUT_SECONDS:.0f}s. This suite starts a SECOND "
            f"container alongside the canonical-adapter suite's; if the host is "
            f"short of memory that is the first thing to suspect. "
            f"Last error: {last}"
        ) from last
    try:
        yield driver
    finally:
        driver.close()


@pytest.fixture
def make_canonical_store(curation_driver: object) -> Callable[[], object]:
    """Factory returning a **pristine** canonical store on every call.

    Pristine by minting a fresh namespace rather than deleting anything: every
    read and write in the adapter is namespace-scoped, so a new namespace is an
    empty graph by construction and ``current_epoch()`` starts at zero — which
    the executor's snapshot precondition depends on.
    """
    from agentic_kg.migration.config import MigrationConfig as _Config
    from agentic_kg.migration.neo4j import canonical_store_from_driver

    def factory() -> object:
        return canonical_store_from_driver(
            curation_driver,  # type: ignore[arg-type]
            _Config(use_kgcs_resolution=True),
            namespace=f"c{uuid.uuid4().hex}",
        )

    return factory
