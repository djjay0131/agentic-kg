"""The single import gate for ``kg_contracts`` inside this subpackage.

Every other module here imports the contract types from *this* module, never
from ``kg_contracts`` directly. Two reasons, both load-bearing:

* **ADR-0004 decision 3 / AC-15c** — "reaches the optional packages only
  through :mod:`agentic_kg.migration.imports`". A bare
  ``from kg_contracts.stores import ...`` at the top of ``store.py`` would
  surface ``ModuleNotFoundError: No module named 'kg_contracts'`` on a default
  install, which tells an operator nothing. Routing through
  :func:`~agentic_kg.migration.imports.require_kg_contracts` first means the
  failure names the distribution, the pin, and the install command.
* **Every entry point is gated, not just the package ``__init__``.** Putting
  the guard only in ``neo4j/__init__.py`` would be bypassed by
  ``import agentic_kg.migration.neo4j.store``. Because *that* module imports
  this one, the guard runs whichever door is used.

``kg_contracts`` ships a ``py.typed`` marker (unlike ``kgis``, ``kg_eval`` and
``kgcs``, which do not), so these re-exports stay fully typed for mypy.
"""

from __future__ import annotations

from agentic_kg.migration.imports import require_kg_contracts

# Raises MigrationDependencyError with an actionable message when the opt-in
# `migration` extra is not installed. Must run before the imports below.
require_kg_contracts()

from kg_contracts.assertions import (  # noqa: E402
    Assertion,
    CanonicalEntity,
    CurationStatus,
)
from kg_contracts.curation import (  # noqa: E402
    CurationOperation,
    CurationOperationType,
    Precondition,
)
from kg_contracts.identity import EntityRef  # noqa: E402
from kg_contracts.stores import (  # noqa: E402
    AdapterCapabilities,
    CommitResult,
    GraphMutationBatch,
    GraphMutationStore,
    GraphReader,
    GraphReadOptions,
    LedgerReader,
    TemporalGraphReader,
    UnsupportedCapabilityError,
)

__all__ = [
    "AdapterCapabilities",
    "Assertion",
    "CanonicalEntity",
    "CommitResult",
    "CurationOperation",
    "CurationOperationType",
    "CurationStatus",
    "EntityRef",
    "GraphMutationBatch",
    "GraphMutationStore",
    "GraphReadOptions",
    "GraphReader",
    "LedgerReader",
    "Precondition",
    "TemporalGraphReader",
    "UnsupportedCapabilityError",
]
