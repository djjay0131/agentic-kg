"""Turn synthesis output into review-queue candidates — never canonical writes.

ADR-0009: on the canonical path the SynthesisAgent proposes, and a human
approves. This module builds the same ``kg_contracts`` candidate shapes the
nightly ingest produces and wraps each one in a ``ReviewItem``. It imports the
opt-in ``migration`` extra, so it is only imported *inside* a canonical-path
method; a default install never reaches it.

The review item's ``candidate_payload`` is exactly what
:func:`agentic_kg.migration.curation.commit.commit_candidate` expects, so an
approval re-enters ``run_curation`` -> ``PlanExecutor`` and no code here ever
holds a ``GraphMutationStore``.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from agentic_kg.agents.provenance import AgentProvenance
from agentic_kg.agents.schemas import GraphUpdate, SynthesisItem, SynthesisReport
from agentic_kg.knowledge_graph.models.review_item import (
    ReviewEvidenceRef,
    ReviewItem,
)

AGENT_ORIGIN = "agent:synthesis"
ADVISER_TYPE = "agent.synthesis"
#: A human approval is the identity evidence for a synthesized problem, so
#: the candidate states that confidence explicitly instead of inheriting the
#: extractor's 0.8 and making the identity guard guess (see curation/policy.py).
AGENT_IDENTITY_CONFIDENCE = 1.0
AGENT_EXTRACTION_CONFIDENCE = 0.8
AGENT_SOURCE_RELIABILITY = 0.75


def _stable_candidate_id(run_id: str, action: str, key: str) -> str:
    digest = hashlib.sha256(f"{run_id}\x1f{action}\x1f{key}".encode("utf-8")).hexdigest()
    return f"agent-{digest[:24]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _scores() -> Any:
    from kg_contracts.candidates import CandidateScores

    return CandidateScores(
        extraction_confidence=AGENT_EXTRACTION_CONFIDENCE,
        source_reliability=AGENT_SOURCE_RELIABILITY,
        identity_confidence=AGENT_IDENTITY_CONFIDENCE,
    )


def _source_coordinates(*, run_id: str) -> Any:
    from kg_contracts.candidates import SourceCoordinates

    return SourceCoordinates(
        source_type="agent",
        locator=f"agent://synthesis/{run_id}",
    )


def _evidence_refs(evidence_ids: list[str]) -> tuple[Any, ...]:
    from kg_contracts.evidence import EvidenceRef, EvidenceRelationship

    return tuple(
        EvidenceRef(
            evidence_id=str(eid),
            relationship=EvidenceRelationship.SUPPORTS,
        )
        for eid in dict.fromkeys(evidence_ids)
        if eid
    )


def build_entity_candidate(
    *,
    run_id: str,
    statement: str,
    evidence_ids: list[str],
    properties: dict[str, Any] | None = None,
) -> Any:
    """A ``Problem`` EntityCandidate for one synthesized problem."""
    from kg_contracts.candidates import EntityCandidate
    from kg_contracts.identity import EntityRef

    candidate_id = _stable_candidate_id(run_id, "create_problem", statement)
    props = {
        "origin": AGENT_ORIGIN,
        "workflow_run_id": run_id,
        **(properties or {}),
    }
    return EntityCandidate(
        candidate_id=candidate_id,
        graph_id="research",
        producer=AGENT_ORIGIN,
        producer_run_id=run_id,
        ontology_version="2026-09-18.1",
        source_coordinates=_source_coordinates(run_id=run_id),
        semantic_key=f"agent-problem/{candidate_id}",
        scores=_scores(),
        entity_type="Problem",
        aliases=(
            EntityRef(
                entity_type="Problem",
                namespace="surface",
                key=statement[:200],
            ),
        ),
        display_name=statement[:200],
        properties=props,
        evidence_refs=_evidence_refs(evidence_ids),
    )


def build_relation_candidate(
    *,
    run_id: str,
    source_id: str,
    target_id: str,
    relation_type: str,
    evidence_ids: list[str],
) -> Any:
    """A ``RelationCandidate``; endpoints may be aliases (not committable)."""
    from kg_contracts.candidates import RelationCandidate

    key = f"{source_id}->{target_id}:{relation_type}"
    return RelationCandidate(
        candidate_id=_stable_candidate_id(run_id, "create_relation", key),
        graph_id="research",
        producer=AGENT_ORIGIN,
        producer_run_id=run_id,
        ontology_version="2026-09-18.1",
        source_coordinates=_source_coordinates(run_id=run_id),
        semantic_key=f"agent-relation/{_stable_candidate_id(run_id, 'rel-key', key)}",
        scores=_scores(),
        relation_type=relation_type,
        subject=source_id,
        object=target_id,
        properties={
            "origin": AGENT_ORIGIN,
            "workflow_run_id": run_id,
        },
        evidence_refs=_evidence_refs(evidence_ids),
    )


def _review_item(
    *,
    candidate: Any,
    namespace: str,
    run_id: str,
    output_id: str,
    model: str | None,
    prompt_version: str,
    rationale: str,
    evidence_ids: list[str],
    committable: bool,
    blocker: str | None,
    agent_output_id: str,
) -> ReviewItem:
    evidence_refs = [
        ReviewEvidenceRef(
            evidence_id=str(eid),
            relationship="SUPPORTS",
        )
        for eid in dict.fromkeys(evidence_ids)
        if eid
    ]
    payload = (
        candidate.model_dump(mode="json") if hasattr(candidate, "model_dump") else dict(candidate)
    )
    candidate_kind = (
        candidate.candidate_kind
        if hasattr(candidate, "candidate_kind")
        else str(payload.get("candidate_kind") or "status_update")
    )
    candidate_id = payload.get("candidate_id") or _stable_candidate_id(
        run_id, candidate_kind, output_id + rationale[:80]
    )
    now = _now_iso()
    return ReviewItem.build(
        namespace=namespace,
        run_id=run_id,
        candidate_id=str(candidate_id),
        now=now,
        updated_at=now,
        candidate_kind=candidate_kind,
        entity_type=getattr(candidate, "entity_type", None) or payload.get("entity_type"),
        candidate_summary=rationale[:240],
        candidate_payload=payload,
        evidence_refs=evidence_refs,
        adviser_verdict="admit",
        confidence=1.0,
        rationale=rationale,
        adviser_abstained=False,
        adviser_type=ADVISER_TYPE,
        adviser_version=prompt_version,
        adviser_model_id=model or "",
        adviser_model_version="",
        prompt_version=prompt_version,
        status="pending",
        committable=committable,
        commit_blocker=blocker,
        source="agent",
        agent_name="synthesis",
        agent_output_id=agent_output_id,
    )


def build_synthesis_review_items(
    report: SynthesisReport,
    *,
    namespace: str,
    run_id: str,
    trace_id: str,
    source_problem_id: str,
    model: str | None,
    prompt_version: str,
    output_id: str,
    provenance: AgentProvenance,
    evidence_ids: list[str],
) -> tuple[list[ReviewItem], list[SynthesisItem]]:
    """Build (not commit) one review item per proposed canonical change."""
    from kg_contracts.identity import is_identity_id

    items: list[ReviewItem] = []
    outputs: list[SynthesisItem] = []

    for statement in report.new_problems:
        candidate = build_entity_candidate(
            run_id=run_id,
            statement=statement,
            evidence_ids=evidence_ids,
            properties={
                "trace_id": trace_id,
                "derived_from": source_problem_id,
                "agent_output_id": output_id,
            },
        )
        item = _review_item(
            candidate=candidate,
            namespace=namespace,
            run_id=run_id,
            output_id=output_id,
            model=model,
            prompt_version=prompt_version,
            rationale=f"Agent synthesis proposes a new problem: {statement[:160]}",
            evidence_ids=evidence_ids,
            committable=True,
            blocker=None,
            agent_output_id=output_id,
        )
        items.append(item)
        outputs.append(
            SynthesisItem(
                action="create_problem",
                target_id=item.id,
                statement=statement,
                review_item_id=item.id,
                committable=True,
                provenance=provenance,
            )
        )

    for relation in report.new_relations:
        source_id = str(relation.get("source_id") or "")
        target_id = str(relation.get("target_id") or "")
        if not source_id or not target_id:
            continue
        relation_type = str(relation.get("type") or "RELATED_TO").upper()
        candidate = build_relation_candidate(
            run_id=run_id,
            source_id=source_id,
            target_id=target_id,
            relation_type=relation_type,
            evidence_ids=evidence_ids,
        )
        committable = is_identity_id(source_id) and is_identity_id(target_id)
        blocker = (
            None if committable else "an endpoint is still an alias; entity resolution is not wired"
        )
        item = _review_item(
            candidate=candidate,
            namespace=namespace,
            run_id=run_id,
            output_id=output_id,
            model=model,
            prompt_version=prompt_version,
            rationale=f"Agent synthesis proposes a {relation_type} relation",
            evidence_ids=evidence_ids,
            committable=committable,
            blocker=blocker,
            agent_output_id=output_id,
        )
        items.append(item)
        outputs.append(
            SynthesisItem(
                action="create_relation",
                target_id=item.id,
                relation_type=relation_type,
                review_item_id=item.id,
                committable=committable,
                commit_blocker=blocker,
                provenance=provenance,
            )
        )

    if report.source_problem_id or source_problem_id:
        target = report.source_problem_id or source_problem_id
        payload = {
            "candidate_kind": "status_update",
            "candidate_id": _stable_candidate_id(run_id, "update_status", target),
            "action": "update_status",
            "target_id": target,
            "proposed_status": "resolved",
            "origin": AGENT_ORIGIN,
            "workflow_run_id": run_id,
        }
        item = _review_item(
            candidate=payload,
            namespace=namespace,
            run_id=run_id,
            output_id=output_id,
            model=model,
            prompt_version=prompt_version,
            rationale=(
                f"Agent synthesis proposes marking {target} resolved; status "
                "changes have no KGCS candidate operation in this slice"
            ),
            evidence_ids=evidence_ids,
            committable=False,
            blocker=(
                "status changes have no KGCS candidate operation in this slice; "
                "the item is queued for a human decision only"
            ),
            agent_output_id=output_id,
        )
        items.append(item)
        outputs.append(
            SynthesisItem(
                action="update_status",
                target_id=item.id,
                review_item_id=item.id,
                committable=False,
                commit_blocker=item.commit_blocker,
                provenance=provenance,
            )
        )

    return items, outputs


def graph_updates_from_items(
    items: list[ReviewItem], outputs: list[SynthesisItem]
) -> list[GraphUpdate]:
    """Human-facing audit entries: a queued review item, not a graph write."""
    updates: list[GraphUpdate] = []
    for item, output in zip(items, outputs, strict=True):
        updates.append(
            GraphUpdate(
                action="review_item",
                target_id=item.id,
                details=(
                    f"queued {output.action} for human approval (committable={output.committable})"
                ),
            )
        )
    return updates


__all__ = [
    "AGENT_ORIGIN",
    "build_entity_candidate",
    "build_relation_candidate",
    "build_synthesis_review_items",
    "graph_updates_from_items",
]
