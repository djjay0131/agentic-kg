"""Typed provenance for agent outputs (KGPS ``ProvenanceEnvelope`` shape).

Every research-agent output carries the canonical assertions and evidence it
used, plus a ``derived_from`` link to the upstream agent output(s) that fed it.
The wire shape is KGPS's ``ProvenanceEnvelope`` (agentic-kgps v0.5.0): a
message between agent roles that cannot be sent without its evidence.

Why this module defines its own model instead of importing KGPS at module
scope: the default install must be importable without the opt-in ``migration``
extra (the same constraint ``migration.canonical_read`` documents). The shape
here is deliberately identical to KGPS's, and
:meth:`AgentProvenance.to_kgps_envelope` validates it against the real KGPS
model when the extra is present. A legacy-graph output has no canonical
assertion ids, so its envelope **abstains** with that reason rather than
pretending to be grounded.

Records are held in the runner's ``AgentOutputStore`` (in-memory in this
slice, the same lifetime as the runner's checkpoints), so ``GET
/api/agents/outputs/{id}/provenance`` can answer without a second write
surface. They are workflow state, never canonical ``Identity``/``Assertion``
content.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Protocol

from pydantic import BaseModel, Field

from agentic_kg.agents.graph_port import SOURCE_CANONICAL, SOURCE_LEGACY

#: ``sender`` / ``kind`` mappings. Each agent is a role in the PA-AKG chain;
#: the envelope kind names what kind of message it emitted.
AGENT_ENVELOPE: dict[str, tuple[str, str]] = {
    "ranking": ("retriever", "retrieval_result"),
    "continuation": ("generator", "answer"),
    "evaluation": ("validator", "validation_verdict"),
    "synthesis": ("curator", "curation_result"),
}

RECIPIENT = "supervisor"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class CitationRecord(BaseModel):
    """One assertion (optionally narrowed to evidence ids) an output relies on."""

    assertion_id: str
    evidence_ids: list[str] = Field(default_factory=list)


class AgentProvenance(BaseModel):
    """The provenance envelope stored with one agent output."""

    sender: str
    recipient: str = RECIPIENT
    kind: str
    trace_id: str
    parent_trace_id: str | None = None
    citations: list[CitationRecord] = Field(default_factory=list)
    abstained: bool = False
    rationale: str | None = None

    # Agent-output lineage (not part of KGPS's envelope; exposed alongside it).
    derived_from: list[str] = Field(default_factory=list)
    source: Literal["legacy", "canonical"] = SOURCE_LEGACY
    agent: str = ""
    model: str | None = None
    prompt_version: str = ""

    # Used-but-not-canonical inputs, kept so a legacy envelope is still honest
    # about what it read instead of saying "nothing".
    used_problem_ids: list[str] = Field(default_factory=list)
    used_evidence_ids: list[str] = Field(default_factory=list)

    def to_kgps_envelope(self) -> Any:
        """Validate and return the real KGPS ``ProvenanceEnvelope``.

        Requires the opt-in ``migration`` extra. A legacy or ungrounded output
        becomes an abstaining envelope, which is exactly what KGPS's model is
        for — it refuses silently ungrounded text.
        """
        from kgps.grounding import (
            AgentRole,
            Citation,
            CitedSentence,
            EnvelopeKind,
            GroundedAnswer,
            ProvenanceEnvelope,
        )

        kind = EnvelopeKind(self.kind)
        citations = tuple(
            Citation(
                assertion_id=c.assertion_id,
                evidence_ids=tuple(c.evidence_ids),
            )
            for c in self.citations
        )
        answer = None
        if kind is EnvelopeKind.ANSWER and citations:
            text = self.rationale or ""
            answer = GroundedAnswer(
                question="",
                text=text,
                sentences=(
                    CitedSentence(
                        text=text,
                        start=0,
                        end=len(text),
                        citations=citations,
                    ),
                ),
                produced_by=self.agent or "agent",
                model=self.model,
                trace_id=self.trace_id,
            )
        elif kind is EnvelopeKind.ANSWER:
            # An ANSWER envelope carries a GroundedAnswer; without citations
            # this output has to be a retrieval result that abstains.
            kind = EnvelopeKind.RETRIEVAL_RESULT
        return ProvenanceEnvelope(
            sender=AgentRole(self.sender),
            recipient=AgentRole(self.recipient),
            kind=kind,
            trace_id=self.trace_id,
            parent_trace_id=self.parent_trace_id,
            citations=citations,
            abstained=self.abstained,
            rationale=self.rationale,
            answer=answer,
        )


def build_provenance(
    *,
    agent: str,
    run_id: str,
    source: str,
    assertion_ids: list[str] | None = None,
    assertion_evidence: Mapping[str, list[str]] | None = None,
    evidence_ids: list[str] | None = None,
    derived_from: list[str] | None = None,
    parent_trace_id: str | None = None,
    model: str | None = None,
    prompt_version: str = "",
    rationale: str | None = None,
    problem_ids: list[str] | None = None,
    trace_id: str | None = None,
) -> AgentProvenance:
    """Build the envelope for one agent output.

    Citations come from assertion ids; when the graph supplies a per-assertion
    evidence map it is carried through, otherwise the assertion is cited with
    no narrowed evidence. An output with no canonical assertion ids abstains —
    the honest statement for a legacy read or an agent invention.
    """
    sender, kind = AGENT_ENVELOPE.get(agent, ("generator", "retrieval_result"))
    ids = [str(a) for a in (assertion_ids or []) if a]
    per_assertion = assertion_evidence or {}
    citations = [
        CitationRecord(
            assertion_id=aid,
            evidence_ids=[str(e) for e in per_assertion.get(aid, []) if e],
        )
        for aid in ids
    ]
    evidence = list(dict.fromkeys(str(e) for e in (evidence_ids or []) if e))
    abstained = not citations
    if abstained and rationale is None:
        rationale = (
            "legacy graph source: canonical assertion ids are not available"
            if source == SOURCE_LEGACY
            else "the agent output did not resolve to any canonical assertion"
        )
    return AgentProvenance(
        sender=sender,
        recipient=RECIPIENT,
        kind=kind,
        trace_id=trace_id or run_id,
        parent_trace_id=parent_trace_id,
        citations=citations,
        abstained=abstained,
        rationale=rationale,
        derived_from=[str(x) for x in (derived_from or []) if x],
        source=SOURCE_CANONICAL if source == SOURCE_CANONICAL else SOURCE_LEGACY,
        agent=agent,
        model=model,
        prompt_version=prompt_version,
        used_problem_ids=[str(p) for p in (problem_ids or []) if p],
        used_evidence_ids=evidence,
    )


class AgentOutput(BaseModel):
    """One persisted agent output and its provenance."""

    id: str
    run_id: str
    trace_id: str
    agent: str
    source: str
    model: str | None = None
    prompt_version: str = ""
    output_kind: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    provenance: AgentProvenance
    created_at: str = Field(default_factory=_now_iso)

    def to_neo4j_properties(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "agent": self.agent,
            "source": self.source,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "output_kind": self.output_kind,
            "payload": json.dumps(self.payload, sort_keys=True),
            "provenance": json.dumps(self.provenance.model_dump(mode="json"), sort_keys=True),
            "created_at": self.created_at,
        }

    @classmethod
    def from_neo4j_properties(cls, props: Mapping[str, Any]) -> "AgentOutput":
        return cls(
            id=props["id"],
            run_id=props.get("run_id") or "",
            trace_id=props.get("trace_id") or "",
            agent=props.get("agent") or "",
            source=props.get("source") or SOURCE_LEGACY,
            model=props.get("model"),
            prompt_version=props.get("prompt_version") or "",
            output_kind=props.get("output_kind") or "",
            payload=json.loads(props.get("payload") or "{}"),
            provenance=AgentProvenance.model_validate(json.loads(props.get("provenance") or "{}")),
            created_at=props.get("created_at") or "",
        )


class AgentOutputStore(Protocol):
    def save(self, output: AgentOutput) -> AgentOutput: ...

    def get(self, output_id: str) -> AgentOutput | None: ...


class InMemoryAgentOutputStore:
    """Offline store used by tests and by a runner with no database."""

    def __init__(self) -> None:
        self._outputs: dict[str, AgentOutput] = {}

    def save(self, output: AgentOutput) -> AgentOutput:
        self._outputs[output.id] = output
        return output

    def get(self, output_id: str) -> AgentOutput | None:
        return self._outputs.get(output_id)

    def list_for_run(self, run_id: str) -> list[AgentOutput]:
        return [o for o in self._outputs.values() if o.run_id == run_id]


def new_output_id() -> str:
    return f"agentout_{uuid.uuid4().hex[:24]}"


def record_agent_output(
    store: AgentOutputStore | None,
    *,
    agent: str,
    run_id: str,
    source: str,
    payload: dict[str, Any],
    provenance: AgentProvenance,
    output_kind: str = "",
    model: str | None = None,
    prompt_version: str = "",
    output_id: str | None = None,
) -> str:
    """Persist an agent output (if a store is wired) and return its id."""
    oid = output_id or new_output_id()
    if store is not None:
        store.save(
            AgentOutput(
                id=oid,
                run_id=run_id,
                trace_id=provenance.trace_id,
                agent=agent,
                source=source,
                model=model or provenance.model,
                prompt_version=prompt_version or provenance.prompt_version,
                output_kind=output_kind,
                payload=payload,
                provenance=provenance,
            )
        )
    return oid


__all__ = [
    "AGENT_ENVELOPE",
    "AgentOutput",
    "AgentOutputStore",
    "AgentProvenance",
    "CitationRecord",
    "InMemoryAgentOutputStore",
    "build_provenance",
    "new_output_id",
    "record_agent_output",
]
