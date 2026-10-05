"""Rollback: a compensating plan through the same executor, and what it cannot undo.

The headline finding lives here. The plan this repo's curation path actually
emits today is ``CREATE_IDENTITY`` operations, and ``CREATE_IDENTITY`` has **no
inverse** in the v1 ``CurationOperationType`` vocabulary. So a committed
curation run on this pipeline is, right now, not reversible — and the tests
below assert that rather than papering over it with an ``execution=None`` that
a reader would take for "there was nothing to undo".
"""

from __future__ import annotations

import pytest
from agentic_kg.migration.config import MigrationConfig
from agentic_kg.migration.curation import (
    CONTRACT_DEFAULT_POLICY,
    STRUCTURED_IDENTITY_POLICY,
    NotRollbackable,
    RollbackResult,
    roll_back,
    run_curation,
)
from kgcs.executor.executor import ExecutionOutcome

from ._synthetic import attachable_attribute_candidate, graded_entity_candidate


def test_a_create_identity_plan_is_not_reversible_and_says_so(
    shadow_candidates: tuple[object, ...],
    enabled_config: MigrationConfig,
    memory_store: object,
) -> None:
    """Every operation lands in ``non_compensable``; no compensating plan exists.

    ``fully_reversed`` must be False. The failure mode this guards is a caller
    reading ``execution is None`` as "nothing needed undoing" and reporting a
    clean rollback of eight identities that are still in the graph.
    """
    result = run_curation(
        shadow_candidates,
        config=enabled_config,
        store=memory_store,
        confidence_policy=STRUCTURED_IDENTITY_POLICY,
    )
    assert result.committed

    rollback = roll_back(result, store=memory_store)
    assert rollback.compensation.plan is None
    assert len(rollback.non_compensable) == 8
    assert {op.type.value for op in rollback.non_compensable} == {"CREATE_IDENTITY"}
    assert rollback.execution is None
    assert rollback.fully_reversed is False
    assert memory_store.current_epoch() == 1


def test_the_identities_are_still_in_the_graph_after_a_failed_rollback(
    shadow_candidates: tuple[object, ...],
    enabled_config: MigrationConfig,
    memory_store: object,
) -> None:
    """"Not reversible" is a fact about the graph, not only about the result.

    Read back through the store. A rollback reported as partial while the data
    had in fact disappeared would be just as wrong as the reverse.
    """
    result = run_curation(
        shadow_candidates,
        config=enabled_config,
        store=memory_store,
        confidence_policy=STRUCTURED_IDENTITY_POLICY,
    )
    minted = [
        op.payload["identity_id"]
        for op in result.plan.operations
        if op.type.value == "CREATE_IDENTITY"
    ]
    roll_back(result, store=memory_store)
    assert all(memory_store.get_entity(i) is not None for i in minted)


def test_an_attach_assertion_compensates_to_a_retraction(
    enabled_config: MigrationConfig, memory_store: object
) -> None:
    """The one operation the v1 vocabulary can invert, built through the real Compensator.

    The compensating plan is checked; applying it against the *reference* store
    is expected to be refused, because ``MemoryGraphStore`` implements only
    ``CREATE_IDENTITY`` and ``ATTACH_ASSERTION``. That refusal is reported as
    ``UNSUPPORTED_OPERATION`` rather than crashing, and ``fully_reversed`` stays
    False even though the compensation itself was complete — the two halves of
    that property are separately necessary. The Neo4j adapter, which does
    implement ``RETRACT_ASSERTION``, is exercised in ``test_neo4j_curation.py``.
    """
    entity = graded_entity_candidate()
    first = run_curation(
        [entity],
        config=enabled_config,
        store=memory_store,
        confidence_policy=CONTRACT_DEFAULT_POLICY,
    )
    assert first.committed
    identity_id = first.engine.outcomes[0].resolution.resolved_identity
    assert identity_id is not None

    attribute = attachable_attribute_candidate(subject=identity_id)
    second = run_curation(
        [attribute],
        config=enabled_config,
        store=memory_store,
        confidence_policy=CONTRACT_DEFAULT_POLICY,
    )
    assert second.committed
    assert second.operation_counts() == {"ATTACH_ASSERTION": 1}

    rollback = roll_back(second, store=memory_store)
    assert rollback.compensation.plan is not None
    assert [op.type.value for op in rollback.compensation.plan.operations] == [
        "RETRACT_ASSERTION"
    ]
    assert rollback.compensation.fully_compensable is True
    assert rollback.against_snapshot == second.execution.new_epoch
    assert rollback.execution is not None
    assert rollback.execution.outcome is ExecutionOutcome.UNSUPPORTED_OPERATION
    assert rollback.execution.is_compensation is True
    assert rollback.fully_reversed is False, (
        "a compensation the store refused must not be reported as a completed "
        "rollback"
    )


def test_the_compensation_is_guarded_against_the_committed_epoch(
    enabled_config: MigrationConfig, memory_store: object
) -> None:
    """The guard expects the epoch the forward plan committed at, not the plan's own.

    Carrying the source plan's snapshot expectation over would be stale by
    construction — the forward commit is what invalidated it — and every
    compensation would be refused before reaching a store (KGCS ADR-0018).
    """
    entity = graded_entity_candidate()
    first = run_curation(
        [entity],
        config=enabled_config,
        store=memory_store,
        confidence_policy=CONTRACT_DEFAULT_POLICY,
    )
    attribute = attachable_attribute_candidate(
        subject=first.engine.outcomes[0].resolution.resolved_identity
    )
    second = run_curation(
        [attribute],
        config=enabled_config,
        store=memory_store,
        confidence_policy=CONTRACT_DEFAULT_POLICY,
    )
    rollback = roll_back(second, store=memory_store)
    plan = rollback.compensation.plan
    assert plan is not None
    snapshots = [p.expected for p in plan.preconditions if p.kind == "snapshot_version"]
    assert snapshots == [str(second.execution.new_epoch)]
    assert snapshots != [second.plan.snapshot_version]


def test_rolling_back_a_run_that_never_committed_raises(
    shadow_candidates: tuple[object, ...],
    enabled_config: MigrationConfig,
    memory_store: object,
) -> None:
    """No commit, no compensation — an inverse against state that never existed."""
    plan_only = run_curation(
        shadow_candidates,
        config=enabled_config,
        confidence_policy=STRUCTURED_IDENTITY_POLICY,
    )
    with pytest.raises(NotRollbackable):
        roll_back(plan_only, store=memory_store)


def test_fully_reversed_needs_both_a_complete_plan_and_a_committed_apply() -> None:
    """Either half alone is not a rollback.

    Driven directly on the result type, because the interesting combination —
    a fully compensable plan whose apply did not commit — is the one a caller
    is most likely to misread, and the store paths above cannot produce every
    combination on demand.
    """
    from kgcs.executor.compensate import CompensationResult

    complete = CompensationResult(plan=None, non_compensable=())
    assert complete.fully_compensable is True
    assert (
        RollbackResult(
            compensation=complete, execution=None, against_snapshot=1
        ).fully_reversed
        is False
    )
