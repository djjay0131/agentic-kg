"""
Configuration seam for the KGIS / KGCS migration.

This module is the single place that answers "is the KGIS/KGCS path enabled?".
Every flag defaults to **off**, so importing this module — or the package that
contains it — changes nothing about how the existing pipeline behaves. Later
migration PRs branch on :func:`get_migration_config`; until an operator sets one
of the environment variables below, every one of those branches takes the
legacy path.

Environment variables
---------------------
``KGIS_INGESTION_ENABLED``
    Route ingestion through ``kgis`` instead of ``agentic_kg.ingestion``.
``KGCS_RESOLUTION_ENABLED``
    Route entity resolution / canonicalisation through ``kgcs``.

The two are deliberately independent switches rather than one master flag:
KGIS (ingestion) and KGCS (canonicalisation) are separate systems that will be
adopted in separate PRs, and an operator needs to be able to roll one back
without reverting the other.

Truthiness follows the usual container/CI conventions — ``1``, ``true``,
``yes`` and ``on`` (case-insensitive, surrounding whitespace ignored) all mean
enabled. Anything else, including unset, means disabled. This is deliberately
more permissive than the older ``DEBUG`` flag in :mod:`agentic_kg.config`,
which only accepts the literal ``"true"``: silently ignoring
``KGIS_INGESTION_ENABLED=1`` on a flag that selects between two pipelines is a
worse failure than accepting an extra spelling.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

# Values (lowercased, stripped) that count as "enabled".
_TRUTHY = frozenset({"1", "true", "yes", "on"})

ENV_KGIS_ENABLED = "KGIS_INGESTION_ENABLED"
ENV_KGCS_ENABLED = "KGCS_RESOLUTION_ENABLED"


def _env_flag(name: str) -> bool:
    """Read a boolean feature flag from the environment. Absent => False."""
    return os.getenv(name, "").strip().lower() in _TRUTHY


@dataclass
class MigrationConfig:
    """Opt-in switches for the KGIS/KGCS migration path.

    Both fields default to ``False``. Constructing this dataclass never
    imports ``kgis`` or ``kgcs`` — checking whether the migration is enabled
    must stay cheap and side-effect free, so that callers can branch on it
    even when the optional ``migration`` extra is not installed.
    """

    use_kgis_ingestion: bool = field(default_factory=lambda: _env_flag(ENV_KGIS_ENABLED))
    use_kgcs_resolution: bool = field(default_factory=lambda: _env_flag(ENV_KGCS_ENABLED))

    @property
    def any_enabled(self) -> bool:
        """True if any part of the migration path is turned on."""
        return self.use_kgis_ingestion or self.use_kgcs_resolution

    @property
    def is_default(self) -> bool:
        """True if this is the untouched, legacy-behaviour configuration."""
        return not self.any_enabled


# Singleton instance, mirroring the pattern in agentic_kg.config.
_migration_config: Optional[MigrationConfig] = None


def get_migration_config() -> MigrationConfig:
    """Get the migration configuration singleton."""
    global _migration_config
    if _migration_config is None:
        _migration_config = MigrationConfig()
    return _migration_config


def reset_migration_config() -> None:
    """Reset the migration configuration singleton (useful for testing)."""
    global _migration_config
    _migration_config = None


__all__ = [
    "ENV_KGCS_ENABLED",
    "ENV_KGIS_ENABLED",
    "MigrationConfig",
    "get_migration_config",
    "reset_migration_config",
]
