"""Evidence evolution: the release-critical scenario, now representable.

This module used to pin a defect. It asserted that the pipeline **could not**
add new evidence to a fact already in the canonical graph, and every test in it
said so on purpose. agentic-kgcs ``f68d1d7`` fixed that, and this module was the
signal to replace — not to adjust — so it now asserts the *correct* behaviour.

The fix, in one sentence
------------------------
``assertion_id`` used to be a pure function of ``candidate_id`` (which excludes
evidence), so one fact could hold only one record and supersession — two records
of one fact, one current — was unrepresentable. At ``f68d1d7`` the planner mints
the id from ``record_seed(fact, object, valid_period, provenance, evidence)``
(``kgcs.records``), which keeps **fact identity** and **record identity** apart:

* ``candidate_id`` is still derived from ``(graph_id, candidate_kind,
  semantic_key)`` with evidence **excluded** — still correct, because the same
  fact cited by a second paper is corroboration, not a second fact;
* ``assertion_id`` now folds in the evidence (and the origin), so re-asserting
  the same fact with new evidence mints a **new record**, and the designed
  supersession path marks the prior record ``SUPERSEDED`` instead of the record
  superseding itself.

Where each claim is asserted
----------------------------
This module is the **fast, producer-level** half: it drives real KGIS
``Candidate`` objects through ``run_curation`` against the reference
``MemoryGraphStore`` and asserts the identity claims at that seam. The
**real-store** half — a supersession actually *applied* against
``Neo4jCanonicalGraphStore``, so the prior record is observably ``SUPERSEDED``
and both evidence sets are readable — lives in ``test_neo4j_curation.py`` and
``tests/migration/neo4j/test_evidence_evolution.py``. The two files used to
overlap; the store-outcome assertions now live only where a store that supports
``RETRACT_ASSERTION`` can produce them, and the identity/`record_seed` unit
tests live here where they need no database at all.

Not one assertion here is a row count. Counting rows would pass against an
adapter that appended a duplicate of the prior record, which is the exact
failure the clock-free seed exists to prevent.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from agentic_kg.migration.config import MigrationConfig
from agentic_kg.migration.curation import CONTRACT_DEFAULT_POLICY, run_curation
from kg_contracts.assertions import Assertion
from kg_contracts.candidates import AttributeAssertionCandidate, SourceCoordinates
from kg_contracts.evidence import EvidenceRef, EvidenceRelationship
from kgcs.executor.executor import ExecutionOutcome
from kgcs.recuration.evolution import ConceptEvolutionPlanner
from kgcs.recuration.triggers import CurationTrigger, TriggerKind
from kgis.ids import DeterministicIdStrategy

from ._synthetic import GRAPH_ID, auto_routing_scores

#: A minted identity to hang the assertion on, so the candidate routes AUTO and
#: reaches the planner instead of being deferred for entity resolution.
SUBJECT = "kg://research/identity/" + "0" * 26

#: The fact under test, held constant across every re-assertion: same subject,
#: same predicate, same value. Only the evidence changes, which is the whole
#: point — this is corroboration, not correction.
SEMANTIC_KEY = "paper/cskg/title"
VALUE = "CS-KG"


def evidence_candidate(evidence_ids: tuple[str, ...]) -> AttributeAssertionCandidate:
    """The same fact, cited by ``evidence_ids``.

    ``candidate_id`` is minted through KGIS's own ``DeterministicIdStrategy``
    with the same arguments ``migration/ingestion/papers.py`` uses, rather than
    by copying the string it produced: a transcribed id would keep agreeing with
    itself after the derivation upstream changed, which is the one thing this
    module must not do.
    """
    ids = DeterministicIdStrategy()
    return AttributeAssertionCandidate(
        candidate_id=ids.candidate_id(
            graph_id=GRAPH_ID,
            candidate_kind="attribute_assertion",
            semantic_key=SEMANTIC_KEY,
        ),
        graph_id=GRAPH_ID,
        producer="test-producer",
        producer_run_id="run-evidence",
        ontology_version="1",
        source_coordinates=SourceCoordinates(
            source_type="paper", locator="paper://doi/10.1007/978-3-031-19433-7_39"
        ),
        semantic_key=SEMANTIC_KEY,
        scores=auto_routing_scores(),
        subject=SUBJECT,
        attribute="title",
        value=VALUE,
        created_at=datetime(2026, 9, 18, tzinfo=UTC),
        evidence_refs=tuple(
            EvidenceRef(evidence_id=e, relationship=EvidenceRelationship.DERIVED_FROM)
            for e in evidence_ids
        ),
    )


def _enabled() -> MigrationConfig:
    return MigrationConfig(use_kgcs_resolution=True)


# --------------------------------------------------------------------------
# Fact identity is stable; record identity is not
# --------------------------------------------------------------------------


def test_new_evidence_does_not_change_the_candidate_id() -> None:
    """Corroboration does not mint a new fact. This half is CORRECT.

    Pinned as the premise of everything below, and pinned as *desirable*: if
    this ever changes, a second paper citing the same fact starts producing a
    second candidate, deduplication collapses, and the graph fills with
    near-duplicate assertions. The fix at ``f68d1d7`` deliberately left this
    alone — it separated the *record* id, not the *fact* id.
    """
    one = evidence_candidate(("ev_A",))
    two = evidence_candidate(("ev_A", "ev_B"))
    assert one.candidate_id == two.candidate_id
    assert one.evidence_refs != two.evidence_refs, (
        "the two candidates must really differ in evidence, or this test and "
        "every test below is comparing a thing to itself"
    )


def test_new_evidence_mints_a_new_assertion_id() -> None:
    """**The fix.** New evidence now produces a record of its own.

    Same fact (``candidate_id`` equal), different evidence, different
    ``assertion_id``: exactly the pair of records a supersession needs. Before
    ``f68d1d7`` these ids were equal and this assertion was its inverse.
    """
    plans = [
        run_curation(
            [evidence_candidate(evidence)],
            config=_enabled(),
            confidence_policy=CONTRACT_DEFAULT_POLICY,
        ).plan
        for evidence in (("ev_A",), ("ev_A", "ev_B"))
    ]
    ids = [p.operations[0].payload["assertion_id"] for p in plans]
    evidence_sets = [
        tuple(r["evidence_id"] for r in p.operations[0].payload["evidence_refs"])
        for p in plans
    ]
    assert evidence_sets[0] != evidence_sets[1], "the payloads must differ in evidence"
    assert ids[0] != ids[1], (
        "assertion_id is still derived from candidate_id alone, so one fact can "
        "hold only one record and supersession is unrepresentable — the defect "
        "this module used to pin has regressed"
    )


# --------------------------------------------------------------------------
# Record id properties, at the planner seam (no database required)
# --------------------------------------------------------------------------


def _seeded_prior():
    """The prior record, its id backfilled from its own ``record_seed``.

    Built without a store on purpose — the properties below are about the id
    derivation, not about any adapter.
    """
    from kg_contracts.testing.factories import make_assertion
    from kgcs.records import backfill_record_id

    evidence = (
        EvidenceRef(evidence_id="ev_A", relationship=EvidenceRelationship.DERIVED_FROM),
    )
    draft = make_assertion(
        subject_identity=SUBJECT,
        predicate="title",
        object_value=VALUE,
        recorded_at=datetime(2026, 9, 18, tzinfo=UTC),
        evidence_refs=evidence,
    )
    return draft.model_copy(update={"assertion_id": backfill_record_id(draft)}), evidence


def test_the_minted_id_is_clock_free() -> None:
    """Re-minting the same successor at a different wall time yields the same id.

    This is the property that makes a true replay collide instead of appending.
    ``recorded_at`` is the only clock-bearing input moved here; if it reached
    the seed, these two ids would differ.
    """
    from datetime import timedelta

    from kgcs.records import assertion_record_seed

    prior, _ = _seeded_prior()
    new_evidence = (
        EvidenceRef(evidence_id="ev_B", relationship=EvidenceRelationship.DERIVED_FROM),
    )
    planner = ConceptEvolutionPlanner(snapshot_version="0")
    once = planner.next_record(prior, evidence_refs=new_evidence, recorded_at=prior.recorded_at)
    twice = planner.next_record(
        prior,
        evidence_refs=new_evidence,
        recorded_at=prior.recorded_at + timedelta(days=365),
    )
    assert once.assertion_id == twice.assertion_id
    # ... and the difference from the prior is the evidence, nothing else.
    assert assertion_record_seed(once) != assertion_record_seed(prior)


def test_a_true_replay_is_refused_rather_than_duplicated() -> None:
    """Same fact, same object, *same* evidence: nothing record-distinguishing.

    ``next_record`` refuses to mint it — the successor would collide with the
    record it is supposedly superseding, which is a replay, not an evolution.
    """
    prior, evidence = _seeded_prior()
    planner = ConceptEvolutionPlanner(snapshot_version="0")
    with pytest.raises(ValueError, match="nothing record-distinguishing"):
        planner.next_record(prior, evidence_refs=evidence, recorded_at=prior.recorded_at)


# --------------------------------------------------------------------------
# Route 1 and route 2: re-attaching
# --------------------------------------------------------------------------


def test_re_attaching_with_the_pinned_empty_snapshot_is_refused_as_a_replay(
    memory_store: object,
) -> None:
    """Route 1: ``STALE``. The new evidence never reaches the graph.

    Still true after the fix, and for the same reason: the refusal is the
    *plan-level snapshot guard*, not the record id. A pipeline that pins
    ``snapshot_version="0"`` commits once in the life of a graph and is inert
    thereafter; this test is the control that says so.
    """
    kwargs = dict(
        config=_enabled(),
        store=memory_store,
        confidence_policy=CONTRACT_DEFAULT_POLICY,
        snapshot_version="0",
    )
    first = run_curation([evidence_candidate(("ev_A",))], **kwargs)
    second = run_curation([evidence_candidate(("ev_A", "ev_B"))], **kwargs)

    assert first.execution.outcome is ExecutionOutcome.COMMITTED
    assert second.execution.outcome is ExecutionOutcome.STALE

    rows = memory_store.assertions_for(SUBJECT)
    assert len(rows) == 1
    assert [e.evidence_id for e in rows[0].evidence_refs] == ["ev_A"], (
        "the new evidence reached the graph despite the STALE refusal"
    )


def test_re_attaching_new_evidence_lands_a_second_distinct_record(
    memory_store: object,
) -> None:
    """Route 2: no shared id, no overwrite. Two records, each with its own evidence.

    The corruption the pin used to demonstrate — two rows sharing one
    ``assertion_id``, which ``kgcs.executor.compensate``'s own docstring warns
    about — cannot happen any more, because the two attaches mint different ids.
    The full version of this scenario, with the prior record observably
    ``SUPERSEDED`` rather than merely superseded-by-intent, is applied against
    the real adapter in ``test_neo4j_curation.py``.
    """
    kwargs = dict(
        config=_enabled(),
        store=memory_store,
        confidence_policy=CONTRACT_DEFAULT_POLICY,
    )
    run_curation([evidence_candidate(("ev_A",))], **kwargs)
    second = run_curation([evidence_candidate(("ev_A", "ev_B"))], **kwargs)
    assert second.execution.outcome is ExecutionOutcome.COMMITTED

    rows = memory_store.assertions_for(SUBJECT)
    assert len(rows) == 2
    assert len({a.assertion_id for a in rows}) == 2, "the two records must not share an id"
    assert [[e.evidence_id for e in a.evidence_refs] for a in rows] == [
        ["ev_A"],
        ["ev_A", "ev_B"],
    ]


# --------------------------------------------------------------------------
# Route 3: the platform's own designed path
# --------------------------------------------------------------------------


def superseding_plan(store_epoch: int):
    """The platform's designed evidence-evolution plan, built from its own parts.

    ``old`` and ``new`` are read out of the planner's own payloads rather than
    hand-built, so the ids under test are the ids the pipeline really produces.
    """
    plans = [
        run_curation(
            [evidence_candidate(evidence)],
            config=_enabled(),
            confidence_policy=CONTRACT_DEFAULT_POLICY,
        ).plan
        for evidence in (("ev_A",), ("ev_A", "ev_B"))
    ]
    old = Assertion.model_validate({**plans[0].operations[0].payload, "curation_epoch": 1})
    new = Assertion.model_validate({**plans[1].operations[0].payload, "curation_epoch": 2})
    trigger = CurationTrigger.of(
        kind=TriggerKind.NEW_EVIDENCE,
        assertion_ids=(old.assertion_id,),
        evidence_ids=("ev_B",),
    )
    result = ConceptEvolutionPlanner(
        snapshot_version=str(store_epoch)
    ).plan_supersession(old_assertion=old, new_assertion=new, trigger=trigger)
    return old, new, result


def test_the_designed_supersession_path_targets_a_distinct_successor() -> None:
    """Route 3: ``plan_supersession`` now retires the old record in favour of a new one.

    ``ATTACH(new)`` followed by ``RETRACT(old, superseded_by=new)``. The record
    being retired and the record that replaces it are different records, which
    is what makes this a supersession rather than the self-deletion it used to
    be. Applied against the real adapter, that leaves the old record
    ``SUPERSEDED`` and the new one current — asserted in ``test_neo4j_curation.py``.
    """
    old, new, result = superseding_plan(store_epoch=1)
    assert old.assertion_id != new.assertion_id, (
        "the old and new assertions are the same record again — the upstream "
        "fix this module exists to assert has regressed"
    )

    types = [op.type.value for op in result.plan.operations]
    assert types == ["ATTACH_ASSERTION", "RETRACT_ASSERTION"]

    attached = result.plan.operations[0].payload
    retract = result.plan.operations[1].payload
    assert attached["assertion_id"] == new.assertion_id
    assert retract["assertion_id"] == old.assertion_id
    assert retract["superseded_by"] == new.assertion_id, (
        "the retraction no longer names the new record as its successor"
    )
    assert retract["assertion_id"] != retract["superseded_by"], (
        "the record supersedes itself again — supersession has regressed"
    )
