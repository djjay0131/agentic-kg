"""Review-queue items for the nightly pipeline (P2).

A ``ReviewItem`` is one KGIS candidate the deterministic admission policy
deferred, graded once by the bounded adviser, and waiting for a human decision.
It is *canonical-path workflow state* rather than canonical graph content: the
node lives in the same staging Neo4j database as the canonical graph, carries
the ``Canon__`` prefix and an ``ns`` namespace, but it is never an
``Identity``/``Assertion`` and no canonical reader selects it (ADR-0004/0005).

Storage shape (Neo4j)
---------------------
Neo4j properties cannot hold nested maps or lists of maps, so the candidate
payload, evidence references and committed identity ids are JSON strings:

======================  ======================================================
property                shape
======================  ======================================================
``uid``                 ``"<ns>\\x1f<run_id>\\x1f<candidate_id>"`` (MERGE key)
``id``                  stable, human-facing id derived from ``uid``
``run_id``              str
``ns``                  canonical namespace
``candidate_id``        str (KGIS candidate id)
``candidate_kind``      str (entity | relation | attribute_assertion | ...)
``entity_type``         str | absent (Problem, ResearchConcept, ...)
``adviser_verdict``     ``admit`` | ``reject`` | ``merge-into:<id>``
``status``              ``pending`` | ``approved`` | ``rejected``
``candidate_payload``   JSON str -> dict
``evidence_refs``       JSON str -> list[{evidence_id, relationship, content}]
``committed_identity_ids`` JSON str -> list[str]
======================  ======================================================

Idempotency is on ``(run_id, candidate_id)``, enforced by the ``uid`` MERGE in
:mod:`agentic_kg.knowledge_graph.review_items`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, Mapping, Optional

from pydantic import BaseModel, Field

#: ASCII unit separator, the same convention the canonical adapter uses for its
#: synthetic ``uid``. Restated rather than imported so this model does not reach
#: into ``migration.neo4j`` (the API imports this module on a default install).
UID_SEP = "\x1f"

#: A review item's lifecycle.
ReviewItemStatus = Literal["pending", "approved", "rejected"]

#: A human decision accepted by the API.
ReviewDecision = Literal["approve", "reject"]

#: The adviser verdict sentinels. ``merge-into:<id>`` is stored verbatim.
VERDICT_ADMIT = "admit"
VERDICT_REJECT = "reject"
VERDICT_MERGE_PREFIX = "merge-into:"

#: ``ReviewItem.adviser_verdict`` values the queue currently understands.
VERDICT_VALUES = (VERDICT_ADMIT, VERDICT_REJECT)


def review_item_uid(namespace: str, run_id: str, candidate_id: str) -> str:
    """The synthetic primary key: one item per ``(run, candidate)``."""
    return f"{namespace}{UID_SEP}{run_id}{UID_SEP}{candidate_id}"


def review_item_id(namespace: str, run_id: str, candidate_id: str) -> str:
    """A stable, URL-safe id derived from the idempotency key.

    Hashing keeps the id short even though a KGIS candidate id and a nightly
    run id are both long. Because the input is the idempotency key, a re-run
    that sees the same candidate produces the same id, so a retry cannot queue
    a duplicate under a new name.
    """
    digest = hashlib.sha256(
        review_item_uid(namespace, run_id, candidate_id).encode("utf-8")
    ).hexdigest()
    return f"review_{digest[:32]}"


def is_merge_verdict(verdict: str) -> bool:
    return verdict.startswith(VERDICT_MERGE_PREFIX) and bool(
        verdict[len(VERDICT_MERGE_PREFIX) :]
    )


class ReviewEvidenceRef(BaseModel):
    """One piece of evidence as it is shown to the human reviewer."""

    evidence_id: str
    relationship: Optional[str] = None
    content: Optional[str] = None


class ReviewAgreement(BaseModel):
    """Adviser-vs-human agreement for one entity type, to date."""

    entity_type: str
    decided: int = 0
    agreed: int = 0
    agreement_rate: Optional[float] = None


class ReviewItem(BaseModel):
    """One deferred candidate, its adviser grade, and the human decision."""

    id: str
    uid: str
    run_id: str
    namespace: str
    candidate_id: str
    candidate_kind: str
    entity_type: Optional[str] = None
    candidate_summary: str = ""
    candidate_payload: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[ReviewEvidenceRef] = Field(default_factory=list)
    adviser_verdict: str = VERDICT_REJECT
    confidence: Optional[float] = None
    rationale: str = ""
    adviser_abstained: bool = False
    adviser_type: str = ""
    adviser_version: str = ""
    adviser_model_id: str = ""
    adviser_model_version: str = ""
    prompt_version: str = ""
    #: ``adviser`` for the nightly KGIS adviser stage, ``agent`` for a research
    #: agent that proposed the item (ADR-0010).
    source: str = "adviser"
    agent_name: str = ""
    agent_output_id: str = ""
    status: ReviewItemStatus = "pending"
    committable: bool = True
    commit_blocker: Optional[str] = None
    created_at: str = ""
    updated_at: Optional[str] = None
    decided_by: Optional[str] = None
    decided_at: Optional[str] = None
    decision_note: Optional[str] = None
    committed_epoch: Optional[int] = None
    committed_identity_ids: list[str] = Field(default_factory=list)

    @classmethod
    def build(
        cls,
        *,
        namespace: str,
        run_id: str,
        candidate_id: str,
        now: str,
        **fields: Any,
    ) -> "ReviewItem":
        """Construct with the two derived identifiers filled in."""
        return cls(
            id=review_item_id(namespace, run_id, candidate_id),
            uid=review_item_uid(namespace, run_id, candidate_id),
            run_id=run_id,
            namespace=namespace,
            candidate_id=candidate_id,
            created_at=now,
            **fields,
        )

    def to_neo4j_properties(self) -> dict[str, Any]:
        """Flatten the item into Neo4j-settable scalar properties."""
        return {
            "uid": self.uid,
            "id": self.id,
            "run_id": self.run_id,
            "ns": self.namespace,
            "candidate_id": self.candidate_id,
            "candidate_kind": self.candidate_kind,
            "entity_type": self.entity_type,
            "candidate_summary": self.candidate_summary,
            "candidate_payload": json.dumps(self.candidate_payload, sort_keys=True),
            "evidence_refs": json.dumps(
                [ref.model_dump() for ref in self.evidence_refs], sort_keys=True
            ),
            "adviser_verdict": self.adviser_verdict,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "adviser_abstained": self.adviser_abstained,
            "adviser_type": self.adviser_type,
            "adviser_version": self.adviser_version,
            "adviser_model_id": self.adviser_model_id,
            "adviser_model_version": self.adviser_model_version,
            "prompt_version": self.prompt_version,
            "source": self.source,
            "agent_name": self.agent_name,
            "agent_output_id": self.agent_output_id,
            "status": self.status,
            "committable": self.committable,
            "commit_blocker": self.commit_blocker,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
            "decision_note": self.decision_note,
            "committed_epoch": self.committed_epoch,
            "committed_identity_ids": json.dumps(self.committed_identity_ids),
        }

    @classmethod
    def from_neo4j_properties(cls, props: Mapping[str, Any]) -> "ReviewItem":
        """Rebuild an item from properties written by ``to_neo4j_properties``."""
        from agentic_kg.knowledge_graph.repository import decode_json_field

        payload = decode_json_field(props.get("candidate_payload"), {}) or {}
        refs = decode_json_field(props.get("evidence_refs"), []) or []
        identity_ids = decode_json_field(props.get("committed_identity_ids"), []) or []
        return cls(
            id=props["id"],
            uid=props["uid"],
            run_id=props["run_id"],
            namespace=props.get("ns") or "canon",
            candidate_id=props["candidate_id"],
            candidate_kind=props.get("candidate_kind") or "",
            entity_type=props.get("entity_type"),
            candidate_summary=props.get("candidate_summary") or "",
            candidate_payload=dict(payload),
            evidence_refs=[ReviewEvidenceRef(**ref) for ref in refs],
            adviser_verdict=props.get("adviser_verdict") or VERDICT_REJECT,
            confidence=props.get("confidence"),
            rationale=props.get("rationale") or "",
            adviser_abstained=bool(props.get("adviser_abstained", False)),
            adviser_type=props.get("adviser_type") or "",
            adviser_version=props.get("adviser_version") or "",
            adviser_model_id=props.get("adviser_model_id") or "",
            adviser_model_version=props.get("adviser_model_version") or "",
            prompt_version=props.get("prompt_version") or "",
            source=props.get("source") or "adviser",
            agent_name=props.get("agent_name") or "",
            agent_output_id=props.get("agent_output_id") or "",
            status=props.get("status") or "pending",
            committable=bool(props.get("committable", True)),
            commit_blocker=props.get("commit_blocker"),
            created_at=props.get("created_at") or "",
            updated_at=props.get("updated_at"),
            decided_by=props.get("decided_by"),
            decided_at=props.get("decided_at"),
            decision_note=props.get("decision_note"),
            committed_epoch=props.get("committed_epoch"),
            committed_identity_ids=list(identity_ids),
        )


class ReviewDecisionRecord(BaseModel):
    """An append-only human decision record."""

    uid: str
    review_uid: str
    namespace: str
    decision: ReviewDecision
    decided_by: str
    decided_at: str
    note: Optional[str] = None
    committed_epoch: Optional[int] = None
    committed_identity_ids: list[str] = Field(default_factory=list)

    def to_neo4j_properties(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "review_uid": self.review_uid,
            "ns": self.namespace,
            "decision": self.decision,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
            "note": self.note,
            "committed_epoch": self.committed_epoch,
            "committed_identity_ids": json.dumps(self.committed_identity_ids),
        }


__all__ = [
    "ReviewAgreement",
    "ReviewDecision",
    "ReviewDecisionRecord",
    "ReviewEvidenceRef",
    "ReviewItem",
    "ReviewItemStatus",
    "VERDICT_ADMIT",
    "VERDICT_MERGE_PREFIX",
    "VERDICT_REJECT",
    "is_merge_verdict",
    "review_item_id",
    "review_item_uid",
]
