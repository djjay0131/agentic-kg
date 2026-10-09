"""Unit tests for the nightly PipelineRun models and repository.

The real Neo4j round-trip lives in ``test_pipeline_runs_integration.py``
(testcontainers, Docker required). These tests are Docker-free: they pin the
storage shape (nested fields as JSON strings), the model round-trip, cursor
encoding, and the repository wiring against a mocked session.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from agentic_kg.knowledge_graph.models.pipeline_run import (
    PipelineBudget,
    PipelineFailure,
    PipelineQueryResult,
    PipelineRun,
    PipelineTotals,
)
from agentic_kg.knowledge_graph.pipeline_runs import (
    InvalidCursorError,
    PipelineRunNotFoundError,
    PipelineRunRepository,
    _decode_cursor,
    _encode_cursor,
    get_pipeline_run,
    list_pipeline_runs,
    save_pipeline_run,
)


def make_run(run_id: str = "nightly-20260101T023000Z", **overrides) -> PipelineRun:
    data = {
        "run_id": run_id,
        "started_at": "2026-01-01T02:30:00Z",
        "finished_at": "2026-01-01T02:41:00Z",
        "status": "succeeded",
        "trigger": "schedule",
        "namespace": "staging",
        "queries": [
            PipelineQueryResult(
                query_id="q1",
                query="transformer efficiency",
                topic="NLP",
                limit=50,
                papers_seen=120,
                papers_new=7,
                status="succeeded",
            )
        ],
        "totals": PipelineTotals(
            papers_seen=120,
            papers_new=7,
            committed_operations=5,
            deferred_candidates=2,
            honest_nulls=1,
        ),
        "deferral_reasons": {"low_confidence": 2},
        "budget": PipelineBudget(
            max_papers=500,
            max_llm_usd=5.0,
            est_llm_usd=1.25,
            stopped_by_budget=True,
        ),
        "review_queue_size": None,
        "proposed_queries": [],
        "failures": [PipelineFailure(step="cites", message="rate limited", log_url=None)],
        "git_sha": "abc123",
        "image": "gcr.io/vt-gcp-00042/agentic-kg:1",
    }
    data.update(overrides)
    return PipelineRun(**data)


def make_fake_repo(read_records=None):
    """A MagicMock repository whose session drives the real closures.

    ``execute_read``/``execute_write`` invoke the unit of work with a mock
    transaction, so the repository's own code (query selection, prop building,
    pagination) runs, but no database is touched.
    """
    repo = MagicMock()
    session = MagicMock()
    repo.session.return_value.__enter__.return_value = session
    tx = MagicMock()

    if read_records is not None:
        result = MagicMock()
        result.__iter__ = MagicMock(return_value=iter(read_records))
        result.single = MagicMock(
            return_value=read_records[0] if read_records else None
        )
        tx.run.return_value = result

    session.execute_read.side_effect = lambda work: work(tx)
    session.execute_write.side_effect = lambda work: work(tx)
    return repo, session, tx


# =============================================================================
# Model / storage shape
# =============================================================================


class TestPipelineRunModel:
    def test_defaults_for_a_running_run(self):
        run = PipelineRun(run_id="nightly-1", started_at="2026-01-01T02:30:00Z")
        assert run.status == "running"
        assert run.finished_at is None
        assert run.review_queue_size is None
        assert run.proposed_queries == []
        assert run.failures == []
        assert run.totals == PipelineTotals()
        assert run.budget.stopped_by_budget is False

    def test_rejects_unknown_status(self):
        with pytest.raises(ValueError):
            PipelineRun(
                run_id="nightly-1",
                started_at="2026-01-01T02:30:00Z",
                status="finished",  # type: ignore[arg-type]
            )

    def test_rejects_unknown_trigger(self):
        with pytest.raises(ValueError):
            PipelineRun(
                run_id="nightly-1",
                started_at="2026-01-01T02:30:00Z",
                trigger="cron",  # type: ignore[arg-type]
            )

    def test_to_neo4j_properties_json_encodes_nested_fields(self):
        props = make_run().to_neo4j_properties()
        # Scalars stay scalar.
        assert props["run_id"] == "nightly-20260101T023000Z"
        assert props["status"] == "succeeded"
        assert props["review_queue_size"] is None
        # Nested structures are JSON strings (Neo4j can't store nested maps).
        for key in (
            "queries",
            "totals",
            "deferral_reasons",
            "budget",
            "proposed_queries",
            "failures",
        ):
            assert isinstance(props[key], str), key

    def test_properties_round_trip_preserves_every_field(self):
        run = make_run()
        restored = PipelineRun.from_neo4j_properties(run.to_neo4j_properties())
        assert restored == run

    def test_from_properties_tolerates_missing_optional_fields(self):
        restored = PipelineRun.from_neo4j_properties(
            {"run_id": "nightly-1", "started_at": "2026-01-01T02:30:00Z"}
        )
        assert restored.finished_at is None
        assert restored.review_queue_size is None
        assert restored.queries == []
        assert restored.status == "running"

    def test_from_properties_tolerates_pre_decoded_nested(self):
        props = make_run().to_neo4j_properties()
        # A node written by a serializer that stored real maps (not JSON
        # strings) must still decode.
        props["totals"] = {"papers_seen": 3, "papers_new": 1}
        restored = PipelineRun.from_neo4j_properties(props)
        assert restored.totals.papers_seen == 3
        assert restored.totals.papers_new == 1


# =============================================================================
# Cursor
# =============================================================================


class TestCursor:
    def test_cursor_round_trips(self):
        run = make_run()
        started_at, run_id = _decode_cursor(_encode_cursor(run))
        assert (started_at, run_id) == (run.started_at, run.run_id)

    def test_malformed_cursor_raises(self):
        with pytest.raises(InvalidCursorError):
            _decode_cursor("not-a-cursor")

    def test_cursor_from_other_json_shape_raises(self):
        import base64

        token = base64.urlsafe_b64encode(b'{"nope": 1}').decode("ascii")
        with pytest.raises(InvalidCursorError):
            _decode_cursor(token)


# =============================================================================
# Repository (mocked session)
# =============================================================================


class TestPipelineRunRepositorySave:
    def test_save_merges_by_run_id(self):
        repo, _session, tx = make_fake_repo()
        run = make_run()
        result = PipelineRunRepository(repo).save(run)

        assert result is run
        query, kwargs = tx.run.call_args
        assert "MERGE (r:PipelineRun {run_id: $run_id})" in query[0]
        assert kwargs["run_id"] == run.run_id
        assert kwargs["props"]["status"] == "succeeded"

    def test_module_level_save_uses_passed_repository(self):
        repo, _session, tx = make_fake_repo()
        save_pipeline_run(make_run(), repository=repo)
        assert tx.run.called


class TestPipelineRunRepositoryGet:
    def test_get_decodes_a_stored_run(self):
        props = make_run().to_neo4j_properties()
        repo, _session, _tx = make_fake_repo(read_records=[{"r": props}])
        run = PipelineRunRepository(repo).get("nightly-20260101T023000Z")
        assert run == make_run()

    def test_get_missing_raises_not_found(self):
        repo, _session, _tx = make_fake_repo(read_records=[])
        with pytest.raises(PipelineRunNotFoundError):
            PipelineRunRepository(repo).get("nightly-missing")

    def test_module_level_get_raises_not_found(self):
        repo, _session, _tx = make_fake_repo(read_records=[])
        with pytest.raises(PipelineRunNotFoundError):
            get_pipeline_run("nightly-missing", repository=repo)


class TestPipelineRunRepositoryList:
    def test_empty_graph_returns_no_runs_and_no_cursor(self):
        repo, _session, _tx = make_fake_repo(read_records=[])
        runs, cursor = PipelineRunRepository(repo).list()
        assert runs == []
        assert cursor is None

    def test_list_returns_runs_without_cursor_when_page_not_full(self):
        props = make_run().to_neo4j_properties()
        repo, _session, _tx = make_fake_repo(read_records=[{"r": props}])
        runs, cursor = PipelineRunRepository(repo).list(limit=5)
        assert [r.run_id for r in runs] == [props["run_id"]]
        assert cursor is None

    def test_full_page_yields_cursor_and_drops_extra_row(self):
        runs = [
            make_run("nightly-3", started_at="2026-01-03T02:30:00Z"),
            make_run("nightly-2", started_at="2026-01-02T02:30:00Z"),
        ]
        records = [{"r": r.to_neo4j_properties()} for r in runs]
        repo, _session, tx = make_fake_repo(read_records=records)

        page, cursor = PipelineRunRepository(repo).list(limit=1)

        assert [r.run_id for r in page] == ["nightly-3"]
        assert cursor is not None
        assert _decode_cursor(cursor) == ("2026-01-03T02:30:00Z", "nightly-3")
        # Fetches limit + 1 to detect the next page.
        assert tx.run.call_args.kwargs["limit"] == 2

    def test_cursor_is_applied_to_the_query(self):
        repo, _session, tx = make_fake_repo(read_records=[])
        cursor = _encode_cursor(make_run("nightly-9"))
        PipelineRunRepository(repo).list(cursor=cursor)
        kwargs = tx.run.call_args.kwargs
        assert kwargs["cursor_started_at"] == "2026-01-01T02:30:00Z"
        assert kwargs["cursor_run_id"] == "nightly-9"

    def test_invalid_cursor_propagates(self):
        repo, _session, _tx = make_fake_repo(read_records=[])
        with pytest.raises(InvalidCursorError):
            PipelineRunRepository(repo).list(cursor="garbage")

    def test_module_level_list_uses_passed_repository(self):
        repo, _session, _tx = make_fake_repo(read_records=[])
        runs, cursor = list_pipeline_runs(repository=repo)
        assert runs == []
        assert cursor is None
