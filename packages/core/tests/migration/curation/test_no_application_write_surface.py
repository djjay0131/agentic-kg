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
construct is unrecognised, and unrecognised is reported. The twenty-six
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

import ast

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
#:
#: The first two are safe by what they *are*: one is the architecture's write
#: path, the other a builtin. The third is safe only by what this subpackage's
#: own source happens to say, so it is the only one whose claim needs checking —
#: see :data:`LOCAL_READ_ONLY_CALLEES` and
#: ``test_the_snapshot_helper_is_local_and_read_only``, which checks both halves
#: of that sentence rather than taking the name for it.
PERMITTED_STORE_CALLEES = frozenset({"PlanExecutor", "isinstance", "_current_snapshot"})

#: The subset of :data:`PERMITTED_STORE_CALLEES` whose entry on that list rests
#: on a claim about local source rather than on what the callee is. Matching on
#: a *name* means the whitelist would otherwise admit any function that happened
#: to be spelled this way — including one imported from a module that does hold
#: a write surface.
LOCAL_READ_ONLY_CALLEES = frozenset({"_current_snapshot"})

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


def test_the_scan_finds_a_module_it_has_never_heard_of(tmp_path) -> None:
    """Derivation checked by discovery, not by comparing to today's answer.

    ``test_the_scan_derives_the_modules_it_reads`` asserts the set equals
    ``{pipeline, rollback}`` — which a transcribed list satisfies just as well,
    as a mutation proved. This points the finder at a module that does not
    exist in the subpackage, with an **unannotated** ``store`` parameter: the
    exact structural hole review found, where a new module handling a store was
    scanned by nothing.
    """
    newcomer = tmp_path / "projection.py"
    newcomer.write_text("def publish(store, plan):\n    return store.apply(plan, ())\n")
    innocent = tmp_path / "helpers.py"
    innocent.write_text("def add(a, b):\n    return a + b\n")

    found = {p.name for p in _scan.store_bearing_modules([newcomer, innocent])}
    assert found == {"projection.py"}
    assert _offenders_in(newcomer.read_text(), newcomer.name), (
        "the newly discovered module's store.apply was not reported"
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
    # Not evasions anyone demonstrated — found by mutating the comparison
    # whitelist and discovering no parameter could kill it. That whitelist is a
    # *conjunction* (``is``/``is not`` **and** against ``None``), so one probe
    # violating both conjuncts at once cannot tell them apart: either conjunct
    # surviving alone still satisfies it, and review measured both mutants
    # passing 113 green. One parameter per conjunct, so each dies separately.
    #
    #   compare-identity-non-none: identity, but not against None
    #   compare-equality-none    : against None, but not identity
    "compare-identity-non-none": "def f(store, other):\n    return store is other\n",
    "compare-equality-none": "def f(store):\n    return store == None\n",
}


@pytest.mark.parametrize("name", sorted(EVASIONS))
def test_the_detector_catches_every_known_evasion(name: str) -> None:
    """Twenty-six shapes, from five rounds of review. Each must be reported.

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


def _functions_named(tree: ast.AST, name: str) -> list[ast.AST]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]


def _names_bound_by_import(tree: ast.AST) -> set[str]:
    """Every local name an ``import`` statement binds in this module."""
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".")[0])
    return bound


def test_the_snapshot_helper_is_local_and_read_only() -> None:
    """A name-matched callee is defined where it is used, and cannot write.

    This is the guard that keeps ``_current_snapshot``'s place on
    ``PERMITTED_STORE_CALLEES`` honest, and it is restored here after a rewrite
    dropped it. With it gone, review demonstrated the consequence live: a
    sibling module in this subpackage defining ``_current_snapshot`` with a
    parameter named neither ``store`` nor annotated (so the scan derives no root
    from it, and the module is not store-bearing), imported by ``pipeline.py``
    in place of the local definition, appended the canonical store to a
    module-global list — and the whole suite stayed green at ``113 passed``.

    Two halves, because the comment on the whitelist makes two claims:

    * **local** — the name is defined by a ``def`` in the module that hands it a
      store, and is not bound by any import there. An imported
      ``_current_snapshot`` satisfies the whitelist by spelling alone.
    * **read-only** — the only attribute it calls is one of
      ``PERMITTED_STORE_ATTRS``. ``LEAKS.append(store)`` is an attribute call
      and fails this half even if the function is local.

    Scope is derived, not transcribed: every store-bearing module that actually
    hands a store to one of these names is checked, so a second caller appearing
    tomorrow is covered on the day it appears.
    """
    checked: list[str] = []
    for path in _scan.store_bearing_modules(_scan.modules()):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for name in sorted(LOCAL_READ_ONLY_CALLEES):
            if not _scan.calls_named(source, name):
                continue
            checked.append(f"{path.name}:{name}")

            assert name not in _names_bound_by_import(tree), (
                f"{path.name} imports {name} rather than defining it, so its "
                f"place on PERMITTED_STORE_CALLEES rests on the spelling of a "
                f"name and nothing else"
            )
            definitions = _functions_named(tree, name)
            assert definitions, (
                f"{path.name} hands a store to {name} but does not define it; "
                f"the whitelist admits it by name, so the body it admits is "
                f"not the body this test can see"
            )

            for helper in definitions:
                called = {
                    node.func.attr
                    for node in ast.walk(helper)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                }
                assert called <= PERMITTED_STORE_ATTRS, (
                    f"{path.name}:{name} calls "
                    f"{sorted(called - PERMITTED_STORE_ATTRS)}; only a read from "
                    f"{sorted(PERMITTED_STORE_ATTRS)} is permitted"
                )

    assert checked, (
        "no store-bearing module calls any of LOCAL_READ_ONLY_CALLEES, so this "
        "test asserted nothing. Either the helper was renamed and the whitelist "
        "entry is now dead, or the scan stopped finding the module."
    )


def test_a_global_escape_still_reports_what_it_leaks_into() -> None:
    """Escaping via ``global`` is reported, and so is every use after it.

    A ``global``/``nonlocal`` exclusion in ``store_aliases`` has now been
    deleted twice. It is unkillable by mutation — ``store_offenders`` consults
    ``rebound_names`` itself, so the escaping binding is reported either way —
    which is exactly why it came back looking harmless. It is not harmless: it
    stops the alias propagating, so the leak *downstream* of the escape vanishes
    from the report. This pins the reporting, which is the only thing the line
    ever changed.
    """
    escape_then_leak = (
        "REG = None\n"
        "def f(store):\n"
        "    global REG\n"
        "    REG = store\n"
        "    return Sink(REG)\n"
    )
    reasons = " | ".join(_offenders_in(escape_then_leak))
    assert "bound into global binding" in reasons, reasons
    assert "passed to Sink" in reasons, (
        f"the escape was reported but the leak it enables was not: {reasons}. "
        f"An alias published into an enclosing scope is still an alias; "
        f"dropping it from the alias set only shortens the report."
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
