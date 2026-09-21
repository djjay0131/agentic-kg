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

**How much the static check is worth, after review.** Its first version matched
only a literal ``store`` appearing as a call *argument*, and an independent
reviewer walked past it twice with the whole suite green: ``_s = store``, then
leak ``_s``; and receiver-form ``store.apply(batch, ())``, which nothing in the
file looked at — the docstring had dismissed a direct ``.apply`` as "obvious",
and obvious is not caught. It now follows single-assignment aliases to a fixed
point, flags receiver-form attribute access against a closed allowlist, and
flags an alias placed in a container or returned — which needs no call at all,
and was the exact shape of the reviewer's second evasion.
``test_the_detector_catches_the_evasions_that_walked_past_it`` drives both
evasions plus a direct write through the detector and requires each to be
flagged, because a rule asserting an empty offender list is satisfied by a
matcher that matches nothing.

It remains **syntactic and one-hop**. An alias built through a container, a
closure, ``getattr`` or ``globals()`` is not statically detectable and never will
be. This guards against accident and drift; it is not a security boundary.

What this does **not** prove: that no module anywhere else in the repo can
obtain a canonical store. That claim belongs to
``tests/migration/neo4j/test_no_application_write_surface.py``, which scans the
two application trees AC-1 names. Nothing here widens it.
"""

from __future__ import annotations

import ast

import pytest
from agentic_kg.migration.config import MigrationConfig
from agentic_kg.migration.curation import curated_arm, roll_back, run_curation
from kg_contracts.stores import GraphMutationStore

from . import _scan
from ._synthetic import graded_entity_candidate

#: Modules permitted to hold a ``GraphMutationStore`` at all. Both hand it
#: straight to ``PlanExecutor``; the list is short so that adding a third is a
#: deliberate edit with a reason.
STORE_BEARING_MODULES = frozenset({"pipeline", "rollback"})

#: What a store alias may legally be passed to, and why. Everything else is a
#: leak: a store handed to a helper, a sink, or a returned object becomes
#: reachable by whatever holds that.
#:
#: * ``PlanExecutor`` — the only write path in the architecture.
#: * ``isinstance`` — a type test. Retains no reference.
#: * ``_current_snapshot`` — module-local and read-only; it asks the store for
#:   its current epoch so the plan can be stamped with the snapshot it was
#:   computed against. ``test_the_snapshot_helper_is_local_and_read_only``
#:   checks both halves of that claim rather than taking the name for it.
PERMITTED_STORE_CALLEES = frozenset({"PlanExecutor", "isinstance", "_current_snapshot"})

#: What a store alias may legally be *dereferenced* for. Exactly one read.
#: ``apply`` is deliberately absent: the executor calls it, this subpackage
#: never does. Widening this set is how the architectural law gets lost, so it
#: is a closed list and adding to it is a deliberate edit.
PERMITTED_STORE_ATTRS = frozenset({"current_epoch"})


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


def _offenders_in(source: str, filename: str = "<test>") -> list[str]:
    """Every illegal use of a store alias in ``source``.

    One function, used by the rule and by its controls alike, so the thing
    proved able to fire is the thing that runs over the subpackage.
    """
    tree = ast.parse(source, filename=filename)
    aliases = _scan.store_aliases(tree)
    found: list[str] = []
    for lineno, kind, name in _scan.store_reachings(tree, aliases):
        if kind == "arg" and name not in PERMITTED_STORE_CALLEES:
            found.append(f"{filename}:{lineno} passed to {name}")
        elif kind == "attr" and name not in PERMITTED_STORE_ATTRS:
            found.append(f"{filename}:{lineno} dereferenced .{name}")
        elif kind == "escape":
            found.append(f"{filename}:{lineno} escaped into a {name}")
    return sorted(found)


def test_the_store_is_only_ever_handed_to_the_plan_executor() -> None:
    """A store alias flows into ``PlanExecutor(...)`` and nowhere else.

    Covers three shapes, the last two added after an independent reviewer walked
    past the first with the suite green: the store passed as an argument to
    something that is not the executor; an *alias* of the store leaked the same
    way; and the store used as a receiver for anything but the one permitted
    read.
    """
    offenders: list[str] = []
    for path in _scan.modules():
        if path.stem not in STORE_BEARING_MODULES:
            continue
        offenders.extend(_offenders_in(path.read_text(encoding="utf-8"), path.name))
    assert offenders == [], (
        f"the canonical store (or an alias of it) escaped the executor: {offenders}"
    )


@pytest.mark.parametrize(
    ("source", "expected_fragment"),
    [
        ("def f(store):\n    return SomeSink(store)\n", "passed to SomeSink"),
        ("def f(store):\n    _s = store\n    return SomeSink(_s)\n", "passed to SomeSink"),
        ("def f(store):\n    a = store\n    b = a\n    return Sink(b)\n", "passed to Sink"),
        ("def f(store, batch):\n    return store.apply(batch, ())\n", "dereferenced .apply"),
        ("def f(store):\n    return store.read_only()\n", "dereferenced .read_only"),
        ("def f(store):\n    _s = store\n    _sink = (_s,)\n", "escaped into a tuple"),
        ("def f(store):\n    _sink = {'s': store}\n", "escaped into a dict"),
        ("def f(store):\n    return store\n", "escaped into a return"),
    ],
    ids=[
        "direct-arg",
        "one-hop-alias",
        "two-hop-alias",
        "receiver-apply",
        "receiver-other",
        "alias-into-container",
        "into-dict",
        "returned",
    ],
)
def test_the_detector_catches_the_evasions_that_walked_past_it(
    source: str, expected_fragment: str
) -> None:
    """The detector discriminates — pointed at each evasion, it fires.

    The middle three are the reviewer's, reproduced: ``_s = store`` then leaking
    ``_s``, and receiver-form ``store.apply(...)``, both of which passed the
    entire 55-test suite before this. Without these parameters the rule above
    asserts an empty list, and an empty list is exactly what a matcher that
    matches nothing also produces.
    """
    offenders = _offenders_in(source)
    assert offenders, f"the detector missed: {source!r}"
    assert any(expected_fragment in o for o in offenders), offenders


def test_the_detector_passes_the_legal_shapes() -> None:
    """The control for the control: the permitted uses are not flagged.

    A detector that flagged everything would satisfy every test above and would
    make the rule unpassable for correct code — which is how an over-broad check
    gets loosened back into uselessness.
    """
    legal = (
        "def f(store):\n"
        "    if isinstance(store, GraphReader):\n"
        "        return str(store.current_epoch())\n"
        "    return PlanExecutor(store)\n"
    )
    assert _offenders_in(legal) == []


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
