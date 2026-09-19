"""Neo4j adapters for the KGCS **canonical** surface (mapping spec §4.2).

Two classes, and the split between them is the point:

* :class:`~agentic_kg.migration.neo4j.store.Neo4jCanonicalGraphStore` —
  `GraphMutationStore` + `TemporalGraphReader` + `CapabilityDeclaring`.
  Executor-only. Applications must never hold one (ADR-0010,
  governance-delta principle 3).
* :class:`~agentic_kg.migration.neo4j.reader.Neo4jCanonicalGraphReader` —
  the same data, read-only, and structurally **not** a `GraphMutationStore`.
  This is what the projector and anything application-side get; obtain one with
  ``store.read_only()``.

Importing this package requires the opt-in ``migration`` extra; the failure is
routed through :mod:`agentic_kg.migration.imports` so it names the distribution,
the pin, and the install command rather than surfacing a bare
``ModuleNotFoundError``.

Not in scope here: the legacy/projection surface (`Paper`, `Topic`, the 15
relationship types, the recomputed counters, the six vector indexes). That is a
different module over a different set of labels, and it reads the canonical
surface only through the read-only façade above.
"""

from agentic_kg.migration.neo4j.factory import (
    DEFAULT_NAMESPACE,
    MigrationDisabledError,
    canonical_store_from_config,
    canonical_store_from_driver,
)
from agentic_kg.migration.neo4j.operations import (
    SUPPORTED_OPERATIONS,
    UNSUPPORTED_OPERATIONS,
    UNSUPPORTED_REASONS,
)
from agentic_kg.migration.neo4j.reader import Neo4jCanonicalGraphReader
from agentic_kg.migration.neo4j.store import CommitRefused, Neo4jCanonicalGraphStore

__all__ = [
    "DEFAULT_NAMESPACE",
    "SUPPORTED_OPERATIONS",
    "UNSUPPORTED_OPERATIONS",
    "UNSUPPORTED_REASONS",
    "CommitRefused",
    "MigrationDisabledError",
    "Neo4jCanonicalGraphReader",
    "Neo4jCanonicalGraphStore",
    "canonical_store_from_config",
    "canonical_store_from_driver",
]
