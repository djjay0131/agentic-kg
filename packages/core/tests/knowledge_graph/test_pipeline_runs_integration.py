"""Integration test (testcontainers): PipelineRun save/list/get round-trip.

Proves the storage contract end to end against a real Neo4j: nested fields
survive JSON property encoding, ``MERGE`` updates a run in place, listing is
newest-first, and the cursor paginates without loss. Runs only under the
``integration`` marker and skips cleanly without Docker (see
``tests/conftest.py``).
"""

from __future__ import annotations

import pytest
from agentic_kg.knowledge_graph.models.pipeline_run import (
    PipelineBudget,
    PipelineFailure,
    PipelineQueryResult,
    PipelineRun,
    PipelineTotals,
)
from agentic_kg.knowledge_graph.pipeline_runs import (
    PipelineRunNotFoundError,
    PipelineRunRepository,
)

pytestmark = pytest.mark.integration

_MARKER = "TEST_nightly-"


def _cleanup(repo) -> None:
    with repo.session() as session:
        session.run(
            "MATCH (r:PipelineRun) WHERE r.run_id STARTS WITH $marker DETACH DELETE r",
            marker=_MARKER,
        )


@pytest.fixture
def runs_repo(neo4j_repository):
    """A PipelineRunRepository on the owned container, cleaned before/after."""
    _cleanup(neo4j_repository)
    try:
        yield PipelineRunRepository(neo4j_repository)
    finally:
        _cleanup(neo4j_repository)


def make_run(run_id: str, started_at: str, **overrides) -> PipelineRun:
    data = {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": "2026-01-01T02:41:00Z",
        "status": "partial",
        "trigger": "schedule",
        "namespace": "staging",
        "queries": [
            PipelineQueryResult(
                query_id="q1",
                query="graph neural networks",
                topic="GNN",
                limit=25,
                papers_seen=40,
                papers_new=3,
                status="succeeded",
                error=None,
            ),
            PipelineQueryResult(
                query_id="q2",
                query="retrieval augmented generation",
                topic="RAG",
                limit=25,
                papers_seen=0,
                papers_new=0,
                status="failed",
                error="rate limited",
            ),
        ],
        "totals": PipelineTotals(
            papers_seen=40,
            papers_new=3,
            committed_operations=2,
            deferred_candidates=1,
            honest_nulls=1,
        ),
        "deferral_reasons": {"low_confidence": 1},
        "budget": PipelineBudget(
            max_papers=100,
            max_llm_usd=2.0,
            est_llm_usd=0.4,
            stopped_by_budget=False,
        ),
        "review_queue_size": None,
        "proposed_queries": [],
        "failures": [
            PipelineFailure(step="populate_citations", message="rate limited", log_url=None)
        ],
        "git_sha": "deadbeef",
        "image": "gcr.io/vt-gcp-00042/agentic-kg:test",
    }
    data.update(overrides)
    return PipelineRun(**data)


class TestRoundTrip:
    def test_save_then_get_restores_every_field(self, runs_repo):
        run = make_run(f"{_MARKER}20260101T023000Z", "2026-01-01T02:30:00Z")
        runs_repo.save(run)

        restored = runs_repo.get(run.run_id)

        assert restored == run
        assert restored.queries[1].error == "rate limited"
        assert restored.failures[0].log_url is None
        assert restored.review_queue_size is None
        assert restored.budget.stopped_by_budget is False

    def test_running_run_has_null_finished_at(self, runs_repo):
        run = make_run(
            f"{_MARKER}20260101T030000Z",
            "2026-01-01T03:00:00Z",
            status="running",
            finished_at=None,
        )
        runs_repo.save(run)

        restored = runs_repo.get(run.run_id)
        assert restored.status == "running"
        assert restored.finished_at is None

    def test_save_merges_in_place(self, runs_repo):
        run = make_run(f"{_MARKER}20260101T040000Z", "2026-01-01T04:00:00Z", status="running")
        runs_repo.save(run)
        run.status = "succeeded"
        run.finished_at = "2026-01-01T04:10:00Z"
        runs_repo.save(run)

        with runs_repo._repo.session() as session:
            count = session.run(
                "MATCH (r:PipelineRun {run_id: $run_id}) RETURN count(r) AS n",
                run_id=run.run_id,
            ).single()["n"]
        assert count == 1
        assert runs_repo.get(run.run_id).status == "succeeded"

    def test_get_unknown_raises_not_found(self, runs_repo):
        with pytest.raises(PipelineRunNotFoundError):
            runs_repo.get(f"{_MARKER}does-not-exist")


class TestList:
    def test_empty_returns_no_cursor(self, runs_repo):
        runs, cursor = runs_repo.list()
        assert runs == []
        assert cursor is None

    def test_lists_newest_first(self, runs_repo):
        older = make_run(f"{_MARKER}20260101T010000Z", "2026-01-01T01:00:00Z")
        newer = make_run(f"{_MARKER}20260102T010000Z", "2026-01-02T01:00:00Z")
        runs_repo.save(older)
        runs_repo.save(newer)

        runs, cursor = runs_repo.list()
        assert [r.run_id for r in runs] == [newer.run_id, older.run_id]
        assert cursor is None

    def test_cursor_paginates_without_loss(self, runs_repo):
        run_ids = [
            f"{_MARKER}2026010{i}T010000Z" for i in range(1, 4)
        ]
        for i, run_id in enumerate(run_ids, start=1):
            runs_repo.save(make_run(run_id, f"2026-01-0{i}T01:00:00Z"))

        first, cursor = runs_repo.list(limit=2)
        assert [r.run_id for r in first] == [run_ids[2], run_ids[1]]
        assert cursor is not None

        second, cursor2 = runs_repo.list(limit=2, cursor=cursor)
        assert [r.run_id for r in second] == [run_ids[0]]
        assert cursor2 is None

        # No run appears on both pages and none is lost.
        seen = [r.run_id for r in first] + [r.run_id for r in second]
        assert seen == list(reversed(run_ids))
