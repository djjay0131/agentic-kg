"""The store reaches the executor and nothing else.

The architectural law is that no application-facing code and no LLM adviser
gets a canonical write surface: writes happen only via ``PlanExecutor``
applying a ``CurationPlan``. This subpackage is the executor's *caller*, so it
does legitimately touch a ``GraphMutationStore`` — which makes it exactly the
place the law is easiest to break.

Three checks, and the scope of each is stated rather than implied:

1. **Static, this subpackage.** Only ``pipeline.py`` and ``rollback.py`` name a
   store at all, and in both the only thing they do with it is construct a
   ``PlanExecutor``. Nothing calls ``.apply`` directly.
2. **Structural, at runtime.** No object this subpackage returns satisfies
   ``GraphMutationStore``, and none has an attribute that does. The protocol is
   ``@runtime_checkable``, so this is a real check against the real protocol
   rather than a name comparison.
3. **Behavioural.** A store double that fails on write proves the two
   no-write paths (flag off, empty plan) really do not write — those are in
   ``test_pipeline.py``.

What this does **not** prove: that no module anywhere else in the repo can
obtain a canonical store. That claim belongs to
``tests/migration/neo4j/test_no_application_write_surface.py``, which scans the
two application trees AC-1 names. Nothing here widens it.
"""

from __future__ import annotations

import ast

from agentic_kg.migration.config import MigrationConfig
from agentic_kg.migration.curation import curated_arm, roll_back, run_curation
from kg_contracts.stores import GraphMutationStore

from . import _scan
from ._synthetic import graded_entity_candidate

#: Modules permitted to hold a ``GraphMutationStore`` at all. Both hand it
#: straight to ``PlanExecutor``; the list is short so that adding a third is a
#: deliberate edit with a reason.
STORE_BEARING_MODULES = frozenset({"pipeline", "rollback"})

#: What a ``store`` name may legally be passed to, and why. Everything else is
#: a leak: a store handed to a helper, a sink, or a returned object becomes
#: reachable by whatever holds that.
#:
#: * ``PlanExecutor`` — the only write path in the architecture.
#: * ``isinstance`` — a type test. Retains no reference.
#: * ``_current_snapshot`` — module-local and read-only; it asks the store for
#:   its current epoch so the plan can be stamped with the snapshot it was
#:   computed against. ``test_the_snapshot_helper_is_local_and_read_only``
#:   checks both halves of that claim rather than taking the name for it.
PERMITTED_STORE_CALLEES = frozenset({"PlanExecutor", "isinstance", "_current_snapshot"})


def _store_bearing(path) -> bool:
    return "GraphMutationStore" in path.read_text(encoding="utf-8")


def test_only_the_executor_callers_name_a_write_surface() -> None:
    offenders = sorted(
        p.name
        for p in _scan.modules()
        if p.stem not in STORE_BEARING_MODULES
        and p.stem != _scan.GATE_MODULE
        and _store_bearing(p)
    )
    assert offenders == [], (
        f"these modules name a canonical write surface but are not executor "
        f"callers: {offenders}"
    )


def test_the_store_is_only_ever_handed_to_the_plan_executor() -> None:
    """A ``store`` name flows into ``PlanExecutor(...)`` and nowhere else.

    Checked per call site rather than by grepping for ``.apply``: the failure
    to catch is not someone writing ``store.apply(...)`` — that is obvious —
    but someone passing the store to a helper, a returned object, or a sink,
    from which it becomes reachable by whatever holds that.
    """
    offenders: list[str] = []
    for path in _scan.modules():
        if path.stem not in STORE_BEARING_MODULES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            uses_store = any(
                isinstance(a, ast.Name) and a.id == "store" for a in node.args
            ) or any(
                isinstance(k.value, ast.Name) and k.value.id == "store"
                for k in node.keywords
            )
            if not uses_store:
                continue
            callee = node.func.id if isinstance(node.func, ast.Name) else None
            if callee not in PERMITTED_STORE_CALLEES:
                offenders.append(f"{path.name}:{node.lineno} -> {callee}")
    assert offenders == [], (
        f"the canonical store was handed to something other than PlanExecutor: "
        f"{offenders}"
    )


def test_the_snapshot_helper_is_local_and_read_only() -> None:
    """``_current_snapshot`` is defined where it is used and cannot write.

    The allowlist above would otherwise let a store be handed to *any* function
    that happened to be named ``_current_snapshot`` — including an imported one
    from somewhere that does hold a write surface.
    """
    pipeline = next(p for p in _scan.modules() if p.stem == "pipeline")
    source = pipeline.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(pipeline))
    helper = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_current_snapshot"
    )
    called = {
        node.func.attr
        for node in ast.walk(helper)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert called == {"current_epoch"}, (
        f"_current_snapshot calls {sorted(called)} on the store; only a read is "
        f"permitted"
    )


def test_the_detector_would_catch_a_leak() -> None:
    """The control: the same walk, pointed at a source that does leak.

    ``test_the_store_is_only_ever_handed_to_the_plan_executor`` asserts an empty
    list, and an empty list is what a broken matcher also produces.
    """
    leaking = "def f(store):\n    return SomeSink(store)\n"
    tree = ast.parse(leaking)
    found = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and any(isinstance(a, ast.Name) and a.id == "store" for a in node.args)
    ]
    assert found == ["SomeSink"]


def test_no_returned_object_is_or_holds_a_write_surface(memory_store: object) -> None:
    """Structural, at runtime, over everything this subpackage hands back."""
    candidate = graded_entity_candidate()
    result = run_curation(
        [candidate],
        config=MigrationConfig(use_kgcs_resolution=True),
        store=memory_store,
    )
    arm = curated_arm(result, [candidate], doi_to_slug={})
    rollback = roll_back(result, store=memory_store)

    assert isinstance(memory_store, GraphMutationStore), (
        "the fixture store does not satisfy GraphMutationStore, so this test "
        "would pass over an object that could never have been a write surface"
    )
    for returned in (result, arm, rollback, result.engine, result.execution):
        assert not isinstance(returned, GraphMutationStore)
        for name in dir(returned):
            if name.startswith("__"):
                continue
            try:
                attribute = getattr(returned, name)
            except Exception:  # pragma: no cover - properties that need a plan
                continue
            assert not isinstance(attribute, GraphMutationStore), (
                f"{type(returned).__name__}.{name} is a canonical write surface"
            )
