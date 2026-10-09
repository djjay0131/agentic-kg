"""The PipelineRun report model and its two writers."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from agentic_kg.pipeline.report import (
    Budget,
    Failure,
    PipelineRunReport,
    QueryEntry,
    Totals,
    new_run_id,
    report_to_neo4j_props,
    write_pipeline_run_node,
    write_report_json,
)


class FakeSession:
    def __init__(self, store):
        self._store = store

    def run(self, query, **params):
        self._store.append((query, params))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeRepo:
    def __init__(self):
        self.calls = []

    def session(self):
        return FakeSession(self.calls)


def _report(**overrides) -> PipelineRunReport:
    base = dict(
        run_id="nightly-20261009T023000Z",
        started_at="2026-10-09T02:30:00Z",
        finished_at="2026-10-09T02:35:00Z",
        status="succeeded",
        trigger="schedule",
        namespace="staging",
        queries=[
            QueryEntry(
                query_id="a",
                query="q",
                topic="t",
                limit=5,
                papers_seen=5,
                papers_new=1,
                status="succeeded",
            )
        ],
        totals=Totals(papers_seen=5, papers_new=1, committed_operations=2),
        budget=Budget(max_papers=50, max_llm_usd=0.0),
        git_sha="abc123",
        image="job:latest",
    )
    base.update(overrides)
    return PipelineRunReport(**base)


def test_new_run_id_format():
    dt = datetime(2026, 10, 9, 2, 30, 0, tzinfo=timezone.utc)
    assert new_run_id(dt) == "nightly-20261009T023000Z"


def test_extra_field_is_rejected():
    with pytest.raises(ValueError):
        _report(unexpected="x")


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


def test_neo4j_props_flatten_nested_and_omit_nulls():
    report = _report(
        finished_at=None,
        review_queue_size=None,
        failures=[Failure(step="s", message="m")],
    )
    props = report_to_neo4j_props(report)
    assert "finished_at" not in props
    assert "review_queue_size" not in props
    assert isinstance(props["queries"], str)
    assert isinstance(props["totals"], str)
    assert isinstance(props["failures"], str)
    assert props["run_id"] == report.run_id


def test_write_pipeline_run_node_merges_with_fake_repo():
    repo = FakeRepo()
    report = _report()
    write_pipeline_run_node(report, repo=repo)
    assert len(repo.calls) == 1
    query, params = repo.calls[0]
    assert "MERGE (r:PipelineRun {run_id: $run_id})" in query
    assert params["run_id"] == report.run_id
    assert params["props"]["status"] == "succeeded"
