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


# --------------------------------------------------------------------------
# Store-reachability scan (see test_no_application_write_surface.py)
# --------------------------------------------------------------------------

#: The parameter name a canonical ``GraphMutationStore`` arrives under in this
#: subpackage. Every module that takes one calls it ``store``.
STORE_PARAM = "store"


def store_aliases(tree: ast.AST, root: str = STORE_PARAM) -> set[str]:
    """``root`` plus every name transitively assigned from it in ``tree``.

    Review of the first version of this scanner defeated it with one line:
    ``_s = store`` and then leaking ``_s``, which the matcher never saw because
    it looked only for the literal name ``store``. Assignment is followed to a
    fixed point, so a chain (``a = store; b = a``) is tracked too.

    Still syntactic, and still not a security boundary: an alias built through
    a container, a closure or ``globals()`` is not detectable this way. It
    catches accident and drift, which is what happens.
    """
    aliases = {root}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            value = None
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                value, targets = node.value, list(node.targets)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                value, targets = node.value, [node.target]
            if not isinstance(value, ast.Name) or value.id not in aliases:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in aliases:
                    aliases.add(target.id)
                    changed = True
    return aliases


def store_reachings(tree: ast.AST, aliases: set[str]) -> list[tuple[int, str, str]]:
    """Every place a store alias is used, as ``(line, kind, name)``.

    Three kinds, because the first version of this scan only had one:

    * ``"arg"`` — the alias passed as a positional or keyword argument. ``name``
      is the callee.
    * ``"attr"`` — the alias used as a *receiver*, ``store.something``. ``name``
      is the attribute. Nothing checked this before, so a literal
      ``store.apply(batch, ())`` — a direct canonical write, on a dead branch or
      otherwise — passed the whole suite green. The module docstring called that
      "obvious"; obvious is not caught.
    * ``"escape"`` — the alias placed in a container display or returned, which
      needs no call at all. The reviewer's evasion was exactly this: ``_s =
      store`` and then ``_sink = (_s,)``. ``name`` says which shape.
    """
    found: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            if any(isinstance(e, ast.Name) and e.id in aliases for e in node.elts):
                found.append((node.lineno, "escape", type(node).__name__.lower()))
        elif isinstance(node, ast.Dict):
            if any(
                isinstance(v, ast.Name) and v.id in aliases
                for v in node.values
                if v is not None
            ):
                found.append((node.lineno, "escape", "dict"))
        elif isinstance(node, ast.Return):
            if isinstance(node.value, ast.Name) and node.value.id in aliases:
                found.append((node.lineno, "escape", "return"))
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in aliases:
                found.append((node.lineno, "attr", node.attr))
        elif isinstance(node, ast.Call):
            used = any(
                isinstance(a, ast.Name) and a.id in aliases for a in node.args
            ) or any(
                isinstance(k.value, ast.Name) and k.value.id in aliases
                for k in node.keywords
            )
            if used:
                found.append((node.lineno, "arg", _call_name(node) or "<expr>"))
    return found
