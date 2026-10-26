"""Persistent research-progression cycles.

A ``ResearchCycle`` is app-namespace workflow state, not canonical graph
content: it lives in the same Neo4j database as the canonical graph, carries the
``Canon__`` prefix and an ``ns`` namespace, but it is never an
``Identity``/``Assertion`` and no canonical reader selects it (ADR-0004/0005).

One cycle records everything the closed loop did in one scheduling tick:

* the ranked candidates it selected (``selected_problems``),
* the workflows it started, their workflow id, problem, current state and the
  gate they are waiting at,
* every gate decision (who, when, note, edit payload),
* the budget it was allowed and whether a budget stopped it, and
* the terminal outcome.

A gated workflow can sit for days. The cycle record is the durable index that
lets a later process find it again; the LangGraph state itself lives in the
durable checkpoint store (``agentic_kg.agents.checkpoint_store``).

Storage shape (Neo4j)
---------------------
Nested fields are JSON strings, matching ``PipelineRun`` and ``ReviewItem``:

======================  ======================================================
property                shape
======================  ======================================================
``uid``                 ``"<ns>\\x1f<cycle_id>"`` (MERGE key)
``cycle_id``            str
``ns``                  canonical namespace
``status``              running | awaiting_gate | completed | failed | ...
``started_at``          ISO-8601 UTC str
``finished_at``         ISO-8601 UTC str, null while in flight
``trigger``             schedule | manual | weekly
``policy_version``      str (the versioned autonomy policy used)
``selected_problems``   JSON str -> list[str]
``workflow_runs``       JSON str -> list[dict]
``budget``              JSON str -> dict
``git_sha``             str
``image``               str
``note``                str | null
======================  ======================================================
"""

from __future__ import annotations

import json
from typing import Any, Literal, Mapping, Optional

from pydantic import BaseModel, Field

from agentic_kg.knowledge_graph.models.review_item import UID_SEP

#: Lifecycle of one scheduling tick.
CycleStatus = Literal[
    "running",
    "awaiting_gate",
    "completed",
    "partial",
    "failed",
    "stopped_by_budget",
    "skipped",
]

#: How the cycle was triggered.
CycleTrigger = Literal["schedule", "manual", "weekly"]

#: The three human gates the workflow exposes.
GateName = Literal["select_problem", "approve_proposal", "review_evaluation"]

#: A per-workflow lifecycle state, derived from the workflow state.
CycleWorkflowState = Literal[
    "pending",
    "awaiting_gate",
    "running",
    "completed",
    "rejected",
    "failed",
    "cancelled",
]


def research_cycle_uid(namespace: str, cycle_id: str) -> str:
    """The synthetic primary key for one cycle."""
    return f"{namespace}{UID_SEP}{cycle_id}"


class CycleBudget(BaseModel):
    """The budget one cycle was allowed, and what it consumed."""

    max_llm_usd: float = 0.0
    est_llm_usd: float = 0.0
    max_experiments: int = 0
    experiments: int = 0
    max_wall_seconds: int = 0
    wall_seconds: float = 0.0
    stopped_by_budget: bool = False


class GateDecision(BaseModel):
    """One human (or policy) decision at a workflow gate.

    ``decided_by`` is the human id for a human gate, or
    ``policy:<mode>`` when an autonomy policy resolved it. The note and edit
    payload are kept verbatim so the audit trail says what the human saw and
    what they changed.
    """

    gate: GateName
    decision: Literal["approve", "reject", "edit", "auto_approve", "auto_reject"]
    decided_by: str
    decided_at: str
    note: str = ""
    edited_data: Optional[dict[str, Any]] = None


class CycleWorkflow(BaseModel):
    """One workflow started by a cycle."""

    workflow_run_id: str
    problem_id: str
    problem_statement: str = ""
    state: CycleWorkflowState = "pending"
    current_step: str = ""
    pending_gate: Optional[GateName] = None
    started_at: str = ""
    updated_at: str = ""
    finished_at: Optional[str] = None
    gate_decisions: list[GateDecision] = Field(default_factory=list)
    error: Optional[str] = None

    def gate(self, name: str) -> Optional[GateDecision]:
        """The last decision recorded for ``name``, if any."""
        for decision in reversed(self.gate_decisions):
            if decision.gate == name:
                return decision
        return None


class ResearchCycle(BaseModel):
    """The durable record of one closed-loop scheduling tick."""

    cycle_id: str
    namespace: str
    started_at: str
    finished_at: Optional[str] = None
    status: CycleStatus = "running"
    trigger: CycleTrigger = "schedule"
    policy_version: str = ""
    selected_problems: list[str] = Field(default_factory=list)
    workflow_runs: list[CycleWorkflow] = Field(default_factory=list)
    budget: CycleBudget = Field(default_factory=CycleBudget)
    git_sha: str = ""
    image: str = ""
    note: Optional[str] = None

    @property
    def uid(self) -> str:
        return research_cycle_uid(self.namespace, self.cycle_id)

    def workflow(self, workflow_run_id: str) -> Optional[CycleWorkflow]:
        for item in self.workflow_runs:
            if item.workflow_run_id == workflow_run_id:
                return item
        return None

    def pending_gates(self) -> list[CycleWorkflow]:
        """Workflows currently waiting at a gate, oldest first."""
        return [
            item
            for item in self.workflow_runs
            if item.state == "awaiting_gate" and item.pending_gate is not None
        ]

    def is_active(self) -> bool:
        """Whether the cycle is executing right now.

        ``awaiting_gate`` is deliberately **not** active: a workflow can wait
        for a human for days, and the next scheduled cycle must still be able
        to re-rank the graph. Only ``running`` blocks a new cycle.
        """
        return self.status == "running"

    def to_neo4j_properties(self) -> dict[str, Any]:
        """Flatten the cycle into Neo4j-settable scalar properties."""
        return {
            "uid": self.uid,
            "cycle_id": self.cycle_id,
            "ns": self.namespace,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "trigger": self.trigger,
            "policy_version": self.policy_version,
            "selected_problems": json.dumps(self.selected_problems),
            "workflow_runs": json.dumps(
                [w.model_dump(mode="json") for w in self.workflow_runs]
            ),
            "budget": json.dumps(self.budget.model_dump(mode="json")),
            "git_sha": self.git_sha,
            "image": self.image,
            "note": self.note,
        }

    @classmethod
    def from_neo4j_properties(cls, props: Mapping[str, Any]) -> "ResearchCycle":
        """Rebuild a cycle from properties written by ``to_neo4j_properties``."""
        from agentic_kg.knowledge_graph.repository import decode_json_field

        selected = decode_json_field(props.get("selected_problems"), []) or []
        workflows = decode_json_field(props.get("workflow_runs"), []) or []
        budget = decode_json_field(props.get("budget"), {}) or {}
        return cls(
            cycle_id=props["cycle_id"],
            namespace=props.get("ns") or "canon",
            started_at=props.get("started_at") or "",
            finished_at=props.get("finished_at"),
            status=props.get("status") or "running",
            trigger=props.get("trigger") or "schedule",
            policy_version=props.get("policy_version") or "",
            selected_problems=list(selected),
            workflow_runs=[CycleWorkflow(**item) for item in workflows],
            budget=CycleBudget(**budget),
            git_sha=props.get("git_sha") or "",
            image=props.get("image") or "",
            note=props.get("note"),
        )


__all__ = [
    "CycleBudget",
    "CycleStatus",
    "CycleTrigger",
    "CycleWorkflow",
    "CycleWorkflowState",
    "GateDecision",
    "GateName",
    "ResearchCycle",
    "research_cycle_uid",
]
