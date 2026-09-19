"""Construction of the canonical store from an **injected** `MigrationConfig`.

ADR-0004 decision 4, and AC-15c: "downstream code takes an injected
`MigrationConfig` and does not call `get_migration_config()` inline". This
module is the whole of this subpackage's config surface and it never touches the
singleton — the reason is in the ADR: `get_migration_config()` is a cached
process-global, adequate for a per-deployment legacy/KGIS split but unable to
express running both paths in one process to diff them, which the planned
three-way comparison phase needs.

The flag is load-bearing here, not decorative. `KGCS_RESOLUTION_ENABLED` selects
the KGCS curation path, and this store *is* that path's canonical surface, so
building one while the flag is off would mean the opt-in seam had a hole in it.
:func:`canonical_store_from_config` refuses; :class:`MigrationDisabledError`
names which flag and how to set it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from neo4j import Driver, GraphDatabase

from agentic_kg.migration.config import ENV_KGCS_ENABLED, MigrationConfig
from agentic_kg.migration.neo4j.store import DEFAULT_DATABASE, Neo4jCanonicalGraphStore

#: Namespace used when a caller does not pick one. A deployment with more than
#: one canonical graph in a single database must pass its own.
DEFAULT_NAMESPACE = "canon"


class MigrationDisabledError(RuntimeError):
    """The KGCS path is switched off, so its canonical store must not be built."""


def _require_enabled(config: MigrationConfig) -> None:
    if not config.use_kgcs_resolution:
        raise MigrationDisabledError(
            "the KGCS canonical store was requested while the KGCS path is "
            f"disabled. Set {ENV_KGCS_ENABLED}=1, or pass "
            "MigrationConfig(use_kgcs_resolution=True) explicitly. Both default "
            "to off so the legacy pipeline is unchanged (ADR-0004)."
        )


def canonical_store_from_driver(
    driver: Driver,
    config: MigrationConfig,
    *,
    namespace: str = DEFAULT_NAMESPACE,
    database: str = DEFAULT_DATABASE,
    clock: Callable[[], datetime] | None = None,
    ensure_schema: bool = True,
) -> Neo4jCanonicalGraphStore:
    """Build a canonical store over an already-open driver.

    Args:
        driver: Caller-owned; not closed by the returned store.
        config: Injected, never fetched from the singleton (ADR-0004 dec. 4).
    """
    _require_enabled(config)
    store = Neo4jCanonicalGraphStore(driver, namespace=namespace, database=database, clock=clock)
    if ensure_schema:
        store.ensure_schema()
    return store


def canonical_store_from_config(
    config: MigrationConfig,
    *,
    uri: str,
    auth: tuple[str, str],
    namespace: str = DEFAULT_NAMESPACE,
    database: str = DEFAULT_DATABASE,
    clock: Callable[[], datetime] | None = None,
    ensure_schema: bool = True,
) -> Neo4jCanonicalGraphStore:
    """Open a driver and build a canonical store that owns it.

    ``uri``/``auth`` are explicit parameters rather than being read from
    `agentic_kg.config`: the canonical surface is deliberately addressable
    separately from the legacy/projection connection (spec §4.2, "two Neo4j
    surfaces, never one access path"), and defaulting it to the projection's
    connection settings would quietly collapse the two.
    """
    _require_enabled(config)
    driver = GraphDatabase.driver(uri, auth=auth)
    store = Neo4jCanonicalGraphStore(
        driver, namespace=namespace, database=database, clock=clock, owns_driver=True
    )
    if ensure_schema:
        store.ensure_schema()
    return store


__all__ = [
    "DEFAULT_NAMESPACE",
    "MigrationDisabledError",
    "canonical_store_from_config",
    "canonical_store_from_driver",
]
