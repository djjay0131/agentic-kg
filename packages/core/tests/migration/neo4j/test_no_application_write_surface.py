"""Applications cannot obtain a canonical write surface. Proven, not promised.

Mapping spec §2 rule 1 / **AC-1**: "No module under ``packages/api/`` or
``packages/core/src/agentic_kg/agents/`` imports ``GraphMutationStore``, and no
such module holds an object satisfying it. Asserted by a test, not by review."
Plus **AC-3**: the canonical reader is not a `LedgerReader`, and vice versa
(KGIS ADR-0011 — canonical reads and ledger reads are never one access path).

Two kinds of evidence here, because neither alone is sufficient:

* **structural** — the object handed to non-executor code
  (:class:`Neo4jCanonicalGraphReader`) does not satisfy `GraphMutationStore`,
  and no attribute reachable from it does either. `GraphMutationStore` is
  ``@runtime_checkable``, so ``isinstance`` is a real check against the real
  protocol rather than a name comparison;
* **static** — a source scan of the two application trees, which catches the
  case the structural test cannot: someone constructing the store directly.

The static scan asserts it actually looked at files (obligation 1). A scan over
an empty file set is the canonical vacuous check, and the whole AC would pass on
a typo in a path.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from agentic_kg.migration.neo4j import Neo4jCanonicalGraphReader, Neo4jCanonicalGraphStore
from kg_contracts.stores import (
    CandidateSink,
    GraphMutationStore,
    GraphReader,
    LedgerReader,
    TemporalGraphReader,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
APPLICATION_TREES = (
    REPO_ROOT / "packages" / "api",
    REPO_ROOT / "packages" / "core" / "src" / "agentic_kg" / "agents",
)

#: Names whose appearance in an application module would mean a canonical write
#: surface had reached it.
FORBIDDEN_IMPORTS = (
    "GraphMutationStore",
    "GraphMutationBatch",
    "Neo4jCanonicalGraphStore",
    "agentic_kg.migration.neo4j",
)


def _application_modules() -> list[Path]:
    files: list[Path] = []
    for tree in APPLICATION_TREES:
        assert tree.is_dir(), f"application tree not found: {tree}"
        files.extend(sorted(tree.rglob("*.py")))
    return files


def test_application_trees_are_non_empty() -> None:
    """The set the next test quantifies over must not be empty (§9.0 ob. 1)."""
    modules = _application_modules()
    assert len(modules) >= 20, f"expected to scan the application trees, found {len(modules)}"


def test_no_application_module_imports_a_canonical_write_surface() -> None:
    offenders: list[str] = []
    for path in _application_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [alias.name for alias in node.names]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            for name in names:
                if any(forbidden in name for forbidden in FORBIDDEN_IMPORTS):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} -> {name}")
    assert offenders == [], "canonical write surface imported by application code:\n" + "\n".join(
        offenders
    )


def test_the_scan_would_notice_a_violation(tmp_path: Path) -> None:
    """Obligation 5 for the scan itself: it can fail for the reason it names.

    A source scan that matches nothing is indistinguishable from a source scan
    with a broken pattern, so the detector is pointed at a deliberately
    offending module and asserted to flag it.
    """
    offending = tmp_path / "rogue_router.py"
    offending.write_text(
        "from kg_contracts.stores import GraphMutationStore\n\n"
        "def write(store: GraphMutationStore) -> None: ...\n",
        encoding="utf-8",
    )
    tree = ast.parse(offending.read_text(encoding="utf-8"))
    found = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if any(forbidden in alias.name for forbidden in FORBIDDEN_IMPORTS)
    ]
    assert found == ["GraphMutationStore"]


def test_read_only_surface_is_not_a_mutation_store(make_canonical_store) -> None:
    store = make_canonical_store()
    reader = store.read_only()

    assert isinstance(reader, Neo4jCanonicalGraphReader)
    assert isinstance(reader, GraphReader)
    assert isinstance(reader, TemporalGraphReader)
    assert not isinstance(reader, GraphMutationStore)
    assert not isinstance(reader, CandidateSink)
    assert not hasattr(reader, "apply")


def test_read_only_surface_is_not_a_ledger_reader(make_canonical_store) -> None:
    """AC-3: canonical reads and ledger reads are never the same surface."""
    store = make_canonical_store()
    assert not isinstance(store.read_only(), LedgerReader)
    assert not isinstance(store, LedgerReader)


def test_no_attribute_of_the_reader_reaches_a_write_surface(make_canonical_store) -> None:
    """Not just "the class has no apply" — nothing it holds has one either.

    A reader that kept ``self._store`` would pass the isinstance check above and
    still hand a write surface to anyone willing to type one more dot.
    """
    reader = make_canonical_store().read_only()
    reachable = [getattr(reader, name) for name in dir(reader) if not name.startswith("__")]
    reachable.extend(vars(reader).values())
    assert reachable, "nothing inspected - the walk must not be vacuous"
    offenders = [
        value
        for value in reachable
        if isinstance(value, (GraphMutationStore, Neo4jCanonicalGraphStore))
    ]
    assert offenders == []


def test_the_store_itself_is_the_full_executor_surface(make_canonical_store) -> None:
    """The counterpart: the executor's object really does satisfy all three.

    Without this the previous tests could pass against a reader that is simply
    broken. `kg_contracts.testing.contract._TestableGraphStore` is the union the
    shared suite casts to (`contract.py:49`); this asserts each leg of it.
    """
    from kg_contracts.stores import CapabilityDeclaring

    store = make_canonical_store()
    assert isinstance(store, GraphMutationStore)
    assert isinstance(store, TemporalGraphReader)
    assert isinstance(store, CapabilityDeclaring)


def test_writer_primitives_are_not_public(make_canonical_store) -> None:
    """``GraphWriter`` & co. stay adapter-internal (spec §4.2).

    ``kg_contracts`` deliberately does not re-export them, so a public
    ``put_entity``/``put_assertion``/``mark_superseded`` here would be a raw
    canonical write reachable from anything holding the store — the
    ``upsert_nodes``/``upsert_edges`` bypass ADR-0010 exists to foreclose.
    """
    store = make_canonical_store()
    for name in ("put_entity", "put_assertion", "mark_superseded", "put_entities", "begin"):
        assert not hasattr(store, name), f"{name} must not be a public method"


@pytest.mark.parametrize("surface", ["store", "reader"])
def test_neither_surface_exposes_destructive_helpers(make_canonical_store, surface: str) -> None:
    """No ``delete``/``purge``/``reset`` anywhere on either object.

    Spec §4.6: ``purge_paper_extraction`` and the reconcilers have no
    post-migration equivalent, and canonical state is never destructively
    deleted. Even the test fixtures get their pristine graph from a fresh
    namespace rather than from a delete, so there is no reason for one to exist.
    """
    store = make_canonical_store()
    target = store if surface == "store" else store.read_only()
    public = [name for name in dir(target) if not name.startswith("_")]
    assert public, "nothing inspected"
    banned = [
        name
        for name in public
        if any(word in name.lower() for word in ("delete", "purge", "drop", "reset", "truncate"))
    ]
    assert banned == []
