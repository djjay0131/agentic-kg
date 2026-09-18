"""
KGIS / KGCS migration seam.

Two things live here and nothing else:

* :mod:`~agentic_kg.migration.config` — the single answer to "is the
  KGIS/KGCS path enabled?". Defaults to off.
* :mod:`~agentic_kg.migration.imports` — guarded imports for the optional
  ``agentic-kgis`` / ``agentic-kgcs`` packages, which are installed only via
  the ``migration`` extra.

Importing this package is free: it pulls in neither ``kgis`` nor ``kgcs``, so
it is safe on a default install where neither is present.
"""

from agentic_kg.migration.config import (
    ENV_KGCS_ENABLED,
    ENV_KGIS_ENABLED,
    MigrationConfig,
    get_migration_config,
    reset_migration_config,
)
from agentic_kg.migration.imports import (
    MigrationDependencyError,
    is_migration_module_available,
    require_kg_contracts,
    require_kg_eval,
    require_kgcs,
    require_kgis,
    require_migration_module,
)

__all__ = [
    "ENV_KGCS_ENABLED",
    "ENV_KGIS_ENABLED",
    "MigrationConfig",
    "MigrationDependencyError",
    "get_migration_config",
    "is_migration_module_available",
    "require_kg_contracts",
    "require_kg_eval",
    "require_kgcs",
    "require_kgis",
    "require_migration_module",
    "reset_migration_config",
]
