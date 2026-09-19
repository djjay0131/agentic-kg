"""The evidence-evolution scenario, end to end, through the real KGCS stack.

This is the gating item. The claim under test is precise:

    a superseded assertion stays *historically queryable at its own epoch*
    while the new interpretation is current

and the defect it exists to catch (§9.0 obligation 5) is twofold:

1. an adapter that cannot apply ``RETRACT_ASSERTION`` at all, so the whole
   supersession plan comes back ``UNSUPPORTED_OPERATION`` and nothing happens;
2. an adapter that *can* apply it but does so by mutating the assertion in
   place, so a snapshot read at the earlier epoch reports the new status — the
   old interpretation is not preserved, it is overwritten, and §9 law 10 is
   violated while every "it committed" assertion stays green.

Both are shown to turn these tests red:

* defect 1 is exhibited by the **upstream reference store**, which this module
  drives through the same executor and asserts comes back
  ``UNSUPPORTED_OPERATION ('RETRACT_ASSERTION',)``. That is not a local mock —
  it is ``kg_contracts.testing.memory.MemoryGraphStore`` with KGCS's own
  ``DEFAULT_SUPPORTED_OPERATIONS``, so the comparison exercises upstream code
  rather than a restatement of it (§9.0 obligation 3);
* defect 2 is exhibited by
  ``test_conformance_is_falsifiable.py::test_in_place_status_mutation_breaks_the_epoch_read``,
  which patches the adapter to overwrite the status history instead of appending
  to it — exactly what the reference's ``mark_superseded`` does — and asserts
  that the epoch read below then comes back empty while all seven shared
  contract tests stay green.

Nothing here is mocked: a real ``ConceptEvolutionPlanner`` builds the plans, a
real ``PlanExecutor`` applies them, and a real Neo4j holds the result.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from agentic_kg.migration.neo4j import SUPPORTED_OPERATIONS
from kg_contracts.assertions import CurationStatus
from kg_contracts.stores import GraphReadOptions
from kg_contracts.testing.factories import make_assertion, make_entity
from kg_contracts.testing.memory import MemoryGraphStore
from kgcs.executor.executor import ExecutionOutcome, PlanExecutor
from kgcs.recuration.evolution import ConceptEvolutionPlanner
from kgcs.recuration.triggers import CurationTrigger, TriggerKind

pytestmark = pytest.mark.integration

T0 = datetime(2026, 7, 12, tzinfo=UTC)
T1 = T0 + timedelta(days=30)


def _trigger(reason: str) -> CurationTrigger:
    return CurationTrigger.of(
        kind=TriggerKind.NEW_EVIDENCE,
        evidence_ids=("ev_1",),
        reason=reason,
    )


def _planner(snapshot: str) -> ConceptEvolutionPlanner:
    return ConceptEvolutionPlanner(snapshot_version=snapshot)


@pytest.fixture
def evolved(make_canonical_store):
    """Drive one concept through promotion → assertion → supersession.

    Returns ``(store, executor, entity, old, new, epochs)`` where ``epochs`` maps
    the three committed stages to their curation epochs.
    """
    store = make_canonical_store()
    executor = PlanExecutor(store, supported_operations=SUPPORTED_OPERATIONS)

    entity = make_entity(key="concept-evolution")
    old = make_assertion(
        subject_identity=entity.identity_id,
        predicate="interpretation",
        object_value="the original reading",
        recorded_at=T0,
    )
    new = make_assertion(
        subject_identity=entity.identity_id,
        predicate="interpretation",
        object_value="the revised reading",
        recorded_at=T1,
    )

    create = executor.execute(
        _planner("0")
        .plan_promotion(
            candidate=_entity_candidate_for(entity),
            trigger=_trigger("promote"),
            identity_id=entity.identity_id,
        )
        .plan
    )
    assert create.outcome is ExecutionOutcome.COMMITTED, create.error

    attach = executor.execute(
        _planner(str(create.new_epoch))
        .plan_relabel(label_assertion=old, trigger=_trigger("first interpretation"))
        .plan
    )
    assert attach.outcome is ExecutionOutcome.COMMITTED, attach.error

    supersede_plan = (
        _planner(str(attach.new_epoch))
        .plan_supersession(old_assertion=old, new_assertion=new, trigger=_trigger("revised"))
        .plan
    )
    supersede = executor.execute(supersede_plan)

    return {
        "store": store,
        "entity": entity,
        "old": old,
        "new": new,
        "create": create,
        "attach": attach,
        "supersede": supersede,
        "supersede_plan": supersede_plan,
    }


def _entity_candidate_for(entity):
    from kg_contracts.testing.factories import make_entity_candidate

    return make_entity_candidate(
        entity_type=entity.entity_type,
        aliases=entity.aliases,
        key=entity.aliases[0].key,
    )


def test_reference_store_cannot_run_this_scenario(evolved) -> None:
    """The baseline: upstream's own store refuses the supersession plan.

    Run first, because if this ever goes green the rest of the module stops
    being evidence of anything — it would mean ``RETRACT_ASSERTION`` had become
    universally supported and this adapter's contribution was moot.
    """
    reference = MemoryGraphStore()
    reference_executor = PlanExecutor(reference)  # DEFAULT_SUPPORTED_OPERATIONS
    record = reference_executor.execute(evolved["supersede_plan"])

    assert record.outcome is ExecutionOutcome.UNSUPPORTED_OPERATION
    assert record.unsupported_types == ("RETRACT_ASSERTION",)
    assert reference.current_epoch() == 0, "the store must be untouched"


def test_supersession_commits_against_the_neo4j_adapter(evolved) -> None:
    record = evolved["supersede"]
    assert record.outcome is ExecutionOutcome.COMMITTED, record.error
    assert record.new_epoch == evolved["attach"].new_epoch + 1
    assert evolved["store"].current_epoch() == record.new_epoch


def test_new_interpretation_is_the_only_current_one(evolved) -> None:
    store, entity = evolved["store"], evolved["entity"]
    current = store.assertions_for(entity.identity_id)
    assert len(current) == 1, [a.assertion_id for a in current]
    assert current[0].assertion_id == evolved["new"].assertion_id
    assert current[0].status is CurationStatus.ACTIVE


def test_old_assertion_is_still_active_at_its_own_epoch(evolved) -> None:
    """The gating property: read at epoch N after publishing N+1.

    No ``include_superseded``. At the epoch where it was the current reading,
    the old assertion reads back ``ACTIVE`` with no ``superseded_at`` — because
    that *was* its state then. An adapter that mutates status in place returns
    an empty list here (the record is hidden as SUPERSEDED), which is what makes
    this test able to fail for the reason it names.
    """
    store, entity = evolved["store"], evolved["entity"]
    epoch_n = evolved["attach"].new_epoch
    assert epoch_n is not None

    at_epoch_n = store.assertions_for(
        entity.identity_id, options=GraphReadOptions(curation_epoch=epoch_n)
    )
    assert len(at_epoch_n) == 1, [a.assertion_id for a in at_epoch_n]
    assert at_epoch_n[0].assertion_id == evolved["old"].assertion_id
    assert at_epoch_n[0].status is CurationStatus.ACTIVE
    assert at_epoch_n[0].superseded_at is None
    assert at_epoch_n[0].object_value == "the original reading"


def test_old_assertion_is_preserved_not_deleted(evolved) -> None:
    """§9 law 10 — the record survives, marked, with the retiring timestamp."""
    store, entity = evolved["store"], evolved["entity"]
    everything = store.assertions_for(
        entity.identity_id, options=GraphReadOptions(include_superseded=True)
    )
    by_id = {a.assertion_id: a for a in everything}
    assert set(by_id) == {evolved["old"].assertion_id, evolved["new"].assertion_id}

    retired = by_id[evolved["old"].assertion_id]
    assert retired.status is CurationStatus.SUPERSEDED
    assert retired.superseded_at == evolved["new"].recorded_at


def test_transaction_time_window_closes_at_the_supersession(evolved) -> None:
    """Bitemporal read: the old reading is current *as of* a time before T1."""
    store, entity = evolved["store"], evolved["entity"]
    before = store.assertions_for(
        entity.identity_id,
        options=GraphReadOptions(transaction_at=T1 - timedelta(days=1), include_superseded=True),
    )
    assert [a.assertion_id for a in before] == [evolved["old"].assertion_id]

    after = store.assertions_for(
        entity.identity_id,
        options=GraphReadOptions(transaction_at=T1, include_superseded=True),
    )
    assert [a.assertion_id for a in after] == [evolved["new"].assertion_id]


def _compensate(evolved, *, restamp: bool):
    """Build the compensating plan for the supersession.

    ``restamp=True`` replaces the carried snapshot precondition with one naming
    the graph's *current* epoch — see
    :func:`test_compensator_carries_a_stale_snapshot_precondition` for why that
    is necessary and what it says about upstream.
    """
    from kgcs.executor.compensate import Compensator
    from kgcs.planner import SNAPSHOT_PRECONDITION_KIND

    store = evolved["store"]
    compensation = Compensator(snapshot_version=str(store.current_epoch())).compensate(
        evolved["supersede_plan"]
    )
    assert compensation.plan is not None
    assert compensation.fully_compensable
    if not restamp:
        return compensation.plan
    current = str(store.current_epoch())
    return compensation.plan.model_copy(
        update={
            "snapshot_version": current,
            "preconditions": tuple(
                p.model_copy(update={"expected": current})
                if p.kind == SNAPSHOT_PRECONDITION_KIND
                else p
                for p in compensation.plan.preconditions
            ),
        }
    )


def test_compensator_carries_a_stale_snapshot_precondition(evolved) -> None:
    """Upstream defect, recorded by a test rather than by a comment.

    ``Compensator._carry_snapshot_precondition`` copies the *source plan's*
    snapshot precondition into the compensating plan verbatim. The
    ``snapshot_version`` constructor argument only stamps
    ``CurationPlan.snapshot_version``; it does not reach the precondition. But
    the plan being compensated has, by definition, already committed — so the
    graph has advanced past that snapshot and ``PlanExecutor`` (which enforces
    snapshot guards itself whenever it holds a `GraphReader`, and the store *is*
    one) rejects every compensation as ``STALE``.

    This is not a property of this adapter: it reproduces against any store the
    executor can read. Asserted here so the workaround in the next test is
    visibly a workaround.
    """
    from kgcs.planner import SNAPSHOT_PRECONDITION_KIND

    store = evolved["store"]
    plan = _compensate(evolved, restamp=False)
    carried = [p for p in plan.preconditions if p.kind == SNAPSHOT_PRECONDITION_KIND]
    assert carried, "the compensating plan is expected to carry a snapshot guard"
    assert carried[0].expected != str(store.current_epoch())

    executor = PlanExecutor(store, supported_operations=SUPPORTED_OPERATIONS)
    record = executor.execute(plan, is_compensation=True)
    assert record.outcome is ExecutionOutcome.STALE
    assert store.current_epoch() == int(evolved["supersede"].new_epoch)


def test_supersession_is_reversible_through_the_kgcs_compensator(evolved) -> None:
    """The ``ATTACH↔RETRACT`` inverse actually applies against this adapter.

    ``Compensator`` builds the inverse of a ``RETRACT_ASSERTION`` as an
    ``ATTACH_ASSERTION`` whose payload is the retract's ``reversal_data`` — i.e.
    ``{assertion_id, subject_identity, restore_status, ...}``, **not** a full
    ``Assertion``. An adapter that only accepted full-assertion attach payloads
    would reject every rollback of a supersession, so this exercises the second
    payload shape ``store._apply_attach`` documents.

    The snapshot precondition is re-stamped at the current epoch first; without
    that the executor never reaches the store at all (previous test).
    """
    store, entity = evolved["store"], evolved["entity"]
    executor = PlanExecutor(store, supported_operations=SUPPORTED_OPERATIONS)

    record = executor.execute(_compensate(evolved, restamp=True), is_compensation=True)
    assert record.outcome is ExecutionOutcome.COMMITTED, record.error

    restored = store.assertions_for(entity.identity_id)
    assert evolved["old"].assertion_id in {a.assertion_id for a in restored}, (
        "the retracted assertion must be ACTIVE again after compensation"
    )
    # And the history is still a history: the supersession epoch still shows
    # the old assertion as SUPERSEDED. Compensation adds a record; it does not
    # erase one.
    at_supersession = store.assertions_for(
        entity.identity_id,
        options=GraphReadOptions(
            curation_epoch=evolved["supersede"].new_epoch, include_superseded=True
        ),
    )
    retired = {a.assertion_id: a for a in at_supersession}[evolved["old"].assertion_id]
    assert retired.status is CurationStatus.SUPERSEDED
