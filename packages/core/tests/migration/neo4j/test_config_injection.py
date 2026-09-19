"""AC-15c: injected `MigrationConfig`, and the optional packages behind the guard.

ADR-0004 decisions 3 and 4:

* downstream code takes an **injected** ``MigrationConfig`` and does not call
  ``get_migration_config()`` inline — the singleton is a cached process-global,
  which cannot express running the legacy and KGIS/KGCS paths side by side in
  one process to diff them, and the diff phase is planned;
* every access to the optional packages goes through
  ``agentic_kg.migration.imports``, so a missing ``migration`` extra fails with
  a message naming the distribution, the pin and the install command instead of
  a bare ``ModuleNotFoundError`` from somewhere deep in the adapter.

Both are asserted statically over this subpackage's source, because both are
properties of *how the code is written* and no runtime behaviour distinguishes
them once the extra happens to be installed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from agentic_kg.migration.config import MigrationConfig
from agentic_kg.migration.neo4j import MigrationDisabledError, canonical_store_from_driver

PACKAGE_DIR = (
    Path(__file__).resolve().parents[5]
    / "packages"
    / "core"
    / "src"
    / "agentic_kg"
    / "migration"
    / "neo4j"
)
GUARD_MODULE = "_contracts.py"


def _modules() -> list[Path]:
    files = sorted(PACKAGE_DIR.glob("*.py"))
    assert len(files) >= 5, f"expected the adapter modules, found {files}"
    return files


def test_no_module_calls_the_config_singleton() -> None:
    """ADR-0004 decision 4, enforced rather than remembered."""
    offenders: list[str] = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name == "get_migration_config":
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], f"inline singleton use: {offenders}"


def test_only_the_guard_module_imports_the_optional_packages() -> None:
    """ADR-0004 decision 3 — one door, and it is guarded.

    A guard placed only in ``__init__.py`` would be bypassed by
    ``import agentic_kg.migration.neo4j.store``; requiring every module to reach
    ``kg_contracts`` through ``_contracts`` means the guard runs whichever
    module is imported first.
    """
    optional_roots = {"kg_contracts", "kgis", "kg_eval", "kgcs"}
    offenders: list[str] = []
    for path in _modules():
        if path.name == GUARD_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            for module in modules:
                if module.split(".")[0] in optional_roots:
                    offenders.append(f"{path.name}:{node.lineno} -> {module}")
    assert offenders == [], (
        "these modules import an optional package directly instead of through "
        f"_contracts.py: {offenders}"
    )


def test_the_guard_module_actually_guards() -> None:
    """And the one exempt module really does call the guard, before importing."""
    source = (PACKAGE_DIR / GUARD_MODULE).read_text(encoding="utf-8")
    tree = ast.parse(source)
    guard_line = next(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "require_kg_contracts"
    )
    first_optional_import = min(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("kg_contracts")
    )
    assert guard_line < first_optional_import


def test_store_construction_requires_the_kgcs_flag() -> None:
    """The opt-in seam is load-bearing, not decorative.

    Building the KGCS canonical store while the KGCS path is switched off would
    be a hole in the opt-in guarantee ADR-0004 exists to provide, so it is
    refused — by name, with the flag to set.
    """
    disabled = MigrationConfig(use_kgis_ingestion=True, use_kgcs_resolution=False)
    with pytest.raises(MigrationDisabledError) as excinfo:
        canonical_store_from_driver(object(), disabled)  # type: ignore[arg-type]
    assert "KGCS_RESOLUTION_ENABLED" in str(excinfo.value)


def test_the_flag_check_reads_the_injected_config_not_the_environment(
    monkeypatch: pytest.MonkeyPatch, canonical_driver
) -> None:
    """The environment says off; the injected config says on; the config wins.

    This is the property the singleton cannot provide, and the reason ADR-0004
    committed to injection while there were zero call sites.
    """
    monkeypatch.delenv("KGCS_RESOLUTION_ENABLED", raising=False)
    enabled = MigrationConfig(use_kgcs_resolution=True)
    assert MigrationConfig().use_kgcs_resolution is False, "environment must say off"

    store = canonical_store_from_driver(canonical_driver, enabled, namespace="cfg-injection-test")
    assert store.current_epoch() == 0
    assert store.namespace == "cfg-injection-test"
