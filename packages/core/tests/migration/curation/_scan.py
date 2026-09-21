"""A small, deliberately un-clever AST scanner for this subpackage.

The hardened scanner next door (``tests/migration/ingestion/astscan.py``) is
owned by another suite and is not importable across a ``conftest`` boundary, so
this is a local one. It answers two questions and no others: which modules exist
under the subpackage, and which *names* each module's own source mentions.

Its limits are the same ones that scanner records the hard way, and they are
stated rather than implied. It is **one-hop and syntactic**: it reads each
module's own statements and never follows what those imports transitively pull
in. A name assembled at runtime — ``getattr(importlib, "impor" + "t_module")``
— is not statically detectable and never will be. This guards against accident
and drift, which is what actually happens; it is not a security boundary.

``test_import_gate.py`` points every check here at a deliberately offending
fixture and asserts each one fires, because a scan that matched nothing and a
scan that cannot match look identical in a green check.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
PACKAGE = REPO_ROOT / "packages" / "core" / "src" / "agentic_kg" / "migration" / "curation"

#: The module allowed to name the optional distributions: the import gate.
GATE_MODULE = "_contracts"

#: Top-level modules provided by the opt-in ``migration`` extra.
OPTIONAL_ROOTS = frozenset({"kgcs", "kgis", "kg_contracts", "kg_eval"})


def modules() -> list[Path]:
    """Every module in the subpackage. ``rglob``, not ``glob``.

    A scanner that used ``glob`` would give a future subdirectory no checking at
    all, with no warning that it had lost it — which is exactly how the
    neighbouring scanner was evaded.
    """
    assert PACKAGE.is_dir(), f"curation subpackage not found at {PACKAGE}"
    return sorted(PACKAGE.rglob("*.py"))


def imported_roots(source: str, *, package_depth: int = 0) -> set[str]:
    """Top-level module names this source imports, by any import form.

    Handles the four forms that have actually been used to slip past a matcher:
    ``import a.b``; ``from a import b`` joined to its alias, so
    ``from agentic_kg.migration import neo4j`` yields ``agentic_kg`` *and*
    ``agentic_kg.migration.neo4j``'s leaf; ``importlib.import_module("x")``; and
    ``__import__("x")``. Outward relative imports (``from .. import x``) are
    resolved by level rather than skipped.
    """
    roots: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
                roots.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level >= package_depth:
                # Reaches outside this package; the imported names are the leaves.
                for alias in node.names:
                    roots.add(alias.name)
            module = node.module or ""
            if module:
                roots.add(module.split(".")[0])
                roots.add(module)
                for alias in node.names:
                    roots.add(f"{module}.{alias.name}")
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name in {"import_module", "__import__"} and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    roots.add(first.value.split(".")[0])
                    roots.add(first.value)
    return roots


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def calls_named(source: str, name: str) -> list[int]:
    """Line numbers at which ``name`` is *called* in this source."""
    tree = ast.parse(source)
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node) == name
    ]


def first_optional_import_line(source: str) -> int | None:
    """Line of the first import of an optional distribution, if any."""
    tree = ast.parse(source)
    lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and _import_roots_of(node) & OPTIONAL_ROOTS
    ]
    return min(lines) if lines else None


def _import_roots_of(node: ast.Import | ast.ImportFrom) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.name.split(".")[0] for alias in node.names}
    return {(node.module or "").split(".")[0]}
