"""One hardened AST scanner, shared by the isolation and config-injection suites.

Both suites ask the same question — "can anything in this subpackage reach a
thing it must not reach?" — and both originally asked it with a matcher that a
reviewer evaded four ways out of six, with all nine tests green. The evasions
were not clever:

1. ``from agentic_kg.migration import neo4j`` — the old matcher read only
   ``node.module`` (``agentic_kg.migration``, innocuous) and never joined it to
   the alias. A reviewer obtained a live ``Neo4jCanonicalGraphStore`` handle
   through this form.
2. ``importlib.import_module("neo4j")`` — not an import *node* at all.
3. ``__import__("neo4j")`` — likewise.
4. **Any file in a subdirectory**, because the module list used ``glob`` rather
   than ``rglob``. This is the dangerous one precisely because it takes no
   cleverness: a contributor who adds a subpackage gets no isolation checking
   and no warning that they lost it.

A second review found a fifth hole, in the *fix* for those four:

5. **Every outward relative import.** ``imported_names`` skipped
   ``node.module is None`` and never read ``node.level``, so ``from .. import
   neo4j as _n`` passed all 29 isolation and config tests and yielded a live
   ``Neo4jCanonicalGraphStore``. The stated reason for skipping -- "it can only
   reach inside this package" -- is true for ``level == 1`` and plainly false
   for ``level >= 2``. ``from .. import neo4j`` is drift, not adversarial
   ingenuity, so the guard has to catch it. ``node.level`` is now resolved
   against the importing module's own package.

The lesson is the one §9.0 keeps re-teaching in a new costume. A check of the
form "assert nothing matched" passes when the *matcher* is broken, and that
looks identical to a check that passed because the property holds. So the
matcher is now one function with one set of rules, and ``test_isolation.py``
feeds it every evasion above and asserts each is caught — not as a nicety, but
because that is the only evidence the matcher discriminates at all.

A note on what this cannot do, corrected after review because the first version
claimed more than it delivers.

A determined evasion — ``getattr(importlib, "impor" + "t_module")(...)``, a name
assembled from ``chr()`` calls — is not statically detectable, and no AST check
will catch it. This guards against accident and drift, which is what actually
happens, and it is **not** a security boundary.

The earlier sentence "the real guarantee is structural: nothing in the
subpackage needs a canonical store" was **overstated and is withdrawn**. Two
reasons. This scan is *one-hop and syntactic*: it reads each module's own import
statements and never follows what those imports transitively pull in. Importing
this package already loads ``neo4j`` and ``agentic_kg.knowledge_graph.*`` into
``sys.modules`` by way of the allowed ``agentic_kg.extraction`` import, so those
modules are reachable at runtime from inside this package whatever these tests
say. And a check that has now been evaded five times should not be described as
a guarantee of anything.

The narrower claim is the true one, and it is worth stating exactly: **no module
in this subpackage names a canonical or production-graph module, or a canonical
write surface, in its own source**, and the runtime path this code takes never
constructs one. That is a real property, it is what these tests check, and it is
not the same as the canonical store being unreachable.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

#: Callables whose first string-literal argument names a module to import.
#: ``importlib.import_module`` and ``__import__`` are the two forms that
#: actually appear in real code.
_DYNAMIC_IMPORTERS = frozenset({"import_module", "__import__"})


def package_modules(package_dir: Path) -> list[Path]:
    """Every ``.py`` file in the package, **recursively**.

    ``rglob``, not ``glob``. A subdirectory is the zero-effort way to fall out
    of a non-recursive scan, and nothing would report it.
    """
    return sorted(
        path for path in package_dir.rglob("*.py") if "__pycache__" not in path.parts
    )


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def package_of(path: Path, root_dir: Path, root_package: str) -> str:
    """The dotted package *containing* ``path``.

    For a submodule and for an ``__init__.py`` alike, Python sets
    ``__package__`` to the containing package, and that is what a relative
    import resolves against. So ``ingestion/foo.py`` and
    ``ingestion/__init__.py`` both take ``from . import x`` to ``ingestion.x``.
    """
    relative = path.resolve().relative_to(root_dir.resolve())
    parts = list(relative.parts[:-1])  # drop the filename
    return ".".join([root_package, *parts]) if parts else root_package


def resolve_relative(module: str | None, level: int, package: str) -> str | None:
    """Resolve ``from .[..]module import ...`` to an absolute dotted name.

    ``level`` is the number of leading dots: 1 is the current package, and each
    further dot strips one component. Returns ``None`` when the import climbs
    past the root — unresolvable from here, and reported by the caller rather
    than silently dropped.
    """
    if level < 1:
        return module
    parts = package.split(".")
    if level - 1 > len(parts):
        return None
    base = parts[: len(parts) - (level - 1)]
    if not base:
        return None
    return ".".join([*base, module]) if module else ".".join(base)


def imported_names(tree: ast.AST, package: str | None = None) -> Iterator[tuple[int, str]]:
    """``(lineno, dotted_name)`` for every module this tree can import.

    Four sources, because a module can be named four ways:

    * ``import a.b.c`` / ``import a.b.c as x`` -> ``a.b.c``. The ``as`` is
      irrelevant: the binding name is not what gets imported.
    * ``from a.b import c`` -> **both** ``a.b`` and ``a.b.c``. Emitting only the
      first is the bug that let ``from agentic_kg.migration import neo4j``
      through; emitting only the second would miss ``from neo4j import
      GraphDatabase``. Both are needed.
    * ``from .. import c`` -> resolved against ``package``. The previous version
      skipped every relative import on the grounds that one "can only reach
      inside this package". True for one dot, false for two: ``from .. import
      neo4j`` reaches the canonical adapter, and a reviewer obtained a live
      store handle through it with all 29 tests green.
    * ``importlib.import_module("x")`` and ``__import__("x")`` -> ``x``.

    ``package`` is needed to resolve the relative forms. Without it they are
    skipped here and reported by :func:`unresolvable_relative_imports`, so a
    caller that forgets is told rather than silently under-scanned.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if package is None:
                    continue
                base = resolve_relative(node.module, node.level, package)
            else:
                base = node.module
            if base is None:
                continue
            yield node.lineno, base
            for alias in node.names:
                if alias.name != "*":  # `import *` names no module
                    yield node.lineno, f"{base}.{alias.name}"
        elif isinstance(node, ast.Call):
            target = _dynamic_import_target(node)
            if target is not None:
                yield node.lineno, target


def unresolvable_relative_imports(tree: ast.AST, package: str | None) -> list[int]:
    """Lines where a relative import cannot be resolved to an absolute name.

    Either it climbs past the package root, or no ``package`` was supplied.
    Like an opaque dynamic import this is a hole in the scan, not an all-clear,
    so the isolation suite fails on one rather than passing over it.
    """
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.level
        and (package is None or resolve_relative(node.module, node.level, package) is None)
    ]


def imported_symbols(
    tree: ast.AST, package: str | None = None
) -> Iterator[tuple[int, str, str]]:
    """``(lineno, module, symbol)`` for every ``from module import symbol``.

    Catches a *name* being pulled in — a canonical write surface — as opposed to
    a module being reached. Relative forms are resolved exactly as
    :func:`imported_names` resolves them, so ``from ..neo4j.store import
    Neo4jCanonicalGraphStore`` is caught rather than skipped.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            if package is None:
                continue
            module = resolve_relative(node.module, node.level, package)
        else:
            module = node.module
        if module is None:
            continue
        for alias in node.names:
            yield node.lineno, module, alias.name


def called_names(tree: ast.AST) -> Iterator[tuple[int, str]]:
    """``(lineno, name)`` for every call, by its bare or attribute name.

    ``f()`` yields ``f``; ``mod.f()`` yields ``f``. Deliberately insensitive to
    the receiver, so ``config.get_migration_config()`` and a bare
    ``get_migration_config()`` are both caught.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            yield node.lineno, func.id
        elif isinstance(func, ast.Attribute):
            yield node.lineno, func.attr


def matches_prefix(name: str, prefixes: tuple[str, ...]) -> bool:
    """Whether ``name`` is one of ``prefixes`` or lives underneath one."""
    return any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes)


def root_of(name: str) -> str:
    return name.split(".", 1)[0]


def _dynamic_import_target(node: ast.Call) -> str | None:
    """The module named by ``import_module("x")`` / ``__import__("x")``."""
    func = node.func
    if isinstance(func, ast.Name):
        name = func.id
    elif isinstance(func, ast.Attribute):
        name = func.attr
    else:
        return None
    if name not in _DYNAMIC_IMPORTERS or not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def opaque_dynamic_imports(tree: ast.AST) -> list[int]:
    """Lines where a dynamic import's target is not a string literal.

    An unresolvable target is not an all-clear — it is a hole in the scan. The
    isolation suite fails on one rather than passing over it, so a future
    ``import_module(name)`` has to be justified rather than silently exempt.
    """
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        if name not in _DYNAMIC_IMPORTERS or not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            lines.append(node.lineno)
    return lines


__all__ = [
    "called_names",
    "imported_names",
    "imported_symbols",
    "matches_prefix",
    "opaque_dynamic_imports",
    "package_modules",
    "package_of",
    "parse",
    "resolve_relative",
    "root_of",
    "unresolvable_relative_imports",
]
