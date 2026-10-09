"""The ``PipelineRun`` report: model, JSON writer and Neo4j node writer.

The wire shape is fixed by ``docs/design/nightly-pipeline-contract.md``. The
report is written two ways from the same object:

* a JSON file under ``<runs_dir>/nightly/<run_id>.json`` — in the Job this
  ``runs_dir`` is a Cloud Run GCS volume mount, so the object lands at
  ``gs://<runs_bucket>/nightly/<run_id>.json`` with no GCS client dependency;
* a ``(:PipelineRun {run_id})`` node in Neo4j, so the API can serve runs
  without reading GCS.

Neo4j has no nested property maps, so the nested fields (``queries``,
``totals``, ``deferral_reasons``, ``budget``, ``proposed_queries``,
``failures``) are stored as JSON strings. ``None`` values are omitted rather
than written as nulls; the reader treats an absent property as null.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

#: Neo4j label. Deliberately NOT ``Canon__*`` (ADR-0005 isolation).
PIPELINE_RUN_LABEL = "PipelineRun"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    """ISO-8601 UTC with a trailing ``Z`` (the contract's format)."""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def new_run_id(dt: datetime) -> str:
    """``nightly-YYYYMMDDTHHMMSSZ`` from a UTC datetime."""
    return "nightly-" + dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class QueryEntry(BaseModel):
    """One planned query's outcome. Exactly the contract's per-query fields."""

    model_config = ConfigDict(extra="forbid")

    query_id: str
    query: str
    topic: str
    limit: int
    papers_seen: int = 0
    papers_new: int = 0
    status: str = "pending"
    error: Optional[str] = None


class Totals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    papers_seen: int = 0
    papers_new: int = 0
    committed_operations: int = 0
    deferred_candidates: int = 0
    honest_nulls: dict[str, str] = Field(default_factory=dict)


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_papers: int
    max_llm_usd: float
    est_llm_usd: float = 0.0
    stopped_by_budget: bool = False


class Failure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: str
    message: str
    log_url: Optional[str] = None


class PipelineRunReport(BaseModel):
    """A full ``PipelineRun``. Field names are the contract."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    started_at: str
    finished_at: Optional[str] = None
    status: str = "running"
    trigger: str = "schedule"
    namespace: str = "staging"
    queries: list[QueryEntry] = Field(default_factory=list)
    totals: Totals = Field(default_factory=Totals)
    deferral_reasons: dict[str, int] = Field(default_factory=dict)
    budget: Budget
    review_queue_size: Optional[int] = None
    proposed_queries: list = Field(default_factory=list)
    failures: list[Failure] = Field(default_factory=list)
    git_sha: str = "unknown"
    image: str = "unknown"

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def report_json(report: PipelineRunReport) -> str:
    return json.dumps(report.to_json_dict(), indent=2, sort_keys=False)


def write_report_json(
    report: PipelineRunReport,
    *,
    runs_dir: str | Path,
    runs_bucket: str | None = None,
) -> str:
    """Write ``nightly/<run_id>.json`` under ``runs_dir``; return its URL.

    When ``runs_bucket`` is given the URL is the ``gs://`` object URL; otherwise
    the local path is returned (used by tests and dry runs).
    """
    target_dir = Path(runs_dir) / "nightly"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{report.run_id}.json"
    target.write_text(report_json(report) + "\n", encoding="utf-8")
    if runs_bucket:
        return f"gs://{runs_bucket}/nightly/{report.run_id}.json"
    return str(target)


def report_to_neo4j_props(report: PipelineRunReport) -> dict[str, Any]:
    """Flatten a report into Neo4j-safe properties (nested -> JSON strings)."""
    data = report.to_json_dict()
    props: dict[str, Any] = {}
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            props[key] = json.dumps(value, sort_keys=True)
        else:
            props[key] = value
    return props


def write_pipeline_run_node(report: PipelineRunReport, *, repo: Any = None) -> None:
    """``MERGE`` the ``(:PipelineRun {run_id})`` node.

    ``repo`` is injectable for tests; production passes nothing and the shared
    repository is used. A ``MERGE`` (not ``CREATE``) keeps a re-run of the same
    ``run_id`` idempotent.
    """
    if repo is None:
        from agentic_kg.knowledge_graph.repository import get_repository

        repo = get_repository()
    props = report_to_neo4j_props(report)
    with repo.session() as session:
        session.run(
            f"MERGE (r:{PIPELINE_RUN_LABEL} {{run_id: $run_id}}) SET r = $props",
            run_id=report.run_id,
            props=props,
        )
    logger.info("PipelineRun node written: run_id=%s", report.run_id)


__all__ = [
    "PIPELINE_RUN_LABEL",
    "Budget",
    "Failure",
    "PipelineRunReport",
    "QueryEntry",
    "Totals",
    "iso",
    "new_run_id",
    "report_json",
    "report_to_neo4j_props",
    "utc_now",
    "write_pipeline_run_node",
    "write_report_json",
]
