"""Evidence evolution: a release-critical criterion this pipeline CANNOT meet.

Read the name of every test here before reading the code. None of them asserts
that evidence evolution works. Each one **pins a defect** so that it is a
checked, executable fact rather than a paragraph, and each will go **red the day
the platform is fixed** — which is the signal to delete it, not to adjust it.

The chain, verified by execution rather than inferred
-----------------------------------------------------
``agentic_kg.migration.ingestion.papers`` mints ``candidate_id`` from
``(graph_id, candidate_kind, semantic_key)``. Evidence is **not** an input — and
that is *correct*: the identity of a fact must not depend on how many sources
cite it, or corroboration from a second paper becomes a different fact and
deduplication collapses.

``kgcs.planner.CurationPlanner._assertion`` then derives the record id from the
fact id::

    assertion_id = ids.assertion_id(f"{candidate.candidate_id}:assertion")

``candidate_id`` identifies a **fact**. ``assertion_id`` identifies a **record of
that fact at a point in time**. Deriving the second from the first means the
model cannot hold two records of one fact — which is exactly what bitemporal
supersession is. That is the defect, and it is upstream in ``kgcs``, not here.

Three routes to "add evidence to a fact already in the graph". All three fail
--------------------------------------------------------------------------------
1. **Re-attach with ``snapshot_version="0"``** (the ``kgcs`` default) —
   ``STALE``. The new evidence never lands. Pinned by
   ``test_re_attaching_with_the_pinned_empty_snapshot_is_refused_as_a_replay``.
2. **Re-attach with the snapshot read from the store** (this subpackage's
   default) — ``COMMITTED``, and the evidence does land, by **overwriting the
   record in place**. The prior record is gone: no ``SUPERSEDED`` row, no
   ``superseded_at``, nothing retained under ``include_superseded``. History was
   rewritten, which §9 law 10 forbids. Against the reference
   ``MemoryGraphStore`` it is worse still — two rows share one ``assertion_id``,
   the corruption ``kgcs.executor.compensate``'s own docstring warns about.
   Pinned by ``test_re_attaching_overwrites_the_record_and_loses_the_old_one``.
3. **The platform's designed path**, ``kgcs.recuration.evolution.plan_supersession``
   — the new and old assertions are the same record, so the plan attaches a
   record and then marks *that same record* ``SUPERSEDED``, with
   ``superseded_by`` pointing at itself. Applied against the real Neo4j adapter
   it reports ``COMMITTED`` and the fact **disappears from the live graph**.
   Pinned by ``test_the_designed_supersession_path_supersedes_the_record_by_itself``
   and its integration companion in ``test_neo4j_curation.py``.

Route 3 is the dangerous one: it is the *correct* API, it returns a green
result, and it deletes a fact from the readable graph as the direct consequence
of adding evidence to it.

**No fix is attempted here.** The repair is upstream — let a re-assertion mint a
new record id for the same fact (derive ``assertion_id`` from the candidate *and*
its content/evidence, or let the caller supply it) — and it is an
ontology-semantics decision for the repository owner, not something to settle
inside a migration PR.
"""

from __future__ import annotations

from datetime import UTC, datetime

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
# The root cause
# --------------------------------------------------------------------------


def test_new_evidence_does_not_change_the_candidate_id() -> None:
    """Corroboration does not mint a new fact. This half is CORRECT.

    Pinned as the premise of everything below, and pinned as *desirable*: if
    this ever changes, a second paper citing the same fact starts producing a
    second candidate, deduplication collapses, and the graph fills with
    near-duplicate assertions. The defect is not here.
    """
    one = evidence_candidate(("ev_A",))
    two = evidence_candidate(("ev_A", "ev_B"))
    assert one.candidate_id == two.candidate_id
    assert one.evidence_refs != two.evidence_refs, (
        "the two candidates must really differ in evidence, or this test and "
        "every test below is comparing a thing to itself"
    )


def test_new_evidence_does_not_change_the_assertion_id_either() -> None:
    """**The defect.** A record id that cannot distinguish two records.

    ``assertion_id`` names a record of a fact at a point in time; deriving it
    from ``candidate_id`` alone means the model cannot represent the fact as it
    stood before the new evidence and as it stands after. Supersession is
    exactly that representation, so supersession cannot be expressed.

    This test goes RED when upstream fixes the derivation. That is the intended
    signal: delete this module, not this assertion.
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
    assert ids[0] == ids[1], (
        "assertion_id now distinguishes two evidence states of one fact — the "
        "upstream defect this module exists to pin has been FIXED. Delete this "
        "module and write the real evidence-evolution acceptance test."
    )


# --------------------------------------------------------------------------
# Route 1 and route 2: re-attaching
# --------------------------------------------------------------------------


def test_re_attaching_with_the_pinned_empty_snapshot_is_refused_as_a_replay(
    memory_store: object,
) -> None:
    """Route 1: ``STALE``. The new evidence never reaches the graph.

    This is the outcome an independent reviewer reported. It reproduces exactly,
    and it is the *least* harmful of the three: it refuses loudly.
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


def test_re_attaching_leaves_two_rows_sharing_one_assertion_id(
    memory_store: object,
) -> None:
    """Route 2 on the reference store: one record id, two rows, both ACTIVE.

    ``kgcs.executor.compensate``'s docstring states the requirement this breaks
    in as many words: "An ``assertion_id`` identifies a record; attaching it
    twice is the same record, and an adapter MUST replace in place." The
    reference ``MemoryGraphStore`` appends instead, so the graph now holds two
    contradicting rows for one id and the next status change picks one of them
    arbitrarily.
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
    assert len(rows) == 2, "the reference store no longer appends a duplicate row"
    assert len({a.assertion_id for a in rows}) == 1, "the two rows must share one id"
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


def test_the_designed_supersession_path_supersedes_the_record_by_itself() -> None:
    """Route 3: ``plan_supersession`` emits a record that supersedes itself.

    ATTACH(x) followed by RETRACT(x, superseded_by=x). Nothing in ``kgcs``
    rejects it, because from the planner's point of view it was handed two
    distinct assertions; they are only the same record because of the id
    derivation two layers up.
    """
    old, new, result = superseding_plan(store_epoch=1)
    assert old.assertion_id == new.assertion_id

    types = [op.type.value for op in result.plan.operations]
    assert types == ["ATTACH_ASSERTION", "RETRACT_ASSERTION"]

    retract = result.plan.operations[1].payload
    assert retract["assertion_id"] == retract["superseded_by"], (
        "the retraction no longer names the attached record as its own "
        "successor — the upstream defect may be fixed; re-check this module"
    )
