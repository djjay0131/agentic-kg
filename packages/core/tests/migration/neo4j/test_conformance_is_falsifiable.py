"""Mutation tests: proof that the conformance suite can actually go red.

§9.0 obligation 5 — "a criterion must be able to fail for the reason it names.
State the defect the check exists to catch, and confirm that an implementation
exhibiting that defect turns it red — a mutation is the cheap way."

A green conformance run says nothing on its own. A suite wired to a store that
silently no-ops, or fixtures that make every assertion vacuous, passes exactly
as convincingly as a correct one. So each defect the shared suite is supposed to
catch is injected here, one at a time, and the specific test that should notice
is asserted to fail. The control case — the unmutated adapter passing all ten
— runs in the same module against the same helper, so "all ten" is measured
the same way in both directions.

The mutations are applied with ``monkeypatch`` against the real implementation
rather than by writing a separate broken class: a hand-written stub can drift
away from the thing under test, while a patched attribute is, by construction,
the real code path with one piece changed.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from agentic_kg.migration.neo4j import store as store_module
from agentic_kg.migration.neo4j.store import Neo4jCanonicalGraphStore
from kg_contracts.assertions import CurationStatus
from kg_contracts.curation import (
    CurationOperation,
    CurationOperationType,
)
from kg_contracts.stores import GraphMutationBatch, GraphReadOptions
from kg_contracts.testing.contract import GraphMutationStoreContract
from kg_contracts.testing.factories import make_assertion, make_entity

# Imported as a *module*, never `from ... import TestNeo4j...`: binding a
# `Test`-prefixed class into this namespace would make pytest collect and run
# the shared contract tests a second time here.
from . import test_contract_conformance as conformance_module
from .scenarios import assert_entity_version_guard, assert_valid_time_window_honoured

pytestmark = pytest.mark.integration

#: Every test method the shared suite defines. Derived, not transcribed, so a
#: suite that grows a test upstream is covered here without an edit.
CONTRACT_TESTS: tuple[str, ...] = tuple(
    sorted(name for name in dir(GraphMutationStoreContract) if name.startswith("test_"))
)


def run_contract(factory: Callable[[], object]) -> dict[str, bool]:
    """Run the whole shared suite against ``factory``; report pass/fail per test.

    Exceptions are swallowed deliberately — a mutant is *expected* to raise, and
    the point is to record which tests noticed.
    """
    suite = GraphMutationStoreContract()
    suite.make_store = factory  # type: ignore[method-assign]
    results: dict[str, bool] = {}
    for name in CONTRACT_TESTS:
        try:
            getattr(suite, name)()
        except Exception:  # noqa: BLE001 - failure is the measurement
            results[name] = False
        else:
            results[name] = True
    return results


def test_the_suite_has_the_tests_this_module_assumes() -> None:
    """Obligation 1: the set quantified over must be non-empty — and named.

    If upstream renames a test, the parametrisation below would silently assert
    against a test that no longer exists and every mutant would look "caught" by
    an empty expectation. Pinning the names makes that a failure here instead.
    """
    assert CONTRACT_TESTS == (
        "test_capability_conformance_for_temporal_options",
        "test_create_and_attach_commits_and_returns_new_epoch",
        "test_entity_readable_after_commit_not_before",
        "test_failed_entity_version_precondition_blocks_commit_atomically",
        "test_include_superseded_and_include_revoked_are_independent",
        "test_revoke_identity_hides_entity_and_preserves_creation_epoch",
        "test_revoked_assertions_hidden_by_default_visible_with_flag",
        "test_snapshot_read_at_old_epoch_hides_later_records",
        "test_superseded_assertions_hidden_by_default_visible_with_flag",
        "test_transaction_at_filters_by_half_open_recorded_superseded_window",
    )


def test_unmutated_adapter_passes_every_contract_test(make_canonical_store) -> None:
    """The control case, measured by the same harness as the mutants."""
    results = run_contract(make_canonical_store)
    assert len(results) == len(CONTRACT_TESTS)
    failed = [name for name, ok in results.items() if not ok]
    assert failed == []


# --- the mutants --------------------------------------------------------------


def _mutate_epoch_never_advances(monkeypatch: pytest.MonkeyPatch) -> None:
    """Commit without minting a new curation epoch."""
    monkeypatch.setattr(
        Neo4jCanonicalGraphStore,
        "_advance_epoch",
        lambda self, tx: store_module._read_epoch(tx, self._namespace),
    )


def _mutate_superseded_always_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve SUPERSEDED records on a default read."""

    def always(history, options):  # noqa: ANN001, ANN202
        return store_module._effective(history, options.curation_epoch)

    monkeypatch.setattr(store_module, "_reported_status", always)


def _mutate_revoked_always_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve REVOKED records on a default read - this adapter's pre-0.3.0 rule.

    Not a hypothetical defect: it is literally what ``_status_visible`` did
    before the re-pin, when ``GraphReadOptions`` had no ``include_revoked`` and
    upstream pinned "revoked is visible by default". Injecting the *old*
    behaviour and asserting the *new* test reddens is the direct evidence that
    the new conformance test is testing this adapter for something it was not
    tested for before.
    """

    def revoked_visible(history, options):  # noqa: ANN001, ANN202
        status, at = store_module._effective(history, options.curation_epoch)
        if status is CurationStatus.SUPERSEDED and not options.include_superseded:
            return None
        return status, at

    monkeypatch.setattr(store_module, "_reported_status", revoked_visible)


def _mutate_visibility_flags_collapsed(monkeypatch: pytest.MonkeyPatch) -> None:
    """One "show me everything" switch instead of two independent ones.

    The mutant the independence cross-term exists for. It passes *both*
    single-flag tests - each of those sets up only one non-ACTIVE status - and
    fails only when both statuses are present and one flag is asked for alone.
    """

    def collapsed(history, options):  # noqa: ANN001, ANN202
        show_all = options.include_superseded or options.include_revoked
        latest, _ = store_module._effective(history, None)
        status, at = store_module._effective(history, options.curation_epoch)
        if latest is CurationStatus.REVOKED:
            return (CurationStatus.REVOKED, at) if show_all else None
        if status is CurationStatus.SUPERSEDED and not show_all:
            return None
        return status, at

    monkeypatch.setattr(store_module, "_reported_status", collapsed)


def _mutate_revoke_restamps_creation_epoch(monkeypatch: pytest.MonkeyPatch) -> None:
    """REVOKE_IDENTITY advances the identity's ``curation_epoch`` to the revoke.

    The defect ADR-0025 argues hardest against: a rollback that erases the
    record of what it rolled back. The identity is still retained and still
    reachable with ``include_revoked=True`` - only the epoch stamp moves - so
    nothing but the epoch assertions can notice.
    """
    import json as _json

    from agentic_kg.migration.neo4j.schema import LABEL_IDENTITY, uid

    real = Neo4jCanonicalGraphStore._apply_revoke_identity

    def restamp(self, tx, payload, epoch):  # noqa: ANN001, ANN202
        touched = real(self, tx, payload, epoch)
        for identity_id in touched:
            key = uid(self._namespace, identity_id)
            row = tx.run(
                f"MATCH (i:{LABEL_IDENTITY} {{uid: $uid}}) RETURN i.payload AS p", uid=key
            ).single()
            doc = _json.loads(row["p"])
            doc["curation_epoch"] = epoch
            tx.run(
                f"MATCH (i:{LABEL_IDENTITY} {{uid: $uid}}) "
                f"SET i.payload = $p, i.curation_epoch = $e",
                uid=key,
                p=_json.dumps(doc),
                e=epoch,
            ).consume()
        return touched

    monkeypatch.setattr(Neo4jCanonicalGraphStore, "_apply_revoke_identity", restamp)


def _mutate_snapshot_reads_ignore_epoch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ignore ``curation_epoch``: always read the latest state.

    This mutation is what forced the adapter to keep exactly *one* epoch gate.
    The first version of it patched only the Cypher prefilter and the contract
    test stayed green, because an identical Python-side check was still running
    — two implementations of one rule, each masking the other's failure. The
    prefilter was removed rather than the mutation strengthened: a rule enforced
    twice cannot be shown to be enforced at all.
    """
    monkeypatch.setattr(store_module, "_epoch_visible", lambda record_epoch, options: True)


def _mutate_preconditions_always_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    """Commit a batch whose optimistic-concurrency guard should have failed."""
    monkeypatch.setattr(
        Neo4jCanonicalGraphStore, "_precondition_holds", lambda self, tx, precondition: True
    )


def _mutate_transaction_time_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ignore the half-open ``[recorded_at, superseded_at)`` window."""
    monkeypatch.setattr(store_module, "_transaction_at_matches", lambda assertion, at: True)


def _mutate_valid_time_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ignore the domain valid-time interval."""
    monkeypatch.setattr(store_module, "_valid_at_matches", lambda assertion, at: True)


MUTANTS: tuple[tuple[str, Callable[[pytest.MonkeyPatch], None], str], ...] = (
    (
        "epoch never advances",
        _mutate_epoch_never_advances,
        "test_create_and_attach_commits_and_returns_new_epoch",
    ),
    (
        "SUPERSEDED served by default",
        _mutate_superseded_always_visible,
        "test_superseded_assertions_hidden_by_default_visible_with_flag",
    ),
    (
        "REVOKED served by default",
        _mutate_revoked_always_visible,
        "test_revoked_assertions_hidden_by_default_visible_with_flag",
    ),
    (
        "visibility flags collapsed into one",
        _mutate_visibility_flags_collapsed,
        "test_include_superseded_and_include_revoked_are_independent",
    ),
    (
        "REVOKE_IDENTITY restamps curation_epoch",
        _mutate_revoke_restamps_creation_epoch,
        "test_revoke_identity_hides_entity_and_preserves_creation_epoch",
    ),
    (
        "snapshot reads ignore curation_epoch",
        _mutate_snapshot_reads_ignore_epoch,
        "test_snapshot_read_at_old_epoch_hides_later_records",
    ),
    (
        "preconditions never fail",
        _mutate_preconditions_always_hold,
        "test_failed_entity_version_precondition_blocks_commit_atomically",
    ),
    (
        "transaction-time window ignored",
        _mutate_transaction_time_ignored,
        "test_transaction_at_filters_by_half_open_recorded_superseded_window",
    ),
    (
        "valid-time interval ignored",
        _mutate_valid_time_ignored,
        "test_capability_conformance_for_temporal_options",
    ),
)


@pytest.mark.parametrize(
    "label,mutate,expected_red", MUTANTS, ids=[m[0].replace(" ", "-") for m in MUTANTS]
)
def test_each_defect_turns_the_named_contract_test_red(
    label: str,
    mutate: Callable[[pytest.MonkeyPatch], None],
    expected_red: str,
    make_canonical_store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mutate(monkeypatch)
    results = run_contract(make_canonical_store)
    assert results[expected_red] is False, (
        f"mutation {label!r} left {expected_red} green - that test cannot "
        f"detect the defect it exists to detect"
    )


# --- the defect the shared suite does *not* catch -----------------------------


def test_in_place_status_mutation_breaks_the_epoch_read(
    make_canonical_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one defect the upstream suite cannot see, and this adapter's answer.

    ``MemoryGraphStore.mark_superseded`` edits the assertion in place. Nothing
    in ``GraphMutationStoreContract`` notices — the suite never supersedes an
    *already-committed* assertion at a later epoch and then reads back at the
    earlier one. So the mutation below (replace the status history instead of
    appending to it, i.e. backdate the new status to the original epoch) leaves
    all ten contract tests green while destroying the property the
    evidence-evolution scenario depends on.

    This asserts both halves: the ten stay green, and the epoch read goes
    wrong. That is why ``test_old_assertion_is_still_active_at_its_own_epoch``
    exists as a separate criterion rather than being folded into "it conforms".
    """
    import json

    def _replace_status(self, tx, assertion_id, epoch, status, at):  # noqa: ANN001
        row = tx.run(
            "MATCH (a:Canon__Assertion {uid: $uid}) RETURN a.status_history AS h",
            uid=f"{self._namespace}\x1f{assertion_id}",
        ).single()
        history = json.loads(row["h"])
        original_epoch = history[0]["epoch"]
        tx.run(
            "MATCH (a:Canon__Assertion {uid: $uid}) SET a.status_history = $h",
            uid=f"{self._namespace}\x1f{assertion_id}",
            h=json.dumps(
                [
                    {
                        "epoch": original_epoch,
                        "status": status.value,
                        "superseded_at": None if at is None else at.isoformat(),
                    }
                ]
            ),
        ).consume()

    monkeypatch.setattr(Neo4jCanonicalGraphStore, "_append_status", _replace_status)

    # Half one: the shared suite still passes. It cannot see this.
    results = run_contract(make_canonical_store)
    assert [name for name, ok in results.items() if not ok] == []

    # Half two: the epoch read is now wrong.
    store = make_canonical_store()
    entity = make_entity(key="in-place")
    old = make_assertion(subject_identity=entity.identity_id, predicate="reading")
    store.apply(
        GraphMutationBatch(
            plan_id="pl_1",
            operations=(
                CurationOperation(
                    type=CurationOperationType.CREATE_IDENTITY,
                    payload=entity.model_dump(mode="json"),
                ),
                CurationOperation(
                    type=CurationOperationType.ATTACH_ASSERTION,
                    payload=old.model_dump(mode="json"),
                ),
            ),
        ),
        preconditions=(),
    )
    epoch_n = store.current_epoch()

    retract = store.apply(
        GraphMutationBatch(
            plan_id="pl_2",
            operations=(
                CurationOperation(
                    type=CurationOperationType.RETRACT_ASSERTION,
                    payload={
                        "assertion_id": old.assertion_id,
                        "new_status": CurationStatus.SUPERSEDED.value,
                        "superseded_at": "2026-08-01T00:00:00+00:00",
                    },
                ),
            ),
        ),
        preconditions=(),
    )
    assert retract.committed is True

    at_epoch_n = store.assertions_for(
        entity.identity_id, options=GraphReadOptions(curation_epoch=epoch_n)
    )
    assert at_epoch_n == [], (
        "the in-place mutant is supposed to LOSE the epoch-N reading; if it is "
        "still there, this test is no longer demonstrating the defect"
    )


# --- a mutant the shared suite cannot catch, and the test that does ----------


def _mutate_version_counter_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never advance a subject's ``entity_version``."""
    monkeypatch.setattr(Neo4jCanonicalGraphStore, "_bump_version", lambda self, tx, subject: None)


def test_frozen_version_counter_survives_the_whole_shared_suite(
    make_canonical_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gap, stated as a measurement rather than as a worry.

    ``GraphMutationStoreContract`` only ever asks for ``entity_version="99"``
    against a fresh identity. ``0 != 99`` and ``0 != 99`` — the guard fails
    either way, so freezing the counter changes nothing the suite can see. This
    asserts that directly: all of them green with the counter disabled.

    Run before the next test so the pair reads as "the suite does not catch
    this, and here is what does".
    """
    _mutate_version_counter_frozen(monkeypatch)
    results = run_contract(make_canonical_store)
    assert len(results) == len(CONTRACT_TESTS)
    assert [name for name, ok in results.items() if not ok] == [], (
        "if the shared suite now catches a frozen version counter, this test "
        "has become obsolete - delete it and the one below"
    )


def test_frozen_version_counter_breaks_the_replay_guard(
    make_canonical_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And the defect it hides: a replayed CREATE_IDENTITY clobbers an identity.

    Same assertions as
    ``test_operations_declaration.py::test_entity_version_precondition_guards_replay``
    — literally the same function, not a copy — so this is a falsification of
    that test rather than of a restatement of it.
    """
    # Control: the scenario passes against the unmutated adapter.
    assert_entity_version_guard(make_canonical_store)

    _mutate_version_counter_frozen(monkeypatch)
    with pytest.raises(AssertionError, match="version counter did not advance"):
        assert_entity_version_guard(make_canonical_store)


# --- the gate hole a junit-level check cannot close --------------------------


def shadowed_contract_methods(cls: type) -> set[str]:
    """Contract test methods this class (or a subclass of it) redefines.

    Walks the MRO down to `GraphMutationStoreContract` and collects any
    ``test_*`` name defined *below* it. The base class's own definitions are
    the real ones and are not shadowing.
    """
    expected = set(CONTRACT_TESTS)
    shadowed: set[str] = set()
    for klass in cls.__mro__:
        if klass is GraphMutationStoreContract:
            break
        shadowed |= expected & set(vars(klass))
    return shadowed


def test_the_conforming_class_shadows_no_contract_method() -> None:
    """Closes the last hole in ``suite_gate.py``, which junit alone cannot see.

    A subclass that overrides all ten contract methods with ``pass`` (and a
    ``make_store`` that raises) still emits ten passing testcases under one
    classname, so the gate reports ``10/10 shared contract tests passed`` and
    exits 0. The junit report records *that* the names ran, never *what* ran —
    an XML-level gate cannot distinguish the real body from a stub, and no
    amount of counting or naming fixes that.

    The check has to live at the class level instead, which is here: a
    conforming class must inherit every contract method, not redefine one.
    Overriding is the only way to keep the name while replacing the body, so
    forbidding it closes the case the gate structurally cannot.

    (``make_store`` is *expected* to be defined by the subclass — it is the
    factory the suite asks for — and is not a ``test_*`` name, so it is not
    caught by this and should not be.)
    """
    classes = [
        obj
        for obj in vars(conformance_module).values()
        if isinstance(obj, type)
        and issubclass(obj, GraphMutationStoreContract)
        and obj is not GraphMutationStoreContract
    ]
    assert classes, (
        "test_contract_conformance.py defines no GraphMutationStoreContract "
        "subclass - the conformance suite is not wired up at all"
    )
    for cls in classes:
        assert shadowed_contract_methods(cls) == set(), (
            f"{cls.__name__} redefines {sorted(shadowed_contract_methods(cls))}. "
            f"A conforming class must inherit every contract method unchanged; "
            f"an override keeps the name in the junit report while replacing "
            f"what it does, which suite_gate.py cannot detect."
        )


def test_the_shadowing_check_would_notice_an_override() -> None:
    """Obligation 5 for the check above: it can fail for the reason it names.

    The stub class is defined inside the function, so pytest never collects it
    — it exists only to be inspected.
    """

    class StubbedOutConformance(GraphMutationStoreContract):
        def make_store(self):  # type: ignore[no-untyped-def]
            raise AssertionError("never called - every test below is a no-op")

        def test_create_and_attach_commits_and_returns_new_epoch(self) -> None:
            pass

        def test_snapshot_read_at_old_epoch_hides_later_records(self) -> None:
            pass

    assert shadowed_contract_methods(StubbedOutConformance) == {
        "test_create_and_attach_commits_and_returns_new_epoch",
        "test_snapshot_read_at_old_epoch_hides_later_records",
    }
    # And an honest subclass - one that only supplies the factory - is clean.
    assert (
        shadowed_contract_methods(conformance_module.TestNeo4jGraphMutationStoreContract) == set()
    )


# --- half a window is half a test -------------------------------------------


def _mutate_valid_from_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Honour ``valid_to`` but drop ``valid_from`` — one edge, not both."""

    def half(assertion, valid_at):  # noqa: ANN001, ANN202
        period = assertion.valid_period
        if period.valid_to is not None and valid_at > period.valid_to:
            return False
        return True

    monkeypatch.setattr(store_module, "_valid_at_matches", half)


def test_half_dropped_valid_window_survives_the_shared_suite(
    make_canonical_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measured, not assumed: the shared suite cannot see a dropped lower edge.

    ``test_capability_conformance_for_temporal_options`` probes ``valid_at``
    only *inside* the window and *after* it, so an implementation that dropped
    the ``valid_from`` comparison passes all ten. So did this repo's own
    façade probe, until a reviewer pointed the mutation at it — 84 passed with
    the lower bound deleted.
    """
    _mutate_valid_from_ignored(monkeypatch)
    results = run_contract(make_canonical_store)
    assert len(results) == len(CONTRACT_TESTS)
    assert [name for name, ok in results.items() if not ok] == [], (
        "if the shared suite now probes below valid_from, this test and the "
        "one below are obsolete - delete them"
    )


def test_half_dropped_valid_window_breaks_the_facade_probe(
    make_canonical_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And the check that does catch it — the same assertions, not a copy."""
    # Control: the scenario passes against the unmutated adapter.
    assert_valid_time_window_honoured(make_canonical_store)

    _mutate_valid_from_ignored(monkeypatch)
    with pytest.raises(AssertionError, match="lower bound"):
        assert_valid_time_window_honoured(make_canonical_store)
