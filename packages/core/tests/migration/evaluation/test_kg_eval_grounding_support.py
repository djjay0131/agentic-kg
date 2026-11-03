"""Regression: the pinned ``kg_eval`` counts ``DERIVED_FROM`` as grounding.

The RQ1 brief names a provenance-scoring bug: ``_unsupported_assertions`` used
to count only ``SUPPORTS`` refs while every KGIS producer emits
``DERIVED_FROM``, so real extraction output scored 100% unsupported. The fix
landed upstream in agentic-kgis #60/#62 and is present at this repo's pinned
v0.6.0, so there is no upstream PR to open for this branch. This test pins the
*resolved* dependency's behaviour: if a future re-pin regresses the rule, this
fails in the migration CI job rather than silently reporting every extraction as
unsupported again.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from kg_contracts.candidates import (
    CandidateScores,
    EntityCandidate,
    SourceCoordinates,
)
from kg_contracts.evidence import (
    EvidenceRef,
    EvidenceRelationship,
    Provenance,
    present_evidence,
)
from kg_contracts.identity import EntityRef
from kg_eval import ArmConfig, ArmOutput, GoldEntity, GoldSet, evaluate_extraction

NOW = datetime(2026, 10, 26, tzinfo=UTC)


def _candidate(relationship: EvidenceRelationship, evidence_id: str) -> EntityCandidate:
    return EntityCandidate(
        graph_id="baseball",
        producer="kgis",
        producer_run_id="run-1",
        ontology_version="2026-10",
        entity_type="Player",
        aliases=(EntityRef(entity_type="Player", namespace="baseball", key="ada"),),
        semantic_key="player/baseball/ada",
        source_coordinates=SourceCoordinates(
            source_type="scouting_report", locator="s3://reports/doc-1.txt"
        ),
        scores=CandidateScores(extraction_confidence=0.9, source_reliability=0.7),
        evidence_refs=(EvidenceRef(evidence_id=evidence_id, relationship=relationship),),
    )


def _evidence(evidence_id: str):
    return present_evidence(
        evidence_id=evidence_id,
        source_type="scouting_report",
        source_locator="s3://reports/doc-1.txt",
        observed_at=NOW,
        provenance=Provenance(source="report", actor="kgis"),
        content="Ada is a shortstop known for hitting.",
    )


def _gold() -> GoldSet:
    return GoldSet(
        gold_set_id="derived-from-grounding",
        entities=(
            GoldEntity(
                entity_type="Player",
                semantic_key="player/baseball/ada",
            ),
        ),
    )


def test_derived_from_grounds_but_does_not_verify() -> None:
    output = ArmOutput(
        arm=ArmConfig(arm_id="kgis-extraction"),
        candidates=(_candidate(EvidenceRelationship.DERIVED_FROM, "ev-1"),),
        evidence={"ev-1": _evidence("ev-1")},
    )
    metrics = evaluate_extraction(output, _gold())
    assert metrics.unsupported_assertion_count == 0
    assert metrics.unverified_assertion_count == 1


def test_supports_grounds_and_verifies() -> None:
    output = ArmOutput(
        arm=ArmConfig(arm_id="kgis-verification"),
        candidates=(_candidate(EvidenceRelationship.SUPPORTS, "ev-2"),),
        evidence={"ev-2": _evidence("ev-2")},
    )
    metrics = evaluate_extraction(output, _gold())
    assert metrics.unsupported_assertion_count == 0
    assert metrics.unverified_assertion_count == 0


@pytest.mark.parametrize(
    "relationship",
    [EvidenceRelationship.CONTRADICTS, EvidenceRelationship.CONTEXTUALIZES],
)
def test_non_grounding_relationships_stay_unsupported(
    relationship: EvidenceRelationship,
) -> None:
    output = ArmOutput(
        arm=ArmConfig(arm_id="non-grounding"),
        candidates=(_candidate(relationship, "ev-3"),),
        evidence={"ev-3": _evidence("ev-3")},
    )
    metrics = evaluate_extraction(output, _gold())
    assert metrics.unsupported_assertion_count == 1
