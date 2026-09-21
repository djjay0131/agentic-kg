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

import json
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


def _compensate(evolved):
    """Build the compensating plan for the supersession.

    No re-stamping workaround. Before agentic-kgcs#34,
    ``Compensator._carry_snapshot_precondition`` copied the *source plan's*
    snapshot precondition into the compensating plan verbatim; since the plan
    being compensated has by definition already committed, the graph had
    advanced past that snapshot and ``PlanExecutor`` rejected every
    compensation as ``STALE``. This module used to hand-rebuild the
    precondition to get past it.

    Since #34 that is the compensator's own job: ``against_snapshot`` is a
    required keyword-only argument naming the epoch the original plan
    committed at, and the guard is rebased onto it. ``snapshot_guarded`` is
    gone from ``CompensationResult`` (it was a constant, not a signal), so
    ``non_compensable`` is the thing to check.
    """
    from kgcs.executor.compensate import Compensator

    store = evolved["store"]
    compensation = Compensator(snapshot_version=str(store.current_epoch())).compensate(
        evolved["supersede_plan"],
        against_snapshot=evolved["supersede"].new_epoch,
    )
    assert compensation.plan is not None
    assert compensation.non_compensable == ()
    return compensation.plan


def _raw_history(store, assertion_id: str) -> list[dict]:
    """The assertion's stored status history, straight out of Neo4j.

    Read directly rather than through :meth:`assertions_for` because the
    property under test is that *every* entry survives a compensating upsert,
    and the read surface deliberately collapses the history to the one entry
    in force at the requested epoch.
    """
    with store._driver.session(database=store._database) as session:
        record = session.run(
            "MATCH (a:Canon__Assertion {uid: $uid}) RETURN a.status_history AS h",
            uid=f"{store.namespace}\x1f{assertion_id}",
        ).single()
    assert record is not None, f"no assertion node for {assertion_id!r}"
    return json.loads(record["h"])


def test_compensator_rebases_the_snapshot_guard_onto_the_committed_epoch(evolved) -> None:
    """The guard is meetable, so the compensation reaches the store at all.

    The inverse of the test this module used to carry. Under the pre-#34
    compensator the guard named the epoch the source plan was *built* against,
    which its own commit had already invalidated, so the executor refused every
    compensation ``STALE`` before the payload was ever looked at.

    Fails for the reason it names in both directions: if KGCS regresses to
    carrying the source plan's snapshot, ``expected`` is the pre-supersession
    epoch and the first assertion reds; if the guard is rebased but onto the
    wrong epoch, the executor returns ``STALE`` and the second reds.
    """
    from kgcs.planner import SNAPSHOT_PRECONDITION_KIND

    store = evolved["store"]
    plan = _compensate(evolved)

    carried = [p for p in plan.preconditions if p.kind == SNAPSHOT_PRECONDITION_KIND]
    assert len(carried) == 1, "every compensating plan carries exactly one snapshot guard"
    assert carried[0].expected == str(store.current_epoch()), (
        "the guard must name the epoch the source plan committed at, not the "
        "one it was built against"
    )

    executor = PlanExecutor(store, supported_operations=SUPPORTED_OPERATIONS)
    record = executor.execute(plan, is_compensation=True)
    assert record.outcome is ExecutionOutcome.COMMITTED, record.error


def test_supersession_is_reversible_through_the_kgcs_compensator(evolved) -> None:
    """The ``ATTACH↔RETRACT`` inverse actually applies against this adapter.

    Since agentic-kgcs#34 fixed defect (b), the inverse of a supersession
    ``RETRACT_ASSERTION`` is a **full ``Assertion``** — the pre-retraction dump,
    carrying the ``assertion_id`` it restores — rather than the partial
    ``{assertion_id, subject_identity, restore_status, ...}`` restore record it
    used to be. So this now exercises the *first* payload shape
    ``store._apply_attach`` documents, reached as an upsert by id.
    """
    store, entity = evolved["store"], evolved["entity"]
    executor = PlanExecutor(store, supported_operations=SUPPORTED_OPERATIONS)

    record = executor.execute(_compensate(evolved), is_compensation=True)
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


def test_compensating_attach_upserts_without_rewriting_history(evolved) -> None:
    """ADR-0018 Decision 5, discharged without destroying the record's past.

    The obligation: *"a compensating ATTACH_ASSERTION is an UPSERT BY
    ``assertion_id``, never an append ... an adapter MUST replace in place"*.
    The trap: the obvious way to replace in place is to re-run the insert, and
    this adapter's insert is a ``MERGE`` on ``uid`` whose ``SET`` clause
    restamps ``status_history``, ``curation_epoch`` and ``seq``. That upsert
    reports ``COMMITTED`` and silently rewrites history — measured against
    14ffd0e8 before the fix:

        status_history  [{2 ACTIVE}, {3 SUPERSEDED}]  ->  [{4 ACTIVE}]
        curation_epoch  2                             ->  4
        read at epoch 2  the old reading, ACTIVE      ->  []

    Both halves of that are independently fatal: ``curation_epoch`` is the only
    epoch gate on the read path, so restamping it deletes the record from every
    earlier snapshot; and collapsing the history erases the statuses those
    snapshots would have reported.

    This test fails for the reason it names. Reintroduce the overwrite — drop
    the ``existing``-branch in ``_insert_assertion`` — and every numbered
    assertion below reds, while the seven shared contract tests stay green
    (the upstream suite never supersedes a committed assertion at a later epoch
    and reads back at the earlier one; that blind spot is pinned by
    ``test_conformance_is_falsifiable.py::test_in_place_status_mutation_breaks_the_epoch_read``).
    """
    store, entity = evolved["store"], evolved["entity"]
    old_id = evolved["old"].assertion_id
    epoch_n = evolved["attach"].new_epoch
    epoch_supersede = evolved["supersede"].new_epoch

    before = _raw_history(store, old_id)
    assert [e["epoch"] for e in before] == [epoch_n, epoch_supersede], before

    executor = PlanExecutor(store, supported_operations=SUPPORTED_OPERATIONS)
    record = executor.execute(_compensate(evolved), is_compensation=True)
    assert record.outcome is ExecutionOutcome.COMMITTED, record.error
    epoch_rollback = record.new_epoch

    # 1. the upsert is an upsert: one node, not two.
    everything = store.assertions_for(
        entity.identity_id, options=GraphReadOptions(include_superseded=True)
    )
    ids = [a.assertion_id for a in everything]
    assert len(ids) == len(set(ids)) == 2, ids
    assert set(ids) == {old_id, evolved["new"].assertion_id}

    # 2. the history GREW by exactly the rollback entry; nothing was replaced.
    after = _raw_history(store, old_id)
    assert after[: len(before)] == before, (
        f"the compensating upsert rewrote existing history entries: {before} -> {after}"
    )
    assert [e["epoch"] for e in after] == [epoch_n, epoch_supersede, epoch_rollback], after
    assert after[-1]["status"] == CurationStatus.ACTIVE.value

    # 3. the minting epoch is preserved, so earlier snapshots still contain the
    #    record at all. Under the overwrite this list is empty.
    at_n = store.assertions_for(
        entity.identity_id, options=GraphReadOptions(curation_epoch=epoch_n)
    )
    assert [a.assertion_id for a in at_n] == [old_id], [a.assertion_id for a in at_n]
    assert at_n[0].status is CurationStatus.ACTIVE
    assert at_n[0].superseded_at is None
    assert at_n[0].object_value == "the original reading"
    assert at_n[0].curation_epoch == epoch_n, (
        "the returned payload must agree with the node's minting epoch"
    )

    # 4. and the supersession epoch still reports what was true then.
    at_supersede = store.assertions_for(
        entity.identity_id,
        options=GraphReadOptions(curation_epoch=epoch_supersede, include_superseded=True),
    )
    retired = {a.assertion_id: a for a in at_supersede}[old_id]
    assert retired.status is CurationStatus.SUPERSEDED
    assert retired.superseded_at == evolved["new"].recorded_at
