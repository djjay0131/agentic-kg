"""The read surface is structurally not a write surface, and AC-1's two trees are clean.

Scope, stated precisely, because the obvious summary would overclaim. This does
**not** prove "no application code anywhere can obtain a canonical write
surface". It proves two narrower things:

1. **Structural, and unconditional.** The object handed to non-executor code
   (:class:`Neo4jCanonicalGraphReader`) does not satisfy `GraphMutationStore`,
   and no attribute reachable from it does either. `GraphMutationStore` is
   ``@runtime_checkable``, so ``isinstance`` is a real check against the real
   protocol, not a name comparison. This holds for every caller.
2. **Static, and scoped to exactly the two trees AC-1 names** —
   ``packages/api/`` and ``packages/core/src/agentic_kg/agents/``. Mapping spec
   §2 rule 1 / AC-1: "No module under ``packages/api/`` or
   ``packages/core/src/agentic_kg/agents/`` imports ``GraphMutationStore``, and
   no such module holds an object satisfying it. Asserted by a test, not by
   review." Nothing here scans ``packages/core/src/agentic_kg/`` at large, the
   CLI, the scripts, or the notebooks. A module outside those two trees that
   constructs the store directly would not be caught, and widening the scan is
   a change to AC-1, not to this test.

Plus **AC-3**: the canonical reader is not a `LedgerReader`, and vice versa
(KGIS ADR-0011 — canonical reads and ledger reads are never one access path).

The static scan asserts it actually looked at files (§9.0 obligation 1) and a
companion test points the same detector at a deliberately offending module
(obligation 5). A scan over an empty file set is the canonical vacuous check,
and the whole AC would otherwise pass on a typo in a path.
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

from .scenarios import assert_valid_time_window_honoured

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


def test_temporal_graph_reader_isinstance_is_vacuous() -> None:
    """Why no test here asserts ``isinstance(x, TemporalGraphReader)``.

    `TemporalGraphReader` extends `GraphReader` and declares **no additional
    members**, so at runtime the two protocols have identical
    ``__protocol_attrs__`` and ``isinstance(x, TemporalGraphReader)`` is true
    for anything that is a `GraphReader` at all — including an adapter that
    raises ``UnsupportedCapabilityError`` on every temporal option. Upstream
    says as much: the class docstring calls it a *marker*.

    An assertion that cannot fail is the exact defect this suite exists to hunt,
    so those assertions were deleted rather than kept for the look of rigour.
    What replaces them is behavioural —
    :func:`test_read_only_surface_actually_honours_temporal_options` — and a
    capability check on the store, both of which can fail.

    This test pins the *reason*: if upstream ever gives `TemporalGraphReader` a
    distinguishing member, the ``isinstance`` check stops being vacuous and
    should come back, and this failure is the prompt to do it.

    Takes no fixture — it inspects the pinned protocols and needs no database.
    """
    extra = set(TemporalGraphReader.__protocol_attrs__) - set(GraphReader.__protocol_attrs__)
    assert extra == set(), (
        f"TemporalGraphReader now declares {sorted(extra)} beyond GraphReader, "
        f"so isinstance() against it is no longer vacuous - restore the "
        f"structural assertions this test exists to justify removing"
    )


def test_read_only_surface_is_not_a_mutation_store(make_canonical_store) -> None:
    store = make_canonical_store()
    reader = store.read_only()

    assert isinstance(reader, Neo4jCanonicalGraphReader)
    assert isinstance(reader, GraphReader)
    # No `isinstance(reader, TemporalGraphReader)` here: it cannot fail. See
    # test_temporal_graph_reader_isinstance_is_vacuous.
    assert not isinstance(reader, GraphMutationStore)
    assert not isinstance(reader, CandidateSink)
    assert not hasattr(reader, "apply")


def test_read_only_surface_actually_honours_temporal_options(make_canonical_store) -> None:
    """The behavioural replacement for the vacuous protocol ``isinstance``.

    Attaches one assertion with a bounded valid period and probes the façade at
    an instant inside the window and at one beyond **each** edge. A reader that
    ignored ``valid_at`` returns it every time; one that could not honour it at
    all raises ``UnsupportedCapabilityError``; one that dropped a single edge
    fails exactly that edge. All three are red — which is more than the
    protocol ``isinstance`` could ever be, since `TemporalGraphReader` declares
    no members to check.

    The assertions live in ``scenarios.assert_valid_time_window_honoured`` so
    ``test_conformance_is_falsifiable.py`` can falsify *them* rather than a
    copy of them.
    """
    assert_valid_time_window_honoured(make_canonical_store)


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
    """The counterpart: the executor's object really does satisfy the union.

    Without this the previous tests could pass against a reader that is simply
    broken. `kg_contracts.testing.contract._TestableGraphStore` is the union the
    shared suite casts to (`contract.py:49`) —
    ``GraphMutationStore + TemporalGraphReader + CapabilityDeclaring``.

    Two of the three legs are asserted structurally. The third,
    `TemporalGraphReader`, is asserted **by capability declaration** instead:
    the protocol adds no members over `GraphReader`, so an ``isinstance``
    against it cannot fail (see
    :func:`test_temporal_graph_reader_isinstance_is_vacuous`), whereas
    ``supports_temporal_queries`` is a real claim this store could get wrong —
    and if it declared ``False``, the shared suite's
    ``test_capability_conformance_for_temporal_options`` would then demand an
    ``UnsupportedCapabilityError`` this adapter does not raise, so the two
    checks are wired to each other rather than each asserting themselves.
    """
    from kg_contracts.stores import CapabilityDeclaring

    store = make_canonical_store()
    assert isinstance(store, GraphMutationStore)
    assert isinstance(store, GraphReader)
    assert isinstance(store, CapabilityDeclaring)
    assert store.capabilities().supports_temporal_queries is True


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
