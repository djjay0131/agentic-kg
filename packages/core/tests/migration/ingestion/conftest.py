"""Fixtures for the KGIS shadow-ingestion suite.

One skip condition, reported in its own words: the opt-in ``migration`` extra
is not installed. Unlike the Neo4j canonical-adapter suite next door, nothing
here needs Docker, testcontainers or a database — the whole path runs against
committed text and two in-memory SQLite databases, which is what lets it be a
fast, deterministic, non-integration suite.

A skipped suite and a passing suite look identical in a green check, so the CI
job that installs the extra also runs ``suite_gate.py`` over the junit output
to assert these tests actually executed.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

pytest.importorskip(
    "kgis",
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
    run_shadow_ingestion,
)
from agentic_kg.migration.ingestion.corpus import CorpusPaper  # noqa: E402
from agentic_kg.migration.ingestion.pipeline import ShadowRunResult  # noqa: E402


@pytest.fixture(scope="session")
def corpus() -> tuple[CorpusPaper, ...]:
    """All eight committed papers, loaded once."""
    return load_corpus()


@pytest.fixture
def enabled_config() -> MigrationConfig:
    """An explicitly enabled config.

    Constructed with keywords rather than by setting the environment: ADR-0004
    decision 4 requires downstream code to take an *injected* config, and a test
    that enabled the flag through ``monkeypatch.setenv`` would pass just as well
    against code that read the environment directly — proving nothing about the
    property it claims to check.
    """
    return MigrationConfig(use_kgis_ingestion=True, use_kgcs_resolution=False)


@pytest.fixture(scope="session")
def shadow_run() -> Iterator[ShadowRunResult]:
    """One shadow run over the whole committed corpus.

    Session-scoped because it is the expensive fixture in this suite (it
    segments 220 KB of text and builds ~230 candidates) and because it is
    deterministic — a fixed clock, a fixed run id and a replay client, so every
    test sees byte-identical candidates.
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
