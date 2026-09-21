"""The same run, against the real canonical adapter.

Everything else in this suite applies plans to
``kg_contracts.testing.memory.MemoryGraphStore`` — the *reference* store, which
implements two of the seven operation types. That makes the fast tier fast and
it makes it wrong about anything the reference store cannot do: a
``RETRACT_ASSERTION`` comes back ``UNSUPPORTED_OPERATION`` there, so the
rollback round trip is unobservable.

This module runs the identical pipeline against
:class:`Neo4jCanonicalGraphStore`, which implements six of the seven, and reads
the results back **through the read-only façade** rather than off the plan.

Marked ``integration`` because it needs a database. In the
``migration-canonical-adapter`` CI job Docker is present and these run; the
job's gate rejects any skip in the report, so a silently skipped container here
turns that job red rather than green.
"""

from __future__ import annotations

import pytest
from agentic_kg.migration.config import MigrationConfig
from agentic_kg.migration.curation import (
    CONTRACT_DEFAULT_POLICY,
    STRUCTURED_IDENTITY_POLICY,
    roll_back,
    run_curation,
)
from agentic_kg.migration.neo4j import SUPPORTED_OPERATIONS
from kg_contracts.stores import GraphReadOptions
from kgcs.executor.executor import ExecutionOutcome

from ._synthetic import attachable_attribute_candidate, graded_entity_candidate

pytestmark = pytest.mark.integration


def test_the_shadow_corpus_curates_into_the_canonical_graph(
    shadow_candidates: tuple[object, ...],
    enabled_config: MigrationConfig,
    make_canonical_store,
) -> None:
    """Eight DOI-keyed identities reach Neo4j and are readable back.

    The identities are read through ``store.read_only()`` — the façade that is
    structurally not a write surface — rather than off the plan that created
    them. Both sides of the claim coming from the plan would prove only that
    the planner agrees with itself.
    """
    store = make_canonical_store()
    result = run_curation(
        shadow_candidates,
        config=enabled_config,
        store=store,
        confidence_policy=STRUCTURED_IDENTITY_POLICY,
        supported_operations=SUPPORTED_OPERATIONS,
    )
    assert result.execution is not None
    assert result.execution.outcome is ExecutionOutcome.COMMITTED
    assert result.execution.new_epoch == 1
    assert result.published_epoch == 1

    reader = store.read_only()
    minted = [
        op.payload["identity_id"]
        for op in result.plan.operations
        if op.type.value == "CREATE_IDENTITY"
    ]
    assert len(minted) == 8
    for identity_id in minted:
        assert reader.get_entity(identity_id) is not None, (
            f"{identity_id} committed but is not readable from Neo4j"
        )


def test_a_replayed_plan_is_refused_by_the_real_store(
    shadow_candidates: tuple[object, ...],
    enabled_config: MigrationConfig,
    make_canonical_store,
) -> None:
    """Idempotent replay against the adapter, not only against the reference."""
    store = make_canonical_store()
    kwargs = dict(
        config=enabled_config,
        store=store,
        confidence_policy=STRUCTURED_IDENTITY_POLICY,
        supported_operations=SUPPORTED_OPERATIONS,
        snapshot_version="0",
    )
    first = run_curation(shadow_candidates, **kwargs)
    second = run_curation(shadow_candidates, **kwargs)
    assert first.execution.outcome is ExecutionOutcome.COMMITTED
    assert second.execution.outcome is ExecutionOutcome.STALE
    assert second.committed_candidate_ids == ()
    assert store.current_epoch() == 1


def test_an_attachment_rolls_back_through_the_real_adapter(
    enabled_config: MigrationConfig, make_canonical_store
) -> None:
    """The rollback the reference store cannot execute, executed.

    ``RETRACT_ASSERTION`` is outside the reference store's two supported types,
    so ``test_rollback.py`` can only check the compensating *plan*. Here the
    compensation actually applies, and the retraction is observed as a status
    change on the assertion — not as its disappearance. History is never
    rewritten (§9 law 10): the record stays, superseded.
    """
    store = make_canonical_store()
    shared = dict(
        config=enabled_config,
        store=store,
        confidence_policy=CONTRACT_DEFAULT_POLICY,
        supported_operations=SUPPORTED_OPERATIONS,
    )
    entity = run_curation([graded_entity_candidate()], **shared)
    assert entity.committed
    identity_id = entity.engine.outcomes[0].resolution.resolved_identity

    attach = run_curation(
        [attachable_attribute_candidate(subject=identity_id)], **shared
    )
    assert attach.committed
    assert attach.operation_counts() == {"ATTACH_ASSERTION": 1}

    reader = store.read_only()
    before = reader.assertions_for(identity_id)
    assert len(before) == 1
    assertion_id = before[0].assertion_id

    rollback = roll_back(
        attach, store=store, supported_operations=SUPPORTED_OPERATIONS
    )
    assert rollback.compensation.plan is not None
    assert rollback.execution is not None
    assert rollback.execution.outcome is ExecutionOutcome.COMMITTED
    assert rollback.execution.is_compensation is True
    assert rollback.fully_reversed is True

    after = reader.assertions_for(identity_id, GraphReadOptions(include_superseded=True))
    assert [a.assertion_id for a in after] == [assertion_id], (
        "the rolled-back assertion was deleted rather than superseded"
    )
    assert after[0].status.value == "SUPERSEDED"
    assert reader.assertions_for(identity_id) == [], (
        "a superseded assertion is still visible to an ordinary read"
    )


def test_a_create_identity_run_is_still_not_reversible_here(
    shadow_candidates: tuple[object, ...],
    enabled_config: MigrationConfig,
    make_canonical_store,
) -> None:
    """The adapter's wider operation support does not create a missing inverse.

    ``CREATE_IDENTITY`` has no inverse in the contract's operation vocabulary,
    which is a fact about ``kg_contracts``, not about any store. A reader might
    reasonably expect the richer adapter to fix it; it does not, and the
    partial rollback is reported the same way here as against the reference.
    """
    store = make_canonical_store()
    result = run_curation(
        shadow_candidates,
        config=enabled_config,
        store=store,
        confidence_policy=STRUCTURED_IDENTITY_POLICY,
        supported_operations=SUPPORTED_OPERATIONS,
    )
    rollback = roll_back(result, store=store, supported_operations=SUPPORTED_OPERATIONS)
    assert rollback.compensation.plan is None
    assert len(rollback.non_compensable) == 8
    assert rollback.fully_reversed is False
    assert store.current_epoch() == 1
