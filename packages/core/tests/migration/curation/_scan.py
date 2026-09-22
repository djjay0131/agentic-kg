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
#
# **Polarity.** This scan does not enumerate the shapes a store may not appear
# in. It enumerates the positions a store may appear in, and reports every other
# occurrence.
#
# That inversion is the whole design, and it was arrived at the hard way. Three
# successive versions enumerated bad shapes; each was defeated, and the third —
# which claimed in its own docstring that "the class is closed" — was walked
# through by an independent reviewer **nine more ways**: list, generator and
# dict comprehensions, lambda bodies, default arguments, ``yield``, aliases
# created through ``IfExp`` and ``BoolOp``, and class-body bindings. Four of
# them, injected into the real ``pipeline.py``, passed the entire suite green,
# and one produced a live module-global write surface.
#
# A shape-enumerating check protects against the shapes someone thought of. It
# cannot be completed by thinking harder, because the language keeps offering
# new positions. A position whitelist fails the other way: a construct nobody
# anticipated is unrecognised, and unrecognised is *reported*.
#
# What this still is not: it is syntactic and one-hop. An alias built through a
# closure, ``getattr``, or ``globals()`` is not statically detectable by any AST
# walk, and this is not a security boundary. What changed is the direction it
# fails in.

STORE_PARAM = "store"

#: Substring identifying a parameter annotated as a canonical write surface, so
#: a parameter that holds one under another name is still a root. The fifth hole
#: review found was structural rather than syntactic: a *new* module with an
#: unannotated ``store`` parameter calling ``store.apply(...)`` was scanned by
#: neither check, because the module list was transcribed. Roots are now derived
#: from parameters, and so is the set of modules worth scanning.
STORE_ANNOTATION = "GraphMutationStore"


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _parameters(node: ast.AST):
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return
    a = node.args
    for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]:
        if arg is not None:
            yield arg


def store_roots(tree: ast.AST) -> set[str]:
    """Parameter names that receive a canonical store, by name or annotation."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        for arg in _parameters(node):
            if arg.arg == STORE_PARAM:
                roots.add(arg.arg)
            elif arg.annotation is not None and STORE_ANNOTATION in ast.unparse(arg.annotation):
                roots.add(arg.arg)
    return roots


def assignments(tree: ast.AST):
    """Every ``(node, target, value)`` binding in ``tree``, one row per target."""
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

    Assigning a store to one of these publishes it into an enclosing scope, so
    such a binding is an escape rather than a local alias. Collected
    module-wide, which is deliberately over-broad in the safe direction.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
    return names


def store_aliases(tree: ast.AST, roots: set[str] | None = None) -> set[str]:
    """Every local name that transitively holds a store.

    Only a binding whose target is a plain local ``Name`` and whose value is
    already an alias extends the set. Everything else that *contains* an alias —
    a comprehension, a lambda, a subscript target — is not an alias to keep
    following; it is an escape, and :func:`store_offenders` reports it as one.

    A ``global``/``nonlocal`` target is deliberately **not** excluded here, and
    this is the second time that line has been deleted. :func:`store_offenders`
    consults :func:`rebound_names` independently, so the escaping binding is
    reported either way and no mutation of the exclusion can change an outcome.
    What excluding it *did* change was the report: it stopped the alias
    propagating, so every downstream use of the escaped name went unmentioned.
    Following the alias costs nothing and reports strictly more.
    """
    aliases = set(roots if roots is not None else store_roots(tree))
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
    """Does ``expr`` evaluate to a store alias, seeing through a walrus?"""
    while isinstance(expr, ast.NamedExpr):
        expr = expr.value
    return isinstance(expr, ast.Name) and expr.id in aliases


def store_offenders(
    source: str,
    filename: str,
    *,
    permitted_callees: frozenset[str],
    permitted_attributes: frozenset[str],
) -> list[str]:
    """Every use of a store alias that is not in a permitted position.

    The three permitted positions, and nothing else:

    * an argument to a callee in ``permitted_callees``;
    * the receiver of an attribute in ``permitted_attributes``;
    * the value of a binding whose target is a plain local name — which creates
      another alias, and every use of *that* is checked by this same rule;
    * either side of an ``is`` / ``is not`` comparison against ``None``, which
      retains no reference. This is the one position real code needed that the
      first draft of the whitelist did not have, and it was found the right way
      round: the rule reported ``if store is not None`` in ``pipeline.py`` and
      the position was added deliberately, rather than the construct slipping
      through unnoticed.
    """
    tree = ast.parse(source, filename=filename)
    aliases = store_aliases(tree)
    if not aliases:
        return []
    parents = _parent_map(tree)
    escaping = rebound_names(tree)

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Name) and node.id in aliases):
            continue
        if not isinstance(node.ctx, ast.Load):
            continue
        verdict = _position(node, parents, escaping, permitted_callees, permitted_attributes)
        if verdict is not None:
            offenders.append(f"{filename}:{node.lineno} {verdict}")
    return sorted(offenders)


def _position(node, parents, escaping, permitted_callees, permitted_attributes) -> str | None:
    """``None`` if this use is permitted, else why it is reported."""
    parent = parents.get(node)

    # A walrus is transparent: judge the binding it sits in.
    while isinstance(parent, ast.NamedExpr) and parent.value is node:
        node, parent = parent, parents.get(parent)

    if isinstance(parent, ast.Call) and any(
        arg is node for arg in parent.args
    ) or (
        isinstance(parent, ast.keyword) and parent.value is node
    ):
        call = parent if isinstance(parent, ast.Call) else parents.get(parent)
        callee = _call_name(call) if isinstance(call, ast.Call) else None
        if callee in permitted_callees:
            return None
        return f"passed to {callee or '<expr>'}"

    if isinstance(parent, ast.Compare):
        operands = [parent.left, *parent.comparators]
        only_identity = all(isinstance(op, (ast.Is, ast.IsNot)) for op in parent.ops)
        against_none = all(
            isinstance(other, ast.Constant) and other.value is None
            for other in operands
            if other is not node
        )
        if only_identity and against_none:
            return None
        return "compared with something other than None"

    if isinstance(parent, ast.Attribute) and parent.value is node:
        if parent.attr in permitted_attributes:
            return None
        return f"dereferenced .{parent.attr}"

    if isinstance(parent, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
        targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
        # A binding in a class body is a class attribute, not a local alias:
        # it outlives the function and is reachable from the class object.
        in_class_body = isinstance(parents.get(parent), ast.ClassDef)
        if not in_class_body and all(
            isinstance(t, ast.Name) and t.id not in escaping for t in targets
        ):
            return None
        if in_class_body:
            return "bound into a class attribute"
        shapes = ", ".join(
            "global binding" if isinstance(t, ast.Name) else type(t).__name__.lower()
            for t in targets
        )
        return f"bound into {shapes}"

    return f"used in {type(parent).__name__ if parent is not None else 'module'}"


def store_bearing_modules(paths) -> list:
    """The modules a store actually reaches, derived rather than transcribed.

    A transcribed list is the fifth hole review found: a new module taking an
    unannotated ``store`` parameter was outside it, so nothing scanned the
    ``store.apply(...)`` inside.
    """
    found = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if store_roots(tree):
            found.append(path)
    return found
