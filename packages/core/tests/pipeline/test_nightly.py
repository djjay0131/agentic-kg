"""The nightly orchestrator: aggregation, budgets, statuses and persistence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agentic_kg.pipeline.catalog import QueryCatalog
from agentic_kg.pipeline.ingest import QueryOutcome
from agentic_kg.pipeline.nightly import main, run_nightly

SHIPPED_CATALOG = Path(__file__).resolve().parents[4] / "config" / "ingest-queries.yaml"

NOW = datetime(2026, 10, 9, 2, 30, 0, tzinfo=timezone.utc)


class FakeSession:
    def __init__(self, store):
        self._store = store

    def execute_write(self, work):
        return work(FakeTx(self._store))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeTx:
    def __init__(self, store):
        self._store = store

    def run(self, query, **params):
        self._store.append((query, params))


class FakeRepo:
    def __init__(self):
        self.calls = []

    def session(self):
        return FakeSession(self.calls)


def _catalog(*specs) -> QueryCatalog:
    return QueryCatalog(
        queries=[
            {
                "id": qid,
                "query": f"query {qid}",
                "topic": "topic",
                "limit": limit,
                "weight": 1.0,
                "enabled": True,
            }
            for qid, limit in specs
        ]
    )


def _runner(outcomes):
    def run(query, limit, *, run_id, namespace):
        return outcomes.get(query, QueryOutcome())

    return run


def test_success_path_aggregates_and_persists(tmp_path):
    catalog = _catalog(("a", 3), ("b", 4))
    outcomes = {
        "query a": QueryOutcome(
            papers_seen=3,
            papers_new=1,
            committed_operations=2,
            deferred_candidates=5,
            deferral_reasons={"unresolved_identity_no_entity_resolution": 2},
            honest_nulls={"entity_resolution": "not_wired"},
            est_llm_usd=0.25,
        ),
        "query b": QueryOutcome(
            papers_seen=4,
            papers_new=2,
            committed_operations=1,
            deferred_candidates=3,
            deferral_reasons={"unresolved_identity_no_entity_resolution": 1},
        ),
    }
    repo = FakeRepo()
    result = run_nightly(
        now=NOW,
        catalog=catalog,
        runner=_runner(outcomes),
        runs_dir=tmp_path,
        runs_bucket="bucket",
        repo=repo,
    )
    report = result.report
    assert result.exit_code == 0
    assert report.status == "succeeded"
    assert report.totals.papers_seen == 7
    assert report.totals.papers_new == 3
    assert report.totals.committed_operations == 3
    assert report.totals.deferred_candidates == 8
    assert report.totals.honest_nulls == 1  # one distinct reason across queries
    assert report.deferral_reasons == {"unresolved_identity_no_entity_resolution": 3}
    assert report.budget.est_llm_usd == 0.25
    assert not report.budget.stopped_by_budget
    assert (tmp_path / "nightly" / f"{report.run_id}.json").is_file()
    assert len(repo.calls) == 1


def test_partial_when_one_query_fails(tmp_path):
    catalog = _catalog(("a", 3), ("b", 3))
    outcomes = {
        "query a": QueryOutcome(papers_seen=3, papers_new=1),
        "query b": QueryOutcome(error="boom"),
    }
    result = run_nightly(
        now=NOW, catalog=catalog, runner=_runner(outcomes), runs_dir=tmp_path,
        write_neo4j=False,
    )
    assert result.exit_code == 1
    assert result.report.status == "partial"
    assert result.report.queries[1].status == "failed"
    assert result.report.queries[1].error == "boom"
    assert result.report.failures[0].step == "ingest:b"


def test_failed_when_every_query_fails(tmp_path):
    catalog = _catalog(("a", 3), ("b", 3))
    outcomes = {"query a": QueryOutcome(error="x"), "query b": QueryOutcome(error="y")}
    result = run_nightly(
        now=NOW, catalog=catalog, runner=_runner(outcomes), runs_dir=tmp_path,
        write_neo4j=False,
    )
    assert result.exit_code == 2
    assert result.report.status == "failed"


def test_planner_exception_yields_failed_report(tmp_path):
    catalog = _catalog(("a", 10))
    result = run_nightly(
        now=NOW, catalog=catalog, max_papers=5, runs_dir=tmp_path, write_neo4j=False,
    )
    assert result.exit_code == 2
    assert result.report.status == "failed"
    assert result.report.failures[0].step == "plan"


def test_budget_stops_the_run(tmp_path):
    catalog = _catalog(("a", 8), ("b", 8), ("c", 8))
    outcomes = {
        "query a": QueryOutcome(papers_seen=8, papers_new=8),
        "query b": QueryOutcome(papers_seen=8, papers_new=8),
        "query c": QueryOutcome(papers_seen=8, papers_new=8),
    }
    # Only two 8-limit queries fit in 20, so the planner leaves the third for a
    # later night: the budget bounded the plan, but tonight ran cleanly.
    result = run_nightly(
        now=NOW, catalog=catalog, max_papers=20, runner=_runner(outcomes),
        runs_dir=tmp_path, write_neo4j=False,
    )
    assert result.report.budget.stopped_by_budget is True
    assert result.report.status == "succeeded"
    assert result.exit_code == 0
    assert len(result.report.queries) == 2  # planner never selected c
    assert all(q.status == "succeeded" for q in result.report.queries)


def test_usd_budget_stops_the_run(tmp_path):
    catalog = _catalog(("a", 3), ("b", 3))
    outcomes = {
        "query a": QueryOutcome(papers_seen=3, est_llm_usd=2.0),
        "query b": QueryOutcome(papers_seen=3, est_llm_usd=2.0),
    }
    result = run_nightly(
        now=NOW, catalog=catalog, max_llm_usd=1.0, runner=_runner(outcomes),
        runs_dir=tmp_path, write_neo4j=False,
    )
    assert result.report.budget.stopped_by_budget is True
    assert result.report.status == "partial"
    assert result.report.queries[1].status == "skipped"


def test_missing_runs_dir_is_a_partial(tmp_path):
    catalog = _catalog(("a", 3))
    result = run_nightly(
        now=NOW, catalog=catalog,
        runner=_runner({"query a": QueryOutcome(papers_seen=3)}),
        runs_dir=None, write_neo4j=False,
    )
    assert result.exit_code == 1
    assert result.report.status == "succeeded"  # ingest itself was fine
    assert result.url is None


def test_neo4j_write_failure_is_a_partial(tmp_path):
    class BoomRepo:
        def session(self):
            raise RuntimeError("no db")

    catalog = _catalog(("a", 3))
    result = run_nightly(
        now=NOW, catalog=catalog,
        runner=_runner({"query a": QueryOutcome(papers_seen=3)}),
        runs_dir=tmp_path, repo=BoomRepo(),
    )
    assert result.exit_code == 1
    assert len(result.report.queries) == 1


def test_cli_plan_only_does_not_ingest(capsys):
    code = main(["--plan-only", "--catalog", str(SHIPPED_CATALOG)])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)


def test_trigger_is_recorded(tmp_path):
    catalog = _catalog(("a", 3))
    result = run_nightly(
        now=NOW, catalog=catalog, trigger="manual",
        runner=_runner({"query a": QueryOutcome()}), runs_dir=tmp_path,
        write_neo4j=False,
    )
    assert result.report.trigger == "manual"


def _only_stdout_line(capsys) -> dict:
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 1
    return json.loads(lines[0])


def test_success_emits_one_structured_event(tmp_path, capsys):
    catalog = _catalog(("a", 3))
    outcomes = {
        "query a": QueryOutcome(
            papers_seen=3,
            papers_new=1,
            committed_operations=2,
            deferred_candidates=5,
            est_llm_usd=0.25,
        ),
    }
    result = run_nightly(
        now=NOW,
        catalog=catalog,
        runner=_runner(outcomes),
        runs_dir=tmp_path,
        runs_bucket="vt-gcp-00042-agentic-kg-runs-staging",
        ui_base_url="https://agentic-kg-ui-staging.example.com/",
        write_neo4j=False,
    )

    event = _only_stdout_line(capsys)

    assert event["event"] == "pipeline_run_completed"
    assert event["severity"] == "INFO"
    assert event["run_id"] == result.report.run_id
    assert event["status"] == "succeeded"
    assert event["trigger"] == "schedule"
    assert event["namespace"] == "staging"
    assert event["totals"] == {
        "papers_seen": 3,
        "papers_new": 1,
        "committed_operations": 2,
        "deferred_candidates": 5,
        "honest_nulls": 0,
    }
    assert event["papers_seen"] == 3
    assert event["papers_new"] == 1
    assert event["committed_operations"] == 2
    assert event["deferred_candidates"] == 5
    assert event["review_queue_size"] is None
    assert event["llm_usd"] == 0.25
    assert event["failure_count"] == 0
    assert event["run_url"] == (
        f"https://agentic-kg-ui-staging.example.com/runs/{result.report.run_id}"
    )
    assert event["url"] == (
        "gs://vt-gcp-00042-agentic-kg-runs-staging/"
        f"nightly/{result.report.run_id}.json"
    )


def test_failure_emits_one_structured_event(tmp_path, capsys):
    catalog = _catalog(("a", 3), ("b", 3))
    outcomes = {"query a": QueryOutcome(error="x"), "query b": QueryOutcome(error="y")}
    result = run_nightly(
        now=NOW,
        catalog=catalog,
        runner=_runner(outcomes),
        runs_dir=tmp_path,
        ui_base_url="https://agentic-kg-ui-staging.example.com",
        write_neo4j=False,
    )

    event = _only_stdout_line(capsys)

    assert event["event"] == "pipeline_run_failed"
    assert event["severity"] == "ERROR"
    assert event["run_id"] == result.report.run_id
    assert event["status"] == "failed"
    assert event["failure_count"] == 2
    assert event["run_url"] == (
        f"https://agentic-kg-ui-staging.example.com/runs/{result.report.run_id}"
    )


def test_run_url_is_null_without_a_ui_base(tmp_path, capsys):
    catalog = _catalog(("a", 3))
    run_nightly(
        now=NOW,
        catalog=catalog,
        runner=_runner({"query a": QueryOutcome()}),
        runs_dir=tmp_path,
        write_neo4j=False,
    )

    assert _only_stdout_line(capsys)["run_url"] is None
