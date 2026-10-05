"""One door onto the optional packages, and the guard runs before it.

Three properties, each with the deliberately-offending counter-fixture that
proves the check can fire. A scan that matched nothing and a scan that cannot
match look identical in a green check, and this suite has an unusually easy
way to become the second: ``modules()`` returning an empty list would make
every assertion here vacuously true.
"""

from __future__ import annotations

import pytest
from agentic_kg.migration.imports import MigrationDependencyError

from . import _scan


def test_the_scanner_actually_sees_the_subpackage() -> None:
    """The set every other test quantifies over is non-empty and complete."""
    names = {p.stem for p in _scan.modules()}
    assert {"__init__", "_contracts", "pipeline", "policy", "arm_export", "rollback"} <= names


def test_only_the_gate_module_names_an_optional_package() -> None:
    offenders: dict[str, set[str]] = {}
    for path in _scan.modules():
        if path.stem == _scan.GATE_MODULE:
            continue
        roots = _scan.imported_roots(path.read_text(encoding="utf-8"), package_depth=1)
        named = roots & _scan.OPTIONAL_ROOTS
        if named:
            offenders[path.name] = named
    assert offenders == {}, (
        f"these modules import an optional distribution directly, bypassing the "
        f"guard in _contracts.py: {offenders}"
    )


def test_the_gate_module_does_name_them() -> None:
    """The control: the rule above is satisfiable by a package that imports nothing."""
    gate = next(p for p in _scan.modules() if p.stem == _scan.GATE_MODULE)
    roots = _scan.imported_roots(gate.read_text(encoding="utf-8"))
    assert {"kgcs", "kg_contracts"} <= roots


@pytest.mark.parametrize(
    "source",
    [
        "import kgcs",
        "from kgcs.engine import CurationEngine",
        "from kgcs import engine as e",
        "import importlib\nm = importlib.import_module('kgcs')",
        "m = __import__('kgcs')",
    ],
    ids=["import", "from-import", "aliased", "import_module", "dunder"],
)
def test_the_scanner_catches_every_import_form(source: str) -> None:
    """The detector discriminates — pointed at each evasion, it fires.

    Without this, ``test_only_the_gate_module_names_an_optional_package`` would
    pass just as well against a matcher that returns the empty set for
    everything, which is how the neighbouring scanner was evaded four ways with
    all its tests green.
    """
    assert _scan.imported_roots(source) & _scan.OPTIONAL_ROOTS


def test_the_guard_runs_before_the_imports_it_guards() -> None:
    """A guard placed after the import it protects is decoration.

    The whole point of ``_contracts.py`` is that a missing extra produces an
    actionable ``MigrationDependencyError`` rather than a bare
    ``ModuleNotFoundError`` from several frames deep. If ``require_kgcs()`` ran
    after ``from kgcs.engine import ...``, the bare error would escape first and
    the guard would never be reached.
    """
    gate = next(p for p in _scan.modules() if p.stem == _scan.GATE_MODULE)
    source = gate.read_text(encoding="utf-8")
    guard_lines = _scan.calls_named(source, "require_kgcs") + _scan.calls_named(
        source, "require_kg_contracts"
    )
    assert guard_lines, "_contracts.py calls no require_* guard at all"
    first_import = _scan.first_optional_import_line(source)
    assert first_import is not None
    assert max(guard_lines) < first_import, (
        f"a require_* guard at line {max(guard_lines)} runs after the first "
        f"optional import at line {first_import}"
    )


def test_the_guard_ordering_check_can_fail() -> None:
    """The control for the ordering check, on a source with the order inverted."""
    inverted = "from kgcs.engine import CurationEngine\nrequire_kgcs()\n"
    guard = _scan.calls_named(inverted, "require_kgcs")
    first_import = _scan.first_optional_import_line(inverted)
    assert guard and first_import is not None
    assert not max(guard) < first_import


def test_the_dependency_error_is_what_a_missing_extra_raises() -> None:
    """The guard's error type is the actionable one, not a bare ImportError."""
    assert issubclass(MigrationDependencyError, ImportError)
