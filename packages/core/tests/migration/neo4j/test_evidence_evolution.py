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
from kg_contracts.curation import CurationOperation, CurationOperationType
from kg_contracts.stores import GraphMutationBatch, GraphReadOptions
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


def _compensate(evolved, *, against_snapshot=None):
    """Build the compensating plan for the supersession.

    ``against_snapshot`` defaults to the epoch the supersession committed at.
    Scenarios that commit something else before rolling back pass the graph's
    current epoch instead — the executor enforces the guard against the live
    epoch, so a rollback applied after an unrelated write has to name that
    epoch or be refused ``STALE`` before it reaches the store.

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
    gone from ``CompensationResult`` (it was a constant, not a signal).
    ``fully_compensable`` is *not* gone — it survives as a derived property
    over the one remaining field — but ``non_compensable`` is asserted here
    directly, because naming the empty tuple says which operations were
    uncompensable rather than only that some were.
    """
    from kgcs.executor.compensate import Compensator

    store = evolved["store"]
    if against_snapshot is None:
        against_snapshot = evolved["supersede"].new_epoch
    compensation = Compensator(snapshot_version=str(store.current_epoch())).compensate(
        evolved["supersede_plan"],
        against_snapshot=against_snapshot,
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
    the ``existing`` branch in ``_insert_assertion`` — and assertions 1 to 4
    all red, while the seven shared contract tests stay green (the upstream
    suite never supersedes a committed assertion at a later epoch and reads
    back at the earlier one; that blind spot is pinned by
    ``test_conformance_is_falsifiable.py::test_in_place_status_mutation_breaks_the_epoch_read``).

    Each numbered assertion is independently sensitive to exactly one carried
    property, verified by mutating them one at a time:

    ===============================  ==========================
    mutation of the ``existing`` branch   reddens
    ===============================  ==========================
    restamp ``seq``                  #1 (read order)
    drop the history append          #2
    restamp ``curation_epoch``       #3
    drop the payload ``model_copy``  #3 (payload epoch)
    ===============================  ==========================

    #1 originally compared *sets* of assertion ids, which the base branch
    already satisfied — it could not fail for the reason it named, and a
    seq-restamping mutant survived the whole suite. It compares order now.
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

    # 1. the upsert is an upsert: one node, not two — and `seq` is preserved,
    #    which is observable only as read ORDER. `_read_assertions` sorts
    #    `ORDER BY a.seq`, so restamping seq on the upsert silently flips the
    #    list from old-first to new-first. Asserting the order rather than the
    #    set is what makes the `seq` carry-over able to fail at all; with a set
    #    comparison alone this criterion is vacuous.
    everything = store.assertions_for(
        entity.identity_id, options=GraphReadOptions(include_superseded=True)
    )
    ids = [a.assertion_id for a in everything]
    assert len(ids) == len(set(ids)) == 2, ids
    assert ids == [old_id, evolved["new"].assertion_id], (
        f"the compensating upsert restamped `seq` and reordered the read: {ids}"
    )

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


# --- placement is owned by REASSIGN / MERGE / SPLIT, never by an attach -------
#
# These four came out of an independent review of the fix above (R12, finding
# H-1) plus the search it prompted for others of the same shape. The class:
# `_insert_assertion`'s upsert writes every column from a payload captured
# BEFORE whatever later operation moved the record, so any column a later
# committed operation owns is silently reverted by a rollback that reports
# COMMITTED. History (epoch/seq/status_history) was the first face; placement
# (subject/object identity and merge lineage) is the second.


def _identity(store, key: str):
    """Commit one bare identity and return it."""
    other = make_entity(key=key)
    result = store.apply(
        GraphMutationBatch(
            plan_id=f"pl_identity_{key}",
            operations=(
                CurationOperation(
                    type=CurationOperationType.CREATE_IDENTITY,
                    payload=other.model_dump(mode="json"),
                ),
            ),
        ),
        preconditions=(),
    )
    assert result.committed is True, result.error
    return other


def _commit(store, plan_id: str, *operations):
    result = store.apply(
        GraphMutationBatch(plan_id=plan_id, operations=operations), preconditions=()
    )
    assert result.committed is True, result.error
    return result


def test_compensating_attach_preserves_a_committed_reassignment(evolved) -> None:
    """H-1: a rollback must not silently revert a ``REASSIGN_ASSERTION``.

    Newly reachable because of this pin. Before agentic-kgcs#34 the inverse of
    a supersession ``RETRACT`` was a partial restore record, which routes to
    ``_append_status`` and touches status only. Since #34 it is a full
    ``Assertion``, so it routes to ``_insert_assertion``, whose ``MERGE … SET``
    writes ``subject_identity`` from a payload captured *before* the
    reassignment.

    Why this is worse than the history rewrite it rhymes with: placement is not
    epoch-versioned. A restamped ``curation_epoch`` at least leaves the record
    visible at later epochs; a reverted subject leaves **no trace at any
    epoch** — the reassignment target reads empty across the whole sweep, so
    the committed operation is gone rather than superseded.

    Fails for the reason it names: revert to writing
    ``subject_identity=assertion.subject_identity`` in ``_insert_assertion``
    and the record reappears under the original entity while the sweep over the
    reassignment target is empty at every epoch.
    """
    store, entity = evolved["store"], evolved["entity"]
    old_id = evolved["old"].assertion_id
    other = _identity(store, "reassign-target")

    reassigned = _commit(
        store,
        "pl_reassign",
        CurationOperation(
            type=CurationOperationType.REASSIGN_ASSERTION,
            payload={
                "assertion_id": old_id,
                "from_identity": entity.identity_id,
                "to_identity": other.identity_id,
            },
        ),
    )

    executor = PlanExecutor(store, supported_operations=SUPPORTED_OPERATIONS)
    record = executor.execute(
        _compensate(evolved, against_snapshot=store.current_epoch()), is_compensation=True
    )
    assert record.outcome is ExecutionOutcome.COMMITTED, record.error

    # The reassignment stands: the restored assertion is on the NEW subject.
    on_target = store.assertions_for(other.identity_id)
    assert [a.assertion_id for a in on_target] == [old_id], (
        f"the compensating upsert reverted a committed REASSIGN_ASSERTION: "
        f"{[a.assertion_id for a in on_target]}"
    )
    assert on_target[0].status is CurationStatus.ACTIVE
    assert on_target[0].subject_identity == other.identity_id, (
        "the payload handed to readers must agree with the stored subject"
    )

    # ...and it is not also still on the old one.
    on_origin = store.assertions_for(
        entity.identity_id, options=GraphReadOptions(include_superseded=True)
    )
    assert old_id not in {a.assertion_id for a in on_origin}

    # The target is non-empty from the reassignment epoch onward, which is the
    # half that goes silent under the defect: with the subject reverted the
    # record is absent from this identity at EVERY epoch, because placement is
    # not epoch-versioned and so has no earlier state to fall back to.
    #
    # `include_superseded` is required here and its absence would be a bug in
    # this test, not in the adapter: at the reassignment epoch the assertion
    # was still SUPERSEDED — the rollback lands an epoch later — so a default
    # read is right to hide it. The placement claim is about *which identity*
    # holds the record, which holds independently of its status.
    at_reassign = store.assertions_for(
        other.identity_id,
        options=GraphReadOptions(curation_epoch=reassigned.new_epoch, include_superseded=True),
    )
    assert [a.assertion_id for a in at_reassign] == [old_id], (
        f"the reassignment left no trace at its own epoch: {[a.assertion_id for a in at_reassign]}"
    )
    assert at_reassign[0].status is CurationStatus.SUPERSEDED


def test_compensating_attach_preserves_merge_lineage(evolved) -> None:
    """The fourth carried property, found by generalising H-1 rather than by review.

    ``_insert_assertion``'s ``MERGE`` used to end ``SET … a.subject_lineage =
    NULL, a.object_lineage = NULL`` unconditionally. Those two columns are the
    breadcrumb ``MERGE_IDENTITIES`` leaves so its declared inverse can find what
    to take back: ``_move_subject`` matches ``a.subject_lineage = $only_lineage``
    and ``_restore_objects`` matches ``a.object_lineage``.

    So a compensation landing between a merge and its split does not corrupt
    one record — it breaks the ``MERGE``/``SPLIT`` inverse pair. The split still
    reports ``COMMITTED``; it just silently moves nothing, because the row it
    would have matched no longer carries the lineage.

    Fails for the reason it names: restore the unconditional ``= NULL`` and the
    final assertion reds with the assertion still stranded on the survivor.
    """
    store, entity = evolved["store"], evolved["entity"]
    old_id = evolved["old"].assertion_id
    survivor = _identity(store, "merge-survivor")

    _commit(
        store,
        "pl_merge",
        CurationOperation(
            type=CurationOperationType.MERGE_IDENTITIES,
            payload={
                "survivor_identity": survivor.identity_id,
                "merged_identities": [entity.identity_id],
            },
        ),
    )
    on_survivor = store.assertions_for(
        survivor.identity_id, options=GraphReadOptions(include_superseded=True)
    )
    assert old_id in {a.assertion_id for a in on_survivor}, "the merge must have moved it"

    # The compensation lands while the merge is in force.
    executor = PlanExecutor(store, supported_operations=SUPPORTED_OPERATIONS)
    record = executor.execute(
        _compensate(evolved, against_snapshot=store.current_epoch()), is_compensation=True
    )
    assert record.outcome is ExecutionOutcome.COMMITTED, record.error

    # Now undo the merge. This is the step that goes silently wrong.
    _commit(
        store,
        "pl_split",
        CurationOperation(
            type=CurationOperationType.SPLIT_IDENTITY,
            payload={
                "source_identity": survivor.identity_id,
                "into_identities": [entity.identity_id],
            },
        ),
    )

    back_home = store.assertions_for(
        entity.identity_id, options=GraphReadOptions(include_superseded=True)
    )
    assert old_id in {a.assertion_id for a in back_home}, (
        "the compensating upsert cleared subject_lineage, so SPLIT_IDENTITY "
        "could not find the assertion to take back - the MERGE/SPLIT inverse "
        "pair is broken, not just one record"
    )
    stranded = store.assertions_for(
        survivor.identity_id, options=GraphReadOptions(include_superseded=True)
    )
    assert old_id not in {a.assertion_id for a in stranded}


def test_upsert_preserves_object_placement_and_its_merge_lineage(make_canonical_store) -> None:
    """The object side of placement, which the supersession scenario never reaches.

    ``evolved``'s assertions carry an ``object_value``, not an
    ``object_identity``, so nothing above can tell whether ``object_identity``
    and ``object_lineage`` are carried across an upsert or restamped from the
    payload — a mutant that restamped ``object_identity`` survived the whole
    suite until this test existed.

    Built from a plain re-ATTACH rather than a compensation on purpose. The
    upsert branch is reachable from any re-assertion of an existing id (ids are
    content-derived), the stale-payload shape is identical, and it keeps the
    scenario down to the four operations that actually matter:

    1. ``X`` asserts something *about* ``subject``, pointing at ``target``;
    2. ``target`` is merged into ``survivor`` — ``_redirect_objects`` moves
       ``X.object_identity`` to ``survivor`` and leaves ``object_lineage =
       target`` as the breadcrumb;
    3. ``X`` is re-attached from its **original** payload, which still says
       ``object_identity = target``;
    4. ``survivor`` is split back into ``target``.

    Step 3 is the write under test. Restamping ``object_identity`` reverts a
    committed merge (step 4's assertion on ``survivor`` reds); clearing
    ``object_lineage`` leaves ``_restore_objects`` nothing to match, so the
    split silently does nothing and step 4's assertion on ``target`` reds.
    """
    store = make_canonical_store()
    subject = make_entity(key="obj-subject")
    target = make_entity(key="obj-target")
    survivor = make_entity(key="obj-survivor")

    linked = make_assertion(
        subject_identity=subject.identity_id,
        predicate="cites",
        # `Assertion` requires exactly one of object_value/object_identity, and
        # the factory defaults object_value to 200 — this assertion points at an
        # identity, which is the whole point of the scenario.
        object_value=None,
        object_identity=target.identity_id,
    )
    _commit(
        store,
        "pl_obj_seed",
        *(
            CurationOperation(
                type=CurationOperationType.CREATE_IDENTITY, payload=e.model_dump(mode="json")
            )
            for e in (subject, target, survivor)
        ),
        CurationOperation(
            type=CurationOperationType.ATTACH_ASSERTION,
            payload=linked.model_dump(mode="json"),
        ),
    )

    def _object_of() -> str | None:
        held = store.assertions_for(
            subject.identity_id, options=GraphReadOptions(include_superseded=True)
        )
        return {a.assertion_id: a for a in held}[linked.assertion_id].object_identity

    assert _object_of() == target.identity_id

    _commit(
        store,
        "pl_obj_merge",
        CurationOperation(
            type=CurationOperationType.MERGE_IDENTITIES,
            payload={
                "survivor_identity": survivor.identity_id,
                "merged_identities": [target.identity_id],
            },
        ),
    )
    assert _object_of() == survivor.identity_id, "the merge must have redirected the object"

    # The upsert, from the pre-merge payload.
    _commit(
        store,
        "pl_obj_reattach",
        CurationOperation(
            type=CurationOperationType.ATTACH_ASSERTION,
            payload=linked.model_dump(mode="json"),
        ),
    )
    assert _object_of() == survivor.identity_id, (
        "the upsert restamped object_identity from a stale payload and reverted "
        "a committed MERGE_IDENTITIES"
    )

    # And the merge is still undoable, which is what object_lineage is for.
    _commit(
        store,
        "pl_obj_split",
        CurationOperation(
            type=CurationOperationType.SPLIT_IDENTITY,
            payload={
                "source_identity": survivor.identity_id,
                "into_identities": [target.identity_id],
            },
        ),
    )
    assert _object_of() == target.identity_id, (
        "the upsert cleared object_lineage, so SPLIT_IDENTITY could not find "
        "the assertion to redirect back"
    )


# --- the pre-#34 restore-record payload shape --------------------------------


def _restore_op(assertion_id: str, subject_identity: str, **extra):
    """The partial payload KGCS emitted before #34, built by hand.

    Nothing upstream produces this any more (``plan_supersession`` always sets
    ``INVERSE_PAYLOAD_KEY``), but ``_build_inverse`` still falls back to the flat
    ``reversal_data`` when that key is absent, so a *persisted* pre-#34 plan
    still lands here. It is the canonical write path, so it is tested rather
    than trusted.
    """
    return CurationOperation(
        type=CurationOperationType.ATTACH_ASSERTION,
        payload={"assertion_id": assertion_id, "subject_identity": subject_identity, **extra},
    )


def test_restore_record_reinstates_without_a_full_assertion(evolved) -> None:
    """The compat branch works: a partial payload restores the retracted record.

    Covers the second shape ``_apply_attach`` documents, which the
    evidence-evolution scenario stopped exercising when #34 made the
    compensator emit full assertions.
    """
    store, entity = evolved["store"], evolved["entity"]
    old_id = evolved["old"].assertion_id
    epoch_supersede = evolved["supersede"].new_epoch

    assert old_id not in {a.assertion_id for a in store.assertions_for(entity.identity_id)}

    _commit(store, "pl_restore", _restore_op(old_id, entity.identity_id))

    assert old_id in {a.assertion_id for a in store.assertions_for(entity.identity_id)}
    # It appends, like every other status write: the supersession epoch still
    # reports what was true then.
    at_supersede = store.assertions_for(
        entity.identity_id,
        options=GraphReadOptions(curation_epoch=epoch_supersede, include_superseded=True),
    )
    assert {a.assertion_id: a for a in at_supersede}[old_id].status is CurationStatus.SUPERSEDED


def test_restore_record_cannot_be_used_to_retract(evolved) -> None:
    """The restore branch is not a back door around ``_RETRACTABLE_TO``.

    ``_apply_retract`` refuses to move an assertion to anything outside
    ``{SUPERSEDED, REVOKED}`` and points callers *here* to reinstate one. The
    converse guard was missing: ``restore_status`` accepted any parseable
    status, so ``ATTACH_ASSERTION`` could retract — to ``REVOKED``, which the
    read surface does not hide by default (kg_contracts issue #8), and without
    the ``superseded_at`` stamping ``_apply_retract`` performs.

    The two sets partition ``CurationStatus``, so this asserts the whole
    complement rather than one hand-picked value.
    """
    store, entity = evolved["store"], evolved["entity"]
    old_id = evolved["old"].assertion_id

    refused = [s for s in CurationStatus if s is not CurationStatus.ACTIVE]
    assert refused, "the guard is vacuous if ACTIVE is the only status"

    for status in refused:
        result = store.apply(
            GraphMutationBatch(
                plan_id=f"pl_bad_{status.value}",
                operations=(_restore_op(old_id, entity.identity_id, restore_status=status.value),),
            ),
            preconditions=(),
        )
        assert result.committed is False, f"{status.value} must not be reachable via attach"
        assert "unsupported_status" in (result.error or ""), result.error

    # And the one that is allowed still is.
    _commit(
        store,
        "pl_good",
        _restore_op(old_id, entity.identity_id, restore_status=CurationStatus.ACTIVE.value),
    )
    assert old_id in {a.assertion_id for a in store.assertions_for(entity.identity_id)}


# --- evidence evolution proper: same fact, new evidence -> a NEW record -------
#
# Everything above supersedes one *hand-built* assertion with another. That
# exercises the store, not the identity rule: the two ids were independent
# inputs, so "a new record appeared" was true by construction.
#
# agentic-kgcs f68d1d7 changes where an `assertion_id` comes from. It is now
# minted from `record_seed(fact_id, object, valid_period, evidence_refs,
# provenance)` -- **clock-free**, so:
#
#   * a true replay (same fact, same object, same evidence) collides onto the
#     record it already minted and the executor refuses it via the per-subject
#     `assertion_absent` guard; while
#   * the same fact carrying NEW evidence hashes to a DIFFERENT id and is a new
#     record.
#
# The tests below assert that **by identity**. Counting rows would pass against
# an adapter that appended a duplicate of the prior record, which is the exact
# failure the clock-free seed exists to prevent.


@pytest.fixture
def evidence_evolution(make_canonical_store):
    """One fact, promoted and asserted, then re-asserted citing new evidence.

    The prior assertion's id is **backfilled from its own record seed**
    (`kgcs.records.backfill_record_id`) rather than left as the model default,
    because the property under test is a relation between two seeded ids. A
    prior whose id came from somewhere else would make "the ids differ" true for
    an uninteresting reason.
    """
    from kg_contracts.evidence import EvidenceRef, EvidenceRelationship
    from kgcs.records import assertion_record_seed, backfill_record_id

    store = make_canonical_store()
    executor = PlanExecutor(store, supported_operations=SUPPORTED_OPERATIONS)

    entity = make_entity(key="evidence-evolution")
    first_evidence = (
        EvidenceRef(evidence_id="ev_original", relationship=EvidenceRelationship.SUPPORTS),
    )
    draft = make_assertion(
        subject_identity=entity.identity_id,
        predicate="interpretation",
        object_value="the original reading",
        recorded_at=T0,
        evidence_refs=first_evidence,
    )
    prior = draft.model_copy(update={"assertion_id": backfill_record_id(draft)})

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
        .plan_relabel(label_assertion=prior, trigger=_trigger("original evidence"))
        .plan
    )
    assert attach.outcome is ExecutionOutcome.COMMITTED, attach.error

    new_evidence = (
        EvidenceRef(evidence_id="ev_corroborating", relationship=EvidenceRelationship.SUPPORTS),
    )
    planner = _planner(str(attach.new_epoch))
    successor = planner.next_record(prior, evidence_refs=new_evidence, recorded_at=T1)

    supersede_plan = planner.plan_supersession(
        old_assertion=prior, new_assertion=successor, trigger=_trigger("new evidence")
    ).plan
    supersede = executor.execute(supersede_plan)
    assert supersede.outcome is ExecutionOutcome.COMMITTED, supersede.error

    return {
        "store": store,
        "executor": executor,
        "entity": entity,
        "prior": prior,
        "successor": successor,
        "first_evidence": first_evidence,
        "new_evidence": new_evidence,
        "attach_epoch": attach.new_epoch,
        "supersede_plan": supersede_plan,
        "seed": assertion_record_seed,
    }


def test_new_evidence_mints_a_different_record_id(evidence_evolution) -> None:
    """The identity claim, at the source: new evidence changes the seed.

    Asserted against ``record_seed`` itself as well as against the two ids, so
    a failure says *which* half broke - a planner that stopped seeding, or a
    seed that stopped including evidence.
    """
    prior, successor = evidence_evolution["prior"], evidence_evolution["successor"]
    seed = evidence_evolution["seed"]

    assert successor.assertion_id != prior.assertion_id
    assert seed(successor) != seed(prior)
    # ... and the difference is the evidence, nothing else.
    assert successor.object_value == prior.object_value
    assert successor.predicate == prior.predicate
    assert successor.subject_identity == prior.subject_identity
    assert successor.valid_period == prior.valid_period


def test_the_minted_id_is_clock_free(evidence_evolution) -> None:
    """Re-minting the same successor at a different wall time yields the same id.

    This is the property that makes a true replay collide instead of appending.
    ``recorded_at`` is the only clock-bearing input moved here; if it reached
    the seed, these two ids would differ.
    """
    from datetime import timedelta

    planner = _planner("0")
    prior = evidence_evolution["prior"]
    once = planner.next_record(
        prior, evidence_refs=evidence_evolution["new_evidence"], recorded_at=T1
    )
    twice = planner.next_record(
        prior,
        evidence_refs=evidence_evolution["new_evidence"],
        recorded_at=T1 + timedelta(days=365),
    )
    assert once.assertion_id == twice.assertion_id == evidence_evolution["successor"].assertion_id


def test_a_true_replay_is_refused_rather_than_duplicated(evidence_evolution) -> None:
    """Same fact, same object, *same* evidence: nothing record-distinguishing.

    ``next_record`` refuses to mint it - the successor would collide with the
    record it is supposedly superseding, which is a replay, not an evolution.
    """
    planner = _planner("0")
    with pytest.raises(ValueError, match="nothing record-distinguishing"):
        planner.next_record(
            evidence_evolution["prior"],
            evidence_refs=evidence_evolution["first_evidence"],
            recorded_at=T1,
        )


def test_replaying_the_committed_plan_is_refused_by_the_assertion_absent_guard(
    evidence_evolution,
) -> None:
    """The executor's half of the same rule, against our store's reader.

    A re-planned replay of an already-committed ATTACH must not append a second
    copy. ``PlanExecutor`` reads the store back through the
    ``assertion_absent`` precondition and returns ``STALE`` *before touching
    it*, naming the guard that failed.
    """
    store = evidence_evolution["store"]
    before = store.current_epoch()

    replay = evidence_evolution["executor"].execute(evidence_evolution["supersede_plan"])

    assert replay.outcome is ExecutionOutcome.STALE
    assert replay.batch_id is None
    assert store.current_epoch() == before, "a refused replay must not advance the epoch"
    assert replay.failed_preconditions, "STALE must name what failed"


def test_the_successor_is_the_only_current_record_by_identity(evidence_evolution) -> None:
    store, entity = evidence_evolution["store"], evidence_evolution["entity"]
    current = store.assertions_for(entity.identity_id)
    assert [a.assertion_id for a in current] == [
        evidence_evolution["successor"].assertion_id
    ]
    assert current[0].evidence_refs == evidence_evolution["new_evidence"]


def test_the_prior_record_still_cites_its_original_evidence(evidence_evolution) -> None:
    """The half a row count cannot see.

    The prior record must still be reachable *and* still carry the evidence it
    was asserted on - not the successor's. An adapter that upserted the new
    evidence onto the old row would keep two rows and pass any count.
    """
    store, entity = evidence_evolution["store"], evidence_evolution["entity"]
    by_id = {
        a.assertion_id: a
        for a in store.assertions_for(
            entity.identity_id, options=GraphReadOptions(include_superseded=True)
        )
    }
    assert set(by_id) == {
        evidence_evolution["prior"].assertion_id,
        evidence_evolution["successor"].assertion_id,
    }
    retired = by_id[evidence_evolution["prior"].assertion_id]
    assert retired.status is CurationStatus.SUPERSEDED
    assert retired.evidence_refs == evidence_evolution["first_evidence"]
    assert retired.object_value == "the original reading"


def test_the_prior_record_is_still_current_at_its_own_epoch(evidence_evolution) -> None:
    """And reachable with no flags at all, by identity, at the epoch it held."""
    store, entity = evidence_evolution["store"], evidence_evolution["entity"]
    at_epoch = store.assertions_for(
        entity.identity_id,
        options=GraphReadOptions(curation_epoch=evidence_evolution["attach_epoch"]),
    )
    assert [a.assertion_id for a in at_epoch] == [evidence_evolution["prior"].assertion_id]
    assert at_epoch[0].status is CurationStatus.ACTIVE
    assert at_epoch[0].evidence_refs == evidence_evolution["first_evidence"]
