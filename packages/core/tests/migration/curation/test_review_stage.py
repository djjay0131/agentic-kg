"""The nightly review-queue adviser stage (no Docker, no network, no model).

A real KGIS ledger is written to a temporary directory, so the stage exercises
the same ``ShadowStores`` path the Job uses. The completion client is a fake
``CompletionPort`` that records the prompt, which is how these tests prove the
adviser received evidence *text* rather than only ids.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agentic_kg.migration.ingestion.stores import ShadowStores
from agentic_kg.pipeline.curate import run_curation_stage
from kg_contracts.candidates import (
    AttributeAssertionCandidate,
    EntityCandidate,
    SourceCoordinates,
)
from kg_contracts.evidence import (
    Evidence,
    EvidenceAvailability,
    EvidenceRef,
    EvidenceRelationship,
    Provenance,
)
from kg_contracts.identity import EntityRef
from kg_contracts.testing.factories import make_scores

RUN_ID = "nightly-20260101T023000Z"


class FakeCompletion:
    """A deterministic ``CompletionPort`` that cites what it was given."""

    def __init__(self, recommendation: str = "admit", confidence: float = 0.92):
        self.recommendation = recommendation
        self.confidence = confidence
        self.requests = []
        self.merge_into: str | None = None

    def complete(self, request):
        from kgcs.advisers import CompletionResponse

        self.requests.append(request)
        payload = {
            "recommendation": self.recommendation,
            "evidence_ids": list(request.evidence_ids),
            "confidence": self.confidence,
            "rationale": "synthetic rationale",
        }
        if self.merge_into:
            payload["merge_into"] = self.merge_into
        return CompletionResponse(
            text=json.dumps(payload),
            model_id="fake-model",
            model_version="1",
        )


class FakeReviewStore:
    def __init__(self):
        self.items = []

    def candidate_ids_for_run(self, run_id):
        return {item.candidate_id for item in self.items if item.run_id == run_id}

    def save(self, item):
        self.items.append(item)
        return item


def _evidence(evidence_id: str, content: str) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        source_type="paper",
        source_locator="paper://doi/10.1000/x",
        observed_at=datetime.now(UTC),
        availability=EvidenceAvailability.PRESENT,
        content=content,
        provenance=Provenance(
            source="test",
            source_ref="fixture",
            actor="test",
        ),
    )


def _entity_candidate(
    *,
    key: str = "X problem",
    evidence_id: str = "ev1",
    run_id: str = f"{RUN_ID}:q1",
) -> EntityCandidate:
    return EntityCandidate(
        graph_id="research",
        producer="test",
        producer_run_id=run_id,
        ontology_version="1",
        source_coordinates=SourceCoordinates(
            source_type="paper", locator="paper://doi/10.1000/x"
        ),
        semantic_key=f"problem/{key.lower().replace(' ', '-')}",
        scores=make_scores(
            extraction_confidence=0.8, source_reliability=0.75
        ),
        entity_type="Problem",
        aliases=(EntityRef(entity_type="Problem", namespace="surface", key=key),),
        display_name=key,
        properties={},
        evidence_refs=(
            EvidenceRef(
                evidence_id=evidence_id,
                relationship=EvidenceRelationship.SUPPORTS,
            ),
        ),
    )


def _assertion_candidate(*, subject: str = "surface:x") -> AttributeAssertionCandidate:
    subject_ref = EntityRef(entity_type="Paper", namespace="surface", key=subject)
    return AttributeAssertionCandidate(
        graph_id="research",
        producer="test",
        producer_run_id=f"{RUN_ID}:q1",
        ontology_version="1",
        source_coordinates=SourceCoordinates(
            source_type="paper", locator="paper://doi/10.1000/x"
        ),
        semantic_key="paper/x/title",
        scores=make_scores(
            extraction_confidence=0.8, source_reliability=0.75
        ),
        subject=subject_ref,
        attribute="title",
        value="A title",
        valid_period=None,
        evidence_refs=(
            EvidenceRef(
                evidence_id="ev2",
                relationship=EvidenceRelationship.SUPPORTS,
            ),
        ),
    )


def _write_ledger(root: Path, candidates, evidence) -> None:
    stores = ShadowStores.at(root)
    try:
        for item in evidence:
            stores.evidence.put(item)
        for candidate in candidates:
            stores.evidence.add_refs(candidate.candidate_id, list(candidate.evidence_refs))
        stores.ledger.submit(list(candidates))
    finally:
        stores.close()


def test_grades_deferred_entity_with_evidence_text(tmp_path):
    candidate = _entity_candidate()
    _write_ledger(
        tmp_path,
        [candidate],
        [_evidence("ev1", "X is hard because Y.")],
    )
    completion = FakeCompletion()
    store = FakeReviewStore()

    result = run_curation_stage(
        run_id=RUN_ID,
        namespace="staging",
        ledger_dir=tmp_path,
        review_store=store,
        completion=completion,
    )

    assert result.review_items_written == 1
    assert result.deferred_candidates == 1
    item = store.items[0]
    assert item.candidate_id == candidate.candidate_id
    assert item.adviser_verdict == "admit"
    assert item.confidence == pytest.approx(0.92)
    assert item.prompt_version == "2+evidence"
    assert item.evidence_refs[0].content == "X is hard because Y."
    # The prompt carried the actual text, not only the id.
    assert "X is hard because Y." in completion.requests[0].prompt
    assert "ev1" in completion.requests[0].prompt


def test_idempotent_on_run_and_candidate(tmp_path):
    candidate = _entity_candidate()
    _write_ledger(tmp_path, [candidate], [_evidence("ev1", "text")])
    completion = FakeCompletion()
    store = FakeReviewStore()

    first = run_curation_stage(
        run_id=RUN_ID,
        namespace="staging",
        ledger_dir=tmp_path,
        review_store=store,
        completion=completion,
    )
    second = run_curation_stage(
        run_id=RUN_ID,
        namespace="staging",
        ledger_dir=tmp_path,
        review_store=store,
        completion=completion,
    )

    assert first.review_items_written == 1
    assert second.review_items_written == 0
    assert second.review_items_skipped == 1
    assert len(store.items) == 1
    assert len(completion.requests) == 1


def test_evidence_less_candidate_is_not_queued(tmp_path):
    """Only deferred candidates are queued; artifacts and AUTO plans are not."""
    candidate = _entity_candidate()
    _write_ledger(tmp_path, [candidate], [_evidence("ev1", "text")])
    store = FakeReviewStore()

    result = run_curation_stage(
        run_id="nightly-other",
        namespace="staging",
        ledger_dir=tmp_path,
        review_store=store,
        completion=FakeCompletion(),
    )

    assert result.candidates_seen == 0
    assert result.review_items_written == 0
    assert "review_candidates" in result.honest_nulls


def test_alias_subject_assertion_is_not_queued(tmp_path):
    """It cannot be committed without ER, so a human cannot decide it yet."""
    assertion = _assertion_candidate(subject="surface:x")
    _write_ledger(
        tmp_path,
        [assertion],
        [_evidence("ev2", "assertion text")],
    )
    store = FakeReviewStore()

    result = run_curation_stage(
        run_id=RUN_ID,
        namespace="staging",
        ledger_dir=tmp_path,
        review_store=store,
        completion=FakeCompletion(),
    )

    assert result.review_items_written == 0
    assert result.excluded.get("uncommittable_assertion") == 1


def test_budget_stops_without_inventing_grades(tmp_path):
    first = _entity_candidate(key="A problem", evidence_id="ev1")
    second = _entity_candidate(key="B problem", evidence_id="ev2")
    _write_ledger(
        tmp_path,
        [first, second],
        [_evidence("ev1", "a"), _evidence("ev2", "b")],
    )
    store = FakeReviewStore()

    result = run_curation_stage(
        run_id=RUN_ID,
        namespace="staging",
        ledger_dir=tmp_path,
        review_store=store,
        completion=FakeCompletion(),
        max_llm_usd=0.0015,
        est_usd_per_call=0.001,
    )

    assert result.stopped_by_budget is True
    assert result.review_items_written == 1
    assert result.ungraded == 1
    assert "budget" in result.honest_nulls


def test_merge_verdict_is_stored_but_not_committable(tmp_path):
    candidate = _entity_candidate()
    _write_ledger(tmp_path, [candidate], [_evidence("ev1", "text")])
    completion = FakeCompletion(recommendation="merge")
    completion.merge_into = "kg://research/identity/EXISTING"
    store = FakeReviewStore()

    result = run_curation_stage(
        run_id=RUN_ID,
        namespace="staging",
        ledger_dir=tmp_path,
        review_store=store,
        completion=completion,
    )

    item = store.items[0]
    assert result.review_items_written == 1
    assert item.adviser_verdict == "merge-into:kg://research/identity/EXISTING"
    assert item.committable is False
    assert "merge" in (item.commit_blocker or "")


def test_hold_is_stored_as_reject(tmp_path):
    candidate = _entity_candidate()
    _write_ledger(tmp_path, [candidate], [_evidence("ev1", "text")])
    store = FakeReviewStore()

    result = run_curation_stage(
        run_id=RUN_ID,
        namespace="staging",
        ledger_dir=tmp_path,
        review_store=store,
        completion=FakeCompletion(recommendation="hold", confidence=0.2),
    )

    assert store.items[0].adviser_verdict == "reject"
    assert result.verdict_counts == {"reject": 1}


def test_no_ledger_is_an_honest_null():
    store = FakeReviewStore()
    result = run_curation_stage(
        run_id=RUN_ID,
        namespace="staging",
        ledger_dir=None,
        review_store=store,
        completion=FakeCompletion(),
    )
    assert result.review_items_written == 0
    assert "ledger" in result.honest_nulls


def test_identity_endpoint_relation_is_queued_and_committable(tmp_path):
    from kg_contracts.candidates import RelationCandidate
    from kg_contracts.identity import new_identity_id

    relation = RelationCandidate(
        graph_id="research",
        producer="test",
        producer_run_id=f"{RUN_ID}:q1",
        ontology_version="1",
        source_coordinates=SourceCoordinates(
            source_type="paper", locator="paper://doi/10.1000/x"
        ),
        semantic_key="relation/x",
        scores=make_scores(
            extraction_confidence=0.8, source_reliability=0.75
        ),
        relation_type="EXTENDS",
        subject=new_identity_id("research"),
        object=new_identity_id("research"),
        properties={},
        evidence_refs=(
            EvidenceRef(
                evidence_id="ev3",
                relationship=EvidenceRelationship.SUPPORTS,
            ),
        ),
    )
    _write_ledger(tmp_path, [relation], [_evidence("ev3", "relation text")])
    store = FakeReviewStore()

    result = run_curation_stage(
        run_id=RUN_ID,
        namespace="staging",
        ledger_dir=tmp_path,
        review_store=store,
        completion=FakeCompletion(),
    )

    assert result.review_items_written == 1
    item = store.items[0]
    assert item.candidate_kind == "relation"
    assert item.committable is True


class _RunProjectionStore:
    """Only the two review-store reads the run projection makes."""

    def __init__(self, pending: int = 0, verdicts: dict[str, int] | None = None):
        self.pending = pending
        self.verdicts = verdicts or {}

    def count_items(self, *, status: str, run_id: str) -> int:
        assert status == "pending"
        return self.pending

    def verdict_counts(self, run_id: str) -> dict[str, int]:
        return dict(self.verdicts)


def _pipeline_run(**overrides):
    from agentic_kg.knowledge_graph.models.pipeline_run import PipelineRun

    base = dict(
        run_id=RUN_ID,
        started_at="2026-01-01T02:30:00Z",
        finished_at="2026-01-01T02:35:00Z",
        status="succeeded",
        trigger="schedule",
        namespace="canon",
    )
    base.update(overrides)
    return PipelineRun(**base)


def test_adviser_stage_emits_the_single_completed_event(tmp_path, monkeypatch, capsys):
    from agentic_kg.knowledge_graph import pipeline_runs
    from agentic_kg.pipeline.curate import CurateResult, update_pipeline_run_from_stage

    run = _pipeline_run()
    monkeypatch.setattr(pipeline_runs, "get_pipeline_run", lambda *a, **k: run)
    monkeypatch.setattr(pipeline_runs, "save_pipeline_run", lambda *a, **k: run)

    result = CurateResult(run_id=RUN_ID, namespace="canon", est_llm_usd=0.002)
    store = _RunProjectionStore(pending=3, verdicts={"admit": 2, "reject": 1})

    updated = update_pipeline_run_from_stage(
        result,
        review_store=store,
        runs_dir=tmp_path,
        runs_bucket="vt-gcp-00042-agentic-kg-runs-staging",
        ui_base_url="https://agentic-kg-ui-staging.example.com/",
    )

    assert updated is run
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["event"] == "pipeline_run_completed"
    assert event["severity"] == "INFO"
    assert event["run_id"] == RUN_ID
    assert event["review_queue_size"] == 3
    assert event["llm_usd"] == 0.002
    assert event["run_url"] == (
        f"https://agentic-kg-ui-staging.example.com/runs/{RUN_ID}"
    )
    assert event["url"] == (
        "gs://vt-gcp-00042-agentic-kg-runs-staging/nightly/" f"{RUN_ID}.json"
    )


def test_adviser_failure_emits_a_partial_completed_event(monkeypatch, capsys):
    from agentic_kg.knowledge_graph import pipeline_runs
    from agentic_kg.pipeline.curate import CurateResult, update_pipeline_run_from_stage

    run = _pipeline_run()
    monkeypatch.setattr(pipeline_runs, "get_pipeline_run", lambda *a, **k: run)
    monkeypatch.setattr(pipeline_runs, "save_pipeline_run", lambda *a, **k: run)

    result = CurateResult(
        run_id=RUN_ID,
        namespace="canon",
        failures=["cand-1: RuntimeError: model timed out"],
    )

    update_pipeline_run_from_stage(
        result,
        review_store=_RunProjectionStore(),
    )

    assert run.status == "partial"
    event = json.loads(capsys.readouterr().out.strip())
    assert event["event"] == "pipeline_run_completed"
    assert event["status"] == "partial"
    assert event["failure_count"] == 1


def test_adviser_stage_can_skip_the_event(monkeypatch, capsys):
    from agentic_kg.knowledge_graph import pipeline_runs
    from agentic_kg.pipeline.curate import CurateResult, update_pipeline_run_from_stage

    run = _pipeline_run()
    monkeypatch.setattr(pipeline_runs, "get_pipeline_run", lambda *a, **k: run)
    monkeypatch.setattr(pipeline_runs, "save_pipeline_run", lambda *a, **k: run)

    update_pipeline_run_from_stage(
        CurateResult(run_id=RUN_ID, namespace="canon"),
        review_store=_RunProjectionStore(),
        emit_event=False,
    )

    assert capsys.readouterr().out.strip() == ""
