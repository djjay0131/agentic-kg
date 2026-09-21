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


def assignments(tree: ast.AST):
    """Every ``(node, target, value)`` binding in ``tree``, one row per target.

    Covers the four binding forms a store can travel through — ``x = store``,
    ``x: T = store``, ``x += store`` and the walrus ``(x := store)``. The walrus
    is here because it was the fourth of four evasions an independent reviewer
    used against the previous version: ``store_aliases`` read only ``Assign``
    and ``AnnAssign``, so ``(alias := store)`` bound a name the scanner had
    never heard of and every later use of it was invisible.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                yield node, target, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            yield node, node.target, node.value
        elif isinstance(node, ast.AugAssign):
            yield node, node.target, node.value
        elif isinstance(node, ast.NamedExpr):
            yield node, node.target, node.value


def rebound_names(tree: ast.AST) -> set[str]:
    """Names declared ``global`` or ``nonlocal`` anywhere in ``tree``.

    Assigning a store to one of these does not create a local alias — it
    publishes the store into an enclosing or module scope, where anything can
    reach it. The set is collected module-wide rather than per-scope, which is
    deliberately *over*-broad: a name that is ``global`` in one function and a
    plain local in another is treated as escaping in both. Over-broad is the
    safe direction for this check, and
    ``test_the_detector_passes_the_legal_shapes`` keeps it from becoming
    over-broad enough to flag correct code.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
    return names


def store_aliases(tree: ast.AST, root: str = STORE_PARAM) -> set[str]:
    """``root`` plus every name transitively assigned from it in ``tree``.

    Review of the first version of this scanner defeated it with one line:
    ``_s = store`` and then leaking ``_s``, which the matcher never saw because
    it looked only for the literal name ``store``. Assignment is followed to a
    fixed point, so a chain (``a = store; b = a``) is tracked too, and — since
    the second review — so is a walrus binding.

    A name declared ``global`` or ``nonlocal`` is tracked here like any other,
    *and* reported as an escape by :func:`store_reachings` — both, deliberately.
    An earlier draft excluded it from the alias set as well, which read like
    safety work and was inert: the escape is flagged from ``store_reachings``'
    own scan, so the exclusion changed no outcome and only removed later uses of
    the name from view. The round-3 mutation battery caught it by reporting the
    mutation of that line as *survived* — a line no test could kill is a line
    doing nothing.

    Still syntactic, and still not a security boundary: an alias built through
    a closure, ``getattr`` or ``globals()`` is not detectable this way. It
    catches accident and drift, which is what happens.
    """
    aliases = {root}
    changed = True
    while changed:
        changed = False
        for _node, target, value in assignments(tree):
            if not _is_alias(value, aliases):
                continue
            if isinstance(target, ast.Name) and target.id not in aliases:
                aliases.add(target.id)
                changed = True
    return aliases


def _is_alias(expr: ast.expr | None, aliases: set[str]) -> bool:
    """Does ``expr`` evaluate to a store alias, seeing through a walrus?

    ``Sink(alias := store)`` passes the store as an argument, but the argument
    node is a ``NamedExpr``, not a ``Name`` — which is how the inline walrus
    slipped past the first attempt at closing this class. Unwrapping here means
    every use site (argument, element, return, receiver) gets the same reading
    instead of each growing its own special case.
    """
    while isinstance(expr, ast.NamedExpr):
        expr = expr.value
    return isinstance(expr, ast.Name) and expr.id in aliases


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
    * ``"escape"`` — the alias leaves this function without a call. Two rounds
      of review found this family one instance at a time, so it is now closed
      as a *class* rather than as the cases that happened to be demonstrated:
      a container display or a ``return`` (round one, ``_s = store`` then
      ``_sink = (_s,)``), and **any binding whose target is not a plain local
      name** (round two, ``REG['canonical'] = store`` — a subscript target, so
      no alias was created and nothing else in the scan looked at it). The
      target forms now covered are subscript, attribute, and a name declared
      ``global``/``nonlocal``; the walrus is handled by :func:`store_aliases`,
      which now tracks it. ``name`` says which shape.
    """
    found: list[tuple[int, str, str]] = []
    escaping = rebound_names(tree)
    for node, target, value in assignments(tree):
        if not _is_alias(value, aliases):
            continue
        if isinstance(target, ast.Subscript):
            found.append((node.lineno, "escape", "subscript assignment"))
        elif isinstance(target, ast.Attribute):
            found.append((node.lineno, "escape", "attribute assignment"))
        elif isinstance(target, ast.Name) and target.id in escaping:
            found.append((node.lineno, "escape", f"global binding of {target.id!r}"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            if any(_is_alias(e, aliases) for e in node.elts):
                found.append((node.lineno, "escape", type(node).__name__.lower()))
        elif isinstance(node, ast.Dict):
            if any(_is_alias(v, aliases) for v in node.values):
                found.append((node.lineno, "escape", "dict"))
        elif isinstance(node, ast.Return):
            if _is_alias(node.value, aliases):
                found.append((node.lineno, "escape", "return"))
        elif isinstance(node, ast.Attribute):
            if _is_alias(node.value, aliases):
                found.append((node.lineno, "attr", node.attr))
        elif isinstance(node, ast.Call):
            used = any(_is_alias(a, aliases) for a in node.args) or any(
                _is_alias(k.value, aliases) for k in node.keywords
            )
            if used:
                found.append((node.lineno, "arg", _call_name(node) or "<expr>"))
    return found
