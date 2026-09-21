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

**How much the static check is worth, and what it does not claim.** An earlier
version of this docstring said "the class is closed". That was false, and it was
false in the most instructive way: it was written immediately after closing the
two instances a reviewer had demonstrated, and a later reviewer then walked
through the same check **nine more ways** — list, generator and dict
comprehensions, lambda bodies, default arguments, ``yield``, aliases created
through ``IfExp`` and ``BoolOp``, and class-body bindings. Four of them, injected
into the real ``pipeline.py``, passed the entire suite green; one produced a live
module-global write surface.

So the claim is corrected and the mechanism is changed. The scan is now
**polarity-inverted**: it enumerates the positions a store alias may occupy and
reports every other occurrence, instead of enumerating the shapes it may not.
A shape-enumerating check protects against the shapes someone thought of, and
cannot be finished by thinking harder — the language keeps offering new
positions. A position whitelist fails the other way round: an unanticipated
construct is unrecognised, and unrecognised is reported. The twenty-five
parameters of ``test_the_detector_catches_every_known_evasion`` are regression
evidence for that change, not the mechanism of it.

The honest statement of strength is therefore: **no use of a store alias in this
subpackage occupies a position outside a whitelist of four**, and the module set
that rule runs over is derived from the code rather than transcribed. It remains
**syntactic and one-hop**: an alias built through a closure, ``getattr`` or
``globals()`` is not detectable by any AST walk, and this is not a security
boundary. What changed is the direction it fails in.

What this does **not** prove: that no module anywhere else in the repo can
obtain a canonical store. That claim belongs to
``tests/migration/neo4j/test_no_application_write_surface.py``, which scans the
two application trees AC-1 names. Nothing here widens it.
"""

from __future__ import annotations

import pytest
from agentic_kg.migration.config import MigrationConfig
from agentic_kg.migration.curation import curated_arm, roll_back, run_curation
from kg_contracts.stores import GraphMutationStore

from . import _scan
from ._synthetic import graded_entity_candidate

#: What a store alias may legally be passed to, and why.
#:
#: * ``PlanExecutor`` — the only write path in the architecture.
#: * ``isinstance`` — a type test. Retains no reference.
#: * ``_current_snapshot`` — module-local and read-only.
PERMITTED_STORE_CALLEES = frozenset({"PlanExecutor", "isinstance", "_current_snapshot"})

#: What a store alias may legally be *dereferenced* for. Exactly one read.
#: ``apply`` is deliberately absent: the executor calls it, this subpackage
#: never does.
PERMITTED_STORE_ATTRS = frozenset({"current_epoch"})


def _offenders_in(source: str, filename: str = "<test>") -> list[str]:
    """Every illegal use of a store alias in ``source``.

    One function, used by the rule and by its controls alike, so the thing
    proved able to fire is the thing that runs over the subpackage.
    """
    return _scan.store_offenders(
        source,
        filename,
        permitted_callees=PERMITTED_STORE_CALLEES,
        permitted_attributes=PERMITTED_STORE_ATTRS,
    )


def test_the_scan_derives_the_modules_it_reads() -> None:
    """The module set is derived from parameters, not transcribed.

    A transcribed list was the fifth hole an independent reviewer found: a new
    module taking an unannotated ``store`` parameter sat outside it, so nothing
    looked at the ``store.apply(...)`` inside. Deriving it means a module that
    starts handling a store starts being scanned in the same commit.
    """
    bearing = {p.stem for p in _scan.store_bearing_modules(_scan.modules())}
    assert bearing == {"pipeline", "rollback"}, (
        f"the set of modules a store reaches changed: {sorted(bearing)}. That is "
        f"not necessarily wrong, but it is never incidental."
    )


def test_the_store_is_only_ever_handed_to_the_plan_executor() -> None:
    """A store alias appears only in a permitted position, anywhere it reaches.

    Polarity-inverted: the rule enumerates where a store *may* appear and
    reports everything else, so a construct nobody anticipated is reported
    rather than missed. See ``_scan`` for why three shape-enumerating versions
    were abandoned.
    """
    offenders: list[str] = []
    for path in _scan.store_bearing_modules(_scan.modules()):
        offenders.extend(_offenders_in(path.read_text(encoding="utf-8"), path.name))
    assert offenders == [], (
        f"the canonical store (or an alias of it) reached a position that is not "
        f"on the whitelist: {offenders}"
    )


EVASIONS = {
    # The two an independent reviewer used against version three.
    "subscript-assign": "REG = {}\ndef f(store):\n    REG['canonical'] = store\n",
    "global-binding": "REG = None\ndef f(store):\n    global REG\n    REG = store\n",
    # The nine it found against version four, each of which passed that suite.
    "list-comprehension": "def f(store):\n    return [s for s in (store,)]\n",
    "gen-comprehension": "def f(store):\n    return (s for s in [store])\n",
    "dict-comprehension": "def f(store):\n    return {'s': v for v in [store]}\n",
    "lambda-body": "def f(store):\n    return lambda: store\n",
    "default-argument": "def f(store):\n    def g(s=store):\n        return s\n    return g\n",
    "yield": "def f(store):\n    yield store\n",
    "ifexp-alias": "def f(store, flag):\n    a = store if flag else None\n    return Sink(a)\n",
    "boolop-alias": "def f(store):\n    a = store or None\n    return Sink(a)\n",
    "class-body": (
        "def f(store):\n    class Holder:\n        canonical = store\n    return Holder\n"
    ),
    # Earlier rounds, kept so no regression re-opens them.
    "direct-arg": "def f(store):\n    return SomeSink(store)\n",
    "one-hop-alias": "def f(store):\n    _s = store\n    return SomeSink(_s)\n",
    "two-hop-alias": "def f(store):\n    a = store\n    b = a\n    return Sink(b)\n",
    "receiver-apply": "def f(store, batch):\n    return store.apply(batch, ())\n",
    "receiver-other": "def f(store):\n    return store.read_only()\n",
    "alias-into-container": "def f(store):\n    _s = store\n    _sink = (_s,)\n",
    "into-dict": "def f(store):\n    _sink = {'s': store}\n",
    "returned": "def f(store):\n    return store\n",
    "walrus-inline": "def f(store):\n    return Sink(alias := store)\n",
    "walrus-then-leak": "def f(store):\n    (alias := store)\n    return Sink(alias)\n",
    "attribute-assign": "def f(store, holder):\n    holder.canonical = store\n",
    "star-arg": "def f(store):\n    return Sink(*[store])\n",
    "annotated-param": (
        "def f(db: GraphMutationStore, batch):\n    return db.apply(batch, ())\n"
    ),
    "unannotated-new-module": "def helper(store, plan):\n    return store.apply(plan, ())\n",
    "globals-lambda": "def f(store):\n    globals()['_G'] = lambda: store\n",
}


@pytest.mark.parametrize("name", sorted(EVASIONS))
def test_the_detector_catches_every_known_evasion(name: str) -> None:
    """Twenty-five shapes, from four rounds of review. Each must be reported.

    Eleven of these defeated an earlier version of this scan. They are kept as
    parameters rather than fixed one at a time, because the lesson of those
    rounds is that the list is never finished — which is why the rule is now a
    position whitelist and these are regression evidence rather than the
    mechanism.
    """
    offenders = _offenders_in(EVASIONS[name])
    assert offenders, f"the detector missed {name}: {EVASIONS[name]!r}"


def test_the_detector_passes_the_legal_shapes() -> None:
    """The control for the control: permitted uses are not flagged.

    A whitelist that flagged everything would satisfy every test above and make
    the rule unpassable for correct code — which is how an over-broad check gets
    loosened back into uselessness. This is the real shape of ``pipeline.py``'s
    own usage.
    """
    legal = (
        "def f(store, plan):\n"
        "    snapshot = _current_snapshot(store)\n"
        "    executor = PlanExecutor(store, clock=None)\n"
        "    return executor, snapshot\n"
        "def _current_snapshot(store):\n"
        "    if isinstance(store, GraphReader):\n"
        "        return str(store.current_epoch())\n"
        "    return '0'\n"
    )
    assert _offenders_in(legal) == []


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
