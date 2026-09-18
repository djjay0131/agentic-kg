"""
Guarded imports for the optional KGIS / KGCS packages.

``agentic-kgis`` and ``agentic-kgcs`` live behind the ``migration`` extra, so
they are absent from a default install. Importing them directly would surface a
bare ``ModuleNotFoundError: No module named 'kgis'`` from somewhere deep in the
pipeline, which tells an operator nothing about *why* it is missing or how to
fix it. Every access to those packages goes through this module instead.

Note the irony we are avoiding repeating: the bug fixed by agentic-kgis PR #39
— the very commit this repo pins — was an *unguarded optional import*. ``import
kgis`` failed outright whenever ``pytest`` was not installed, because a test
helper was imported at package scope. Treating an optional dependency as if it
were mandatory is exactly the failure mode this module exists to prevent, so
the guard below is careful to keep the two cases distinct:

* the package is **not installed** — actionable: install the extra;
* the package **is installed but fails to import** — a broken install, and the
  underlying error is preserved rather than being relabelled as "not
  installed", because telling someone to reinstall an already-present package
  sends them down the wrong path.
"""

from __future__ import annotations

import importlib
from types import ModuleType

# Which distribution ships which top-level module. KGIS ships three.
_MODULE_TO_DISTRIBUTION = {
    "kg_contracts": "agentic-kgis",
    "kgis": "agentic-kgis",
    "kg_eval": "agentic-kgis",
    "kgcs": "agentic-kgcs",
}

_INSTALL_HINT = (
    "Install the optional 'migration' extra:\n"
    "    pip install './packages/core[migration]'\n"
    "or, with uv:\n"
    "    uv pip install -e './packages/core[migration]'"
)


class MigrationDependencyError(ImportError):
    """Raised when an optional KGIS/KGCS package is required but unavailable.

    Subclasses :class:`ImportError` so that callers which already handle
    import failures keep working, while callers that want to distinguish "the
    migration extra is missing" from any other import problem can catch this
    specific type.
    """


def require_migration_module(module_name: str) -> ModuleType:
    """Import an optional migration module, or fail with an actionable error.

    Args:
        module_name: Top-level module to import, e.g. ``"kgis"``.

    Returns:
        The imported module.

    Raises:
        MigrationDependencyError: The distribution providing ``module_name``
            is not installed. The message names the distribution, the exact
            pin, and how to install it.
        ModuleNotFoundError: ``module_name`` is installed but one of *its* own
            imports failed. Re-raised untouched — this is a broken install,
            not a missing extra, and the original error names the real culprit.
    """
    root = module_name.split(".")[0]
    distribution = _MODULE_TO_DISTRIBUTION.get(root)
    if distribution is None:
        raise ValueError(
            f"{module_name!r} is not a known KGIS/KGCS module. "
            f"Known modules: {', '.join(sorted(_MODULE_TO_DISTRIBUTION))}."
        )

    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        missing = (exc.name or "").split(".")[0]
        if missing != root:
            # The package itself is present; something it imports is not.
            # Do NOT claim the extra is missing — say what actually broke.
            raise ModuleNotFoundError(
                f"{module_name!r} is installed but failed to import: its "
                f"dependency {exc.name!r} is missing. This is a broken "
                f"install of {distribution}, not a missing 'migration' extra. "
                f"Reinstalling the extra will not fix it; install {exc.name!r} "
                f"or re-pin {distribution}.",
                name=exc.name,
                path=exc.path,
            ) from exc
        raise MigrationDependencyError(
            f"{module_name!r} is not available. It is provided by "
            f"'{distribution}', an optional dependency that is NOT part of the "
            f"default install.\n\n{_INSTALL_HINT}\n\n"
            f"Note: {distribution} is pinned to an exact commit in "
            f"pyproject.toml — it is not on PyPI, so a plain "
            f"'pip install {distribution}' will not work."
        ) from exc


def is_migration_module_available(module_name: str) -> bool:
    """Return whether an optional migration module can be imported.

    Never raises for the "not installed" case; use this for diagnostics and
    for branching, and :func:`require_migration_module` at the point of use.
    """
    try:
        require_migration_module(module_name)
    except MigrationDependencyError:
        return False
    return True


def require_kg_contracts() -> ModuleType:
    """Import ``kg_contracts`` (the KGIS ports layer) or fail actionably."""
    return require_migration_module("kg_contracts")


def require_kgis() -> ModuleType:
    """Import ``kgis`` (ingestion) or fail actionably."""
    return require_migration_module("kgis")


def require_kg_eval() -> ModuleType:
    """Import ``kg_eval`` (evaluation) or fail actionably."""
    return require_migration_module("kg_eval")


def require_kgcs() -> ModuleType:
    """Import ``kgcs`` (canonicalisation / entity resolution) or fail actionably."""
    return require_migration_module("kgcs")


__all__ = [
    "MigrationDependencyError",
    "is_migration_module_available",
    "require_kg_contracts",
    "require_kg_eval",
    "require_kgcs",
    "require_kgis",
    "require_migration_module",
]
