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

from agentic_kg.knowledge_graph.models.pipeline_run import PipelineRun


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    """ISO-8601 UTC with a trailing ``Z`` (the contract's format)."""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def new_run_id(dt: datetime) -> str:
    """``nightly-YYYYMMDDTHHMMSSZ`` from a UTC datetime."""
    return "nightly-" + dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def report_json(run: PipelineRun) -> str:
    """Serialise a run to the contract's JSON form."""
    return json.dumps(run.model_dump(mode="json"), indent=2, sort_keys=False)


def write_latest_run_id(run_id: str, *, runs_dir: str | Path) -> Path:
    """Write the ``.latest_run_id`` marker the adviser Job reads.

    Cloud Workflows does not parse a Job's stdout, so the run id cannot be
    handed from the ingest Job to the adviser Job through the workflow. Both
    Jobs mount the same runs bucket; this marker is the handoff. It is written
    in the same ``nightly/`` directory as the report and is deliberately not a
    ``.json`` file, so nothing lists it as a run.
    """
    target_dir = Path(runs_dir) / "nightly"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / ".latest_run_id"
    target.write_text(run_id + "\n", encoding="utf-8")
    return target


def write_report_json(
    run: PipelineRun,
    *,
    runs_dir: str | Path,
    runs_bucket: str | None = None,
    kind: str = "nightly",
) -> str:
    """Write ``<kind>/<run_id>.json`` under ``runs_dir``; return its URL.

    ``kind`` defaults to ``nightly`` (the nightly job's contract). The research
    cycle reuses the same writer with ``kind="research-cycles"`` so a cycle
    report is the same object shape in a different folder.

    When ``runs_bucket`` is given the URL is the ``gs://`` object URL; otherwise
    the local path is returned (used by tests and dry runs).
    """
    target_dir = Path(runs_dir) / kind
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{run.run_id}.json"
    target.write_text(report_json(run) + "\n", encoding="utf-8")
    if runs_bucket:
        return f"gs://{runs_bucket}/{kind}/{run.run_id}.json"
    return str(target)


__all__ = [
    "iso",
    "new_run_id",
    "report_json",
    "utc_now",
    "write_latest_run_id",
    "write_report_json",
]
