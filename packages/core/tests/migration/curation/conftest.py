"""Fixtures for the KGCS curation suite.

Two tiers, kept apart on purpose.

The **fast tier** needs nothing but the opt-in ``migration`` extra: it runs the
real KGIS shadow pipeline over the committed corpus against a replay client,
feeds the resulting candidates through the real KGCS engine, and applies the
plan to ``kg_contracts.testing.memory.MemoryGraphStore`` — the reference
in-memory ``GraphMutationStore``. No Docker, no database, no network, no clock.

The **integration tier** (``test_neo4j_curation.py``) applies the same plan to
the real :class:`Neo4jCanonicalGraphStore`, against a Neo4j **this session
starts and declares**. See the comment above the container fixture for why there
is no environment variable and no raw driver here — the short version is that
the first draft copied both from a sibling conftest that PR #87 had since
stripped of a live defect, and PR #87's guard caught the copy on the rebase.

Starting a container of its own is a real cost — this job now runs several —
and hoisting the fixture to ``tests/migration/conftest.py`` so the migration
suites share one is the proper fix. That file is outside this change's scope;
the cost is recorded rather than hidden.

A skipped suite and a passing suite look identical in a green check. The
``migration-canonical-adapter`` CI job runs the whole ``tests/migration`` tree
with the extra installed and its gate rejects *any* skip in the report, so a
whole-suite ``importorskip`` here would turn that job red rather than green.
"""

from __future__ import annotations

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
# Integration tier: a Neo4j this session started, and nothing else.
# --------------------------------------------------------------------------
#
# There is deliberately **no environment variable** pointing this suite at an
# existing Neo4j, and no raw ``GraphDatabase.driver`` anywhere in this file.
#
# The first version of this conftest had both, because it was copied from
# ``tests/migration/neo4j/conftest.py`` *before* PR #87 removed them there. That
# PR removed the ``NEO4J_CANONICAL_URI`` route because it was a live defect, not
# a style problem: setting the variable short-circuited every skip condition and
# ``ensure_schema()`` then ran DDL against whatever it named, over a driver that
# never passed the ownership seam. The copy here reintroduced it verbatim, and
# ``TestNoConftestSelectsADatabaseFromTheEnvironment`` caught it on the rebase —
# which is the guard doing exactly its job, on exactly the file that deserved it.
#
# So: one answer to "which Neo4j?" — one this session started. It is declared to
# the ownership registry in ``tests/conftest.py``, and the store is built through
# ``canonical_store_from_config`` (production code, which owns its own driver)
# rather than by opening one here.


@pytest.fixture(scope="session")
def curation_neo4j() -> Iterator[str]:
    """A throwaway Neo4j container for this suite, declared as owned."""
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
    for key, value in NEO4J_MEMORY_ENV.items():
        container = container.with_env(key, value)
    container.start()
    try:
        url = container.get_connection_url()
        # Starting it is what declares it.
        from ...conftest import declare_session_database

        declare_session_database(
            url,
            reason="this session started this throwaway curation-pipeline container",
        )
        yield url
    finally:
        container.stop()


@pytest.fixture
def make_canonical_store(curation_neo4j: str) -> Iterator[Callable[[], object]]:
    """Factory returning a **pristine** canonical store on every call.

    Pristine by minting a fresh namespace rather than deleting anything: every
    read and write in the adapter is namespace-scoped, so a new namespace is an
    empty graph by construction and ``current_epoch()`` starts at zero — which
    the executor's snapshot precondition depends on.

    Built through ``canonical_store_from_config``, the production constructor,
    which opens and owns its own driver. That keeps the raw-driver call inside
    ``src/`` where the ownership seam can see it, instead of in the test tree
    where PR #87's scan correctly refuses it.

    Connectivity is retried for a bounded window: ``start()`` returning is not
    the same fact as bolt accepting connections, and under contention the gap is
    seconds. A single attempt turns a slow boot into errored tests whose message
    points at the adapter rather than at the wait.
    """
    import time

    from agentic_kg.migration.config import MigrationConfig as _Config
    from agentic_kg.migration.neo4j import canonical_store_from_config

    created: list[object] = []

    def factory() -> object:
        deadline = time.monotonic() + CONNECT_TIMEOUT_SECONDS
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                store = canonical_store_from_config(
                    _Config(use_kgcs_resolution=True),
                    uri=curation_neo4j,
                    auth=("neo4j", NEO4J_TEST_PASSWORD),
                    namespace=f"c{uuid.uuid4().hex}",
                )
            except Exception as exc:  # pragma: no cover - environment-dependent
                last = exc
                time.sleep(1.0)
                continue
            created.append(store)
            return store
        raise RuntimeError(  # pragma: no cover - environment-dependent
            f"the curation suite's Neo4j at {curation_neo4j} never accepted a "
            f"connection within {CONNECT_TIMEOUT_SECONDS:.0f}s. This suite starts "
            f"its own container alongside the other migration suites'; if the host "
            f"is short of memory that is the first thing to suspect. "
            f"Last error: {last}"
        )

    try:
        yield factory
    finally:
        for store in created:
            store.close()  # type: ignore[attr-defined]
