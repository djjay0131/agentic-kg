"""Models for nightly PipelineRun reports.

See ``docs/design/nightly-pipeline-contract.md``. One ``PipelineRun`` is written
per nightly execution, both as a JSON report on GCS and as a ``(:PipelineRun)``
Neo4j node in the staging database (a label deliberately outside the
``Canon__*`` namespace, since it is operational telemetry, not canonical graph
content).

Storage shape (Neo4j)
---------------------
Neo4j properties cannot hold nested maps or lists of maps, so the structured
fields are stored as JSON strings on the node and decoded on read:

==================  ==========================================================
property            shape
==================  ==========================================================
``run_id``          str (also the ``MERGE`` key)
``started_at``      ISO-8601 UTC str
``finished_at``     ISO-8601 UTC str, absent/null while the run is in flight
``status``          str
``trigger``         str
``namespace``       str
``review_queue_size`` int, absent/null until the P2 review queue exists
``git_sha``         str
``image``           str
``queries``         JSON str -> ``list[dict]``
``totals``          JSON str -> ``dict``
``deferral_reasons`` JSON str -> ``dict[str, int]``
``budget``          JSON str -> ``dict``
``proposed_queries`` JSON str -> ``list``
``failures``        JSON str -> ``list[dict]``
==================  ==========================================================

Reading is tolerant: a value that is still a JSON string is decoded (via
``decode_json_field``), so a node written by an older serializer round-trips.
"""

from __future__ import annotations

import json
from typing import Any, Literal, Mapping, Optional

from pydantic import BaseModel, Field

#: ``PipelineRun.status`` -- the contract's closed set.
PipelineRunStatus = Literal["running", "succeeded", "partial", "failed"]

#: ``PipelineRun.trigger`` -- how the execution was started.
PipelineRunTrigger = Literal["schedule", "manual"]


class PipelineQueryResult(BaseModel):
    """Per-query outcome within a nightly run."""

    query_id: str
    query: str
    topic: str
    limit: int
    papers_seen: int = 0
    papers_new: int = 0
    status: str
    error: Optional[str] = None


class PipelineTotals(BaseModel):
    """Run-level totals across every query."""

    papers_seen: int = 0
    papers_new: int = 0
    committed_operations: int = 0
    deferred_candidates: int = 0
    honest_nulls: int = 0


class PipelineBudget(BaseModel):
    """The budget the run was allowed, and whether it hit the ceiling."""

    max_papers: int = 0
    max_llm_usd: float = 0.0
    est_llm_usd: float = 0.0
    stopped_by_budget: bool = False


class PipelineFailure(BaseModel):
    """A step that failed during the run, with an optional log link."""

    step: str
    message: str
    log_url: Optional[str] = None


class PipelineRun(BaseModel):
    """A full nightly PipelineRun record (the API's detail payload)."""

    run_id: str
    started_at: str
    finished_at: Optional[str] = None
    status: PipelineRunStatus = "running"
    trigger: PipelineRunTrigger = "schedule"
    namespace: str = "staging"
    queries: list[PipelineQueryResult] = Field(default_factory=list)
    totals: PipelineTotals = Field(default_factory=PipelineTotals)
    deferral_reasons: dict[str, int] = Field(default_factory=dict)
    budget: PipelineBudget = Field(default_factory=PipelineBudget)
    review_queue_size: Optional[int] = None
    proposed_queries: list[dict[str, Any]] = Field(default_factory=list)
    failures: list[PipelineFailure] = Field(default_factory=list)
    git_sha: str = ""
    image: str = ""

    def to_neo4j_properties(self) -> dict[str, Any]:
        """Flatten the run into Neo4j-settable properties.

        Nested structures are JSON-encoded (see the module docstring for the
        exact shape).
        """
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "trigger": self.trigger,
            "namespace": self.namespace,
            "review_queue_size": self.review_queue_size,
            "git_sha": self.git_sha,
            "image": self.image,
            "queries": json.dumps([q.model_dump() for q in self.queries]),
            "totals": json.dumps(self.totals.model_dump()),
            "deferral_reasons": json.dumps(self.deferral_reasons),
            "budget": json.dumps(self.budget.model_dump()),
            "proposed_queries": json.dumps(self.proposed_queries),
            "failures": json.dumps([f.model_dump() for f in self.failures]),
        }

    @classmethod
    def from_neo4j_properties(cls, props: Mapping[str, Any]) -> "PipelineRun":
        """Rebuild a run from node properties written by ``to_neo4j_properties``."""
        from agentic_kg.knowledge_graph.repository import decode_json_field

        queries = decode_json_field(props.get("queries"), []) or []
        proposed_queries = decode_json_field(props.get("proposed_queries"), []) or []
        failures = decode_json_field(props.get("failures"), []) or []
        totals = decode_json_field(props.get("totals"), {}) or {}
        budget = decode_json_field(props.get("budget"), {}) or {}
        deferral_reasons = decode_json_field(props.get("deferral_reasons"), {}) or {}

        return cls(
            run_id=props["run_id"],
            started_at=props.get("started_at") or "",
            finished_at=props.get("finished_at"),
            status=props.get("status") or "running",
            trigger=props.get("trigger") or "schedule",
            namespace=props.get("namespace") or "staging",
            queries=[PipelineQueryResult(**q) for q in queries],
            totals=PipelineTotals(**totals),
            deferral_reasons=dict(deferral_reasons),
            budget=PipelineBudget(**budget),
            review_queue_size=props.get("review_queue_size"),
            proposed_queries=list(proposed_queries),
            failures=[PipelineFailure(**f) for f in failures],
            git_sha=props.get("git_sha") or "",
            image=props.get("image") or "",
        )


__all__ = [
    "PipelineBudget",
    "PipelineFailure",
    "PipelineQueryResult",
    "PipelineRun",
    "PipelineRunStatus",
    "PipelineRunTrigger",
    "PipelineTotals",
]
