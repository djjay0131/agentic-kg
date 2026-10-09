"""Integration: the migration run is idempotent against the real Neo4j adapter.

The fast tier proves idempotency on the reference ``MemoryGraphStore``; this
proves the same property on :class:`Neo4jCanonicalGraphStore`, where the
executor's snapshot and precondition guards are the ones doing the refusing.
Runs only where an owned Neo4j container is available (the
``migration-canonical-adapter`` CI job).

The second run is expected to commit nothing. That is the wanted outcome: the
same deterministic candidate ids re-plan the same ``CREATE_IDENTITY``
operations, and the store's guards refuse to apply them a second time rather
than minting duplicates.
"""

from __future__ import annotations

import pytest
from agentic_kg.migration.ingestion import ShadowStores, importer_replay_client
from agentic_kg.migration.neo4j import SUPPORTED_OPERATIONS
from agentic_kg.migration.run import run_migration

pytestmark = pytest.mark.integration


def test_second_run_does_not_duplicate_canonical_state(
    enabled_config, corpus, make_canonical_store
):
    store = make_canonical_store()
    stores = ShadowStores.in_memory()
    try:
        first = run_migration(
            config=enabled_config,
            papers=corpus,
            client=importer_replay_client(corpus),
            stores=stores,
            store=store,
            namespace=store.namespace,
            supported_operations=SUPPORTED_OPERATIONS,
        )
        entities_after_first = len(store.find_entities())
        epoch_first = store.current_epoch()

        second = run_migration(
            config=enabled_config,
            papers=corpus,
            client=importer_replay_client(corpus),
            stores=stores,
            store=store,
            namespace=store.namespace,
            supported_operations=SUPPORTED_OPERATIONS,
        )
        entities_after_second = len(store.find_entities())
        epoch_second = store.current_epoch()
    finally:
        stores.close()

    assert first.committed is True
    assert entities_after_first == len(corpus)
    assert second.committed is False
    assert entities_after_second == entities_after_first
    assert epoch_second == epoch_first
