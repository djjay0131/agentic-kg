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
import importlib.util
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


def _root_is_installed(root: str) -> bool:
    """Is the top-level distribution package present on this interpreter?

    This is the guard's discriminator, and it must not be inferred from the
    *failure* that raised. An earlier version compared ``exc.name`` against
    ``root``, which mislabelled every intra-package failure: importing
    ``kgis.does_not_exist`` from a perfectly good kgis install raises
    ``ModuleNotFoundError(name="kgis.does_not_exist")``, whose first segment is
    ``kgis`` — so the guard concluded the extra was missing and
    :func:`is_migration_module_available` quietly returned ``False``. A caller
    branching on that would take the legacy path on a broken install without
    anyone noticing: precisely the agentic-kgis #39 failure mode this module
    exists to prevent.

    Asking the import system directly is the only honest answer.
    """
    try:
        return importlib.util.find_spec(root) is not None
    except (ImportError, ValueError):
        # ValueError: the module sits in sys.modules as None (how tests and
        # some import hooks simulate absence). Either way: not usable.
        return False


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
        ModuleNotFoundError: The root package IS installed but the import
            still failed — a missing submodule, or a missing dependency of the
            package itself. This is a broken install, not a missing extra, so
            it is raised loudly with the real culprit named rather than being
            softened into "install the extra".
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
    except ImportError as exc:
        # Decide from the import system, NOT from the exception: see
        # _root_is_installed. If the root package is present, this failure is
        # a broken install and must never be relabelled "extra not installed".
        if _root_is_installed(root):
            culprit = getattr(exc, "name", None)
            detail = (
                f"its dependency {culprit!r} is missing"
                if culprit and culprit != module_name
                else "it could not be resolved"
            )
            raise ModuleNotFoundError(
                f"{module_name!r} could not be imported even though "
                f"{root!r} IS installed: {detail}. This is a broken "
                f"install of {distribution}, not a missing 'migration' extra. "
                f"Reinstalling the extra will not fix it; install the missing "
                f"dependency or re-pin {distribution}.\n"
                f"Original error: {exc}",
                name=culprit,
                path=getattr(exc, "path", None),
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

    Returns ``False`` only for the genuinely-not-installed case. A *broken*
    install still raises: returning ``False`` there would let a caller branch
    quietly onto the legacy path while the real problem went unreported, which
    is the failure this module is built to avoid.
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
