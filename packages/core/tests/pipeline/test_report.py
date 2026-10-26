"""The nightly JSON report writer (serialises the shared core PipelineRun)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from agentic_kg.knowledge_graph.models.pipeline_run import (
    PipelineBudget,
    PipelineQueryResult,
    PipelineRun,
    PipelineTotals,
)
from agentic_kg.pipeline.report import (
    new_run_id,
    report_json,
    write_report_json,
)


def _report(**overrides) -> PipelineRun:
    base = dict(
        run_id="nightly-20261009T023000Z",
        started_at="2026-10-09T02:30:00Z",
        finished_at="2026-10-09T02:35:00Z",
        status="succeeded",
        trigger="schedule",
        namespace="staging",
        queries=[
            PipelineQueryResult(
                query_id="a",
                query="q",
                topic="t",
                limit=5,
                papers_seen=5,
                papers_new=1,
                status="succeeded",
            )
        ],
        totals=PipelineTotals(papers_seen=5, papers_new=1, committed_operations=2),
        budget=PipelineBudget(max_papers=50, max_llm_usd=0.0),
        git_sha="abc123",
        image="job:latest",
    )
    base.update(overrides)
    return PipelineRun(**base)


def test_new_run_id_format():
    dt = datetime(2026, 10, 9, 2, 30, 0, tzinfo=timezone.utc)
    assert new_run_id(dt) == "nightly-20261009T023000Z"


def test_write_report_json_writes_nightly_object(tmp_path):
    report = _report()
    url = write_report_json(
        report,
        runs_dir=tmp_path,
        runs_bucket="vt-gcp-00042-agentic-kg-runs-staging",
    )
    assert url == (
        "gs://vt-gcp-00042-agentic-kg-runs-staging/"
        "nightly/nightly-20261009T023000Z.json"
    )
    written = json.loads(
        (tmp_path / "nightly" / "nightly-20261009T023000Z.json").read_text()
    )
    assert written["run_id"] == report.run_id
    assert set(written["queries"][0]) == {
        "query_id",
        "query",
        "topic",
        "limit",
        "papers_seen",
        "papers_new",
        "status",
        "error",
    }
    assert written["finished_at"] == "2026-10-09T02:35:00Z"


def test_write_report_json_without_bucket_returns_path(tmp_path):
    url = write_report_json(_report(), runs_dir=tmp_path)
    assert url.endswith("nightly-20261009T023000Z.json")
    assert not url.startswith("gs://")


def test_reported_json_is_the_shared_core_model():
    """The JSON the Job writes decodes straight back into the API's model."""
    report = _report()
    decoded = PipelineRun.model_validate_json(report_json(report))
    assert decoded == report
    assert decoded.totals.honest_nulls == report.totals.honest_nulls
