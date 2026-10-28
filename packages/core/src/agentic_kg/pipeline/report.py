"""The ``PipelineRun`` report: helpers and the JSON (GCS) writer.

The wire shape is fixed by ``docs/design/nightly-pipeline-contract.md`` and is
**one shared model**: ``agentic_kg.knowledge_graph.models.pipeline_run.PipelineRun``
(the API contract, landed in the Runs-page work). The nightly job builds that
same object and persists it two ways:

* a JSON file under ``<runs_dir>/nightly/<run_id>.json`` — in the Job this
  ``runs_dir`` is a Cloud Run GCS volume mount, so the object lands at
  ``gs://<runs_bucket>/nightly/<run_id>.json`` with no GCS client dependency;
* a ``(:PipelineRun {run_id})`` node in Neo4j via the shared
  ``agentic_kg.knowledge_graph.pipeline_runs.save_pipeline_run`` — so the API
  serves the exact same shape the job wrote, with no second writer to drift.

This module keeps only what is specific to the nightly job: run-id/time
helpers and the JSON writer. The model and the Neo4j storage shape live in the
shared core modules.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_kg.knowledge_graph.models.pipeline_run import PipelineRun


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    """ISO-8601 UTC with a trailing ``Z`` (the contract's format)."""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def new_run_id(dt: datetime) -> str:
    """``nightly-YYYYMMDDTHHMMSSZ`` from a UTC datetime."""
    return "nightly-" + dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def run_page_url(run_id: str, ui_base_url: str | None) -> str | None:
    """``<UI base>/runs/<run_id>``; ``None`` when no UI base is configured."""
    if not ui_base_url:
        return None
    return f"{ui_base_url.rstrip('/')}/runs/{run_id}"


def run_event(
    run: PipelineRun,
    *,
    report_url: str | None,
    run_url: str | None,
) -> dict[str, Any]:
    """The single structured log payload for one finished nightly run.

    Cloud Logging parses a bare JSON object written to stdout as
    ``jsonPayload``. The flat summary fields (``papers_seen`` etc.) exist so
    the Terraform log-based alert can extract them as labels; ``totals`` keeps
    the report's own shape for anyone reading the log directly.
    """
    failed = run.status == "failed"
    return {
        "event": "pipeline_run_failed" if failed else "pipeline_run_completed",
        "severity": "ERROR" if failed else "INFO",
        "run_id": run.run_id,
        "status": run.status,
        "trigger": run.trigger,
        "namespace": run.namespace,
        "finished_at": run.finished_at,
        "totals": run.totals.model_dump(),
        "papers_seen": run.totals.papers_seen,
        "papers_new": run.totals.papers_new,
        "committed_operations": run.totals.committed_operations,
        "deferred_candidates": run.totals.deferred_candidates,
        "review_queue_size": run.review_queue_size,
        "llm_usd": run.budget.est_llm_usd,
        "failure_count": len(run.failures),
        "run_url": run_url,
        "url": report_url,
    }


def emit_run_event(event: dict[str, Any]) -> None:
    """Write one event as a single bare JSON line on stdout.

    A logging prefix (``2026-... - agentic_kg... - INFO - {...}``) makes Cloud
    Logging store the line as ``textPayload``; a bare JSON object is stored as
    ``jsonPayload`` so a log-based alert can match ``jsonPayload.event``.
    """
    print(json.dumps(event), flush=True)


def report_json(run: PipelineRun) -> str:
    """Serialise a run to the contract's JSON form."""
    return json.dumps(run.model_dump(mode="json"), indent=2, sort_keys=False)


def write_report_json(
    run: PipelineRun,
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
    target = target_dir / f"{run.run_id}.json"
    target.write_text(report_json(run) + "\n", encoding="utf-8")
    if runs_bucket:
        return f"gs://{runs_bucket}/nightly/{run.run_id}.json"
    return str(target)


__all__ = [
    "emit_run_event",
    "iso",
    "new_run_id",
    "report_json",
    "run_event",
    "run_page_url",
    "utc_now",
    "write_report_json",
]
