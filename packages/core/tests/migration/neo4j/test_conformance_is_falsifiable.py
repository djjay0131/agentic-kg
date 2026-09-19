"""Mutation tests: proof that the conformance suite can actually go red.

§9.0 obligation 5 — "a criterion must be able to fail for the reason it names.
State the defect the check exists to catch, and confirm that an implementation
exhibiting that defect turns it red — a mutation is the cheap way."

A green conformance run says nothing on its own. A suite wired to a store that
silently no-ops, or fixtures that make every assertion vacuous, passes exactly
as convincingly as a correct one. So each defect the shared suite is supposed to
catch is injected here, one at a time, and the specific test that should notice
is asserted to fail. The control case — the unmutated adapter passing all seven
— runs in the same module against the same helper, so "all seven" is measured
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
        "test_snapshot_read_at_old_epoch_hides_later_records",
        "test_superseded_assertions_hidden_by_default_visible_with_flag",
        "test_transaction_at_filters_by_half_open_recorded_superseded_window",
    )


def test_unmutated_adapter_passes_every_contract_test(make_canonical_store) -> None:
    """The control case, measured by the same harness as the mutants."""
    results = run_contract(make_canonical_store)
    assert len(results) == 7
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
    monkeypatch.setattr(store_module, "_status_visible", lambda status, options: True)


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
    all seven contract tests green while destroying the property the
    evidence-evolution scenario depends on.

    This asserts both halves: the seven stay green, and the epoch read goes
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
