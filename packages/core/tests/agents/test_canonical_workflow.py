"""End-to-end research agents over the canonical graph (ADR-0009).

The whole chain runs offline against a fake canonical reader: ranking,
continuation, evaluation, synthesis. Synthesis must produce review-queue items
and **no** canonical write; the only way a change reaches the graph is the
human approval path (``commit_candidate`` -> KGCS ``PlanExecutor``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from agentic_kg.agents.config import AgentConfig, AgentLLMConfig
from agentic_kg.agents.continuation import ContinuationAgent
from agentic_kg.agents.evaluation import EvaluationAgent
from agentic_kg.agents.graph_port import SOURCE_CANONICAL, CanonicalGraphPort
from agentic_kg.agents.provenance import InMemoryAgentOutputStore
from agentic_kg.agents.ranking import RankingAgent
from agentic_kg.agents.sandbox import SandboxResult
from agentic_kg.agents.schemas import (
    ContinuationProposal,
    ExperimentalStep,
    RankedProblem,
    RankingResult,
    SynthesisReport,
)
from agentic_kg.agents.state import create_initial_state
from agentic_kg.agents.synthesis import SynthesisAgent

pytest.importorskip("kg_contracts")


def _identity_id() -> str:
    from kg_contracts.identity import new_identity_id

    return new_identity_id("research")


class FakeReader:
    def __init__(self, source_id: str, related_id: str):
        self._entities = {
            source_id: {
                "identity_id": source_id,
                "entity_type": "Problem",
                "display_name": "How can GNNs scale?",
                "status": "ACTIVE",
                "created_at": datetime.now(timezone.utc) - timedelta(days=5),
                "aliases": [],
            },
            related_id: {
                "identity_id": related_id,
                "entity_type": "Problem",
                "display_name": "A related problem about sampling.",
                "status": "ACTIVE",
                "created_at": datetime.now(timezone.utc) - timedelta(days=20),
                "aliases": [],
            },
        }
        self._assertions = {
            source_id: [
                {
                    "assertion_id": "assert-scale-1",
                    "subject_identity": source_id,
                    "predicate": "statement",
                    "object_value": "How can GNNs scale?",
                    "object_identity": None,
                    "status": "ACTIVE",
                    "evidence_refs": [{"evidence_id": "ev-scale", "relationship": "SUPPORTS"}],
                },
                {
                    "assertion_id": "assert-scale-2",
                    "subject_identity": source_id,
                    "predicate": "dataset",
                    "object_value": "OGB",
                    "object_identity": None,
                    "status": "ACTIVE",
                    "evidence_refs": [{"evidence_id": "ev-scale", "relationship": "SUPPORTS"}],
                },
                {
                    "assertion_id": "assert-scale-3",
                    "subject_identity": source_id,
                    "predicate": "related_to",
                    "object_value": None,
                    "object_identity": related_id,
                    "status": "ACTIVE",
                    "evidence_refs": [],
                },
            ],
            related_id: [
                {
                    "assertion_id": "assert-related-1",
                    "subject_identity": related_id,
                    "predicate": "statement",
                    "object_value": "A related problem about sampling.",
                    "object_identity": None,
                    "status": "ACTIVE",
                    "evidence_refs": [],
                }
            ],
        }

    def current_epoch(self):
        return 3

    def find_entities(self, entity_type=None, alias=None, options=None):
        return [
            e
            for e in self._entities.values()
            if entity_type is None or e["entity_type"] == entity_type
        ]

    def get_entity(self, identity_id, options=None):
        return self._entities.get(identity_id)

    def assertions_for(self, identity_id, options=None):
        return list(self._assertions.get(identity_id, []))


class FakeSink:
    namespace = "staging"

    def __init__(self):
        self.items = []

    def save(self, item):
        self.items.append(item)
        return item


class FakeLLM:
    def __init__(self, *, source_id, related_id, target_id):
        self.source_id = source_id
        self.related_id = related_id
        self.target_id = target_id

    async def structured_extract(self, response_model, **kwargs):
        if response_model is RankingResult:
            return SimpleNamespace(
                content=RankingResult(
                    ranked_problems=[
                        RankedProblem(
                            problem_id=self.source_id,
                            statement="How can GNNs scale?",
                            score=0.6,
                            tractability=0.7,
                            data_availability=0.6,
                            cross_domain_impact=0.8,
                            rationale="A tractable and well evidenced problem.",
                        ),
                        RankedProblem(
                            problem_id=self.related_id,
                            statement="A related problem about sampling.",
                            score=0.5,
                            tractability=0.5,
                            data_availability=0.4,
                            cross_domain_impact=0.5,
                            rationale="A less connected problem.",
                        ),
                    ],
                    total_candidates=2,
                )
            )
        if response_model is ContinuationProposal:
            return SimpleNamespace(
                content=ContinuationProposal(
                    problem_id=self.source_id,
                    title="Sampling for scale",
                    methodology="Use importance-weighted neighbour sampling to reduce cost.",
                    expected_outcome="A 2x speedup with minimal accuracy loss.",
                    experimental_steps=[
                        ExperimentalStep(
                            step_number=1,
                            description="Implement importance-weighted sampling",
                            expected_output="Sampling module",
                        )
                    ],
                    rationale="The canonical evidence supports a sampling approach.",
                )
            )
        if response_model is SynthesisReport:
            return SimpleNamespace(
                content=SynthesisReport(
                    summary=(
                        "The evaluation supports a refinement of the source "
                        "problem and a new related problem."
                    ),
                    new_problems=["Can sampling be learned end to end?"],
                    new_relations=[
                        {
                            "source_id": self.source_id,
                            "target_id": self.target_id,
                            "type": "EXTENDS",
                        }
                    ],
                    source_problem_id=self.source_id,
                )
            )
        raise AssertionError(f"unexpected response model: {response_model}")

    async def extract(self, **kwargs):
        return SimpleNamespace(content="print('ok')")


class FakeSandbox:
    def execute(self, code):
        return SandboxResult(
            success=True,
            stdout='{"metrics": {"accuracy": 0.91}}',
            stderr="",
            exit_code=0,
        )


class ForbiddenRepository:
    """Every attribute access fails: a canonical run must not touch it."""

    def __getattr__(self, name):
        raise AssertionError(f"canonical agent touched repository.{name}")


@pytest.fixture
def workflow():
    source_id = _identity_id()
    related_id = _identity_id()
    target_id = _identity_id()
    reader = FakeReader(source_id, related_id)
    port = CanonicalGraphPort(reader)
    store = InMemoryAgentOutputStore()
    sink = FakeSink()
    llm = FakeLLM(source_id=source_id, related_id=related_id, target_id=target_id)
    repo = ForbiddenRepository()
    common = dict(
        llm_client=llm,
        repository=repo,
        graph_port=port,
        output_store=store,
        source=SOURCE_CANONICAL,
        model="test-model",
        prompt_version="test-prompt-v1",
    )
    return SimpleNamespace(
        source_id=source_id,
        related_id=related_id,
        target_id=target_id,
        reader=reader,
        port=port,
        store=store,
        sink=sink,
        llm=llm,
        ranking=RankingAgent(**common),
        continuation=ContinuationAgent(**common),
        evaluation=EvaluationAgent(**common, sandbox=FakeSandbox()),
        synthesis=SynthesisAgent(**common, canonical_sink=sink, namespace="staging"),
    )


@pytest.mark.asyncio
async def test_full_canonical_workflow_queues_writes_and_tracks_lineage(workflow):
    state = create_initial_state(topic_filter=None, max_problems=10)

    state = await workflow.ranking.run(state)
    ranked = state["ranked_problems"]
    assert {r["problem_id"] for r in ranked} == {
        workflow.source_id,
        workflow.related_id,
    }
    for ranked_problem in ranked:
        assert ranked_problem["provenance"]["source"] == "canonical"
        assert ranked_problem["provenance"]["citations"]
        assert ranked_problem["graph_native_score"] is not None
        assert ranked_problem["output_id"]
    # The graph-native score changed the ranking order: the source problem is
    # better evidenced and fresher than the related one.
    assert ranked[0]["problem_id"] == workflow.source_id
    ranking_output_id = ranked[0]["output_id"]

    state = {**state, "selected_problem_id": workflow.source_id}
    state = await workflow.continuation.run(state)
    proposal = state["proposal"]
    assert proposal["provenance"]["derived_from"] == [ranking_output_id]
    assert proposal["provenance"]["citations"]
    assert proposal["output_id"]

    state = {**state, "proposal_approved": True}
    state = await workflow.evaluation.run(state)
    evaluation = state["evaluation_result"]
    assert evaluation["provenance"]["derived_from"] == [proposal["output_id"]]
    assert evaluation["output_id"]
    assert evaluation["verdict"] == "inconclusive"

    state = {**state, "evaluation_approved": True}
    state = await workflow.synthesis.run(state)
    report = state["synthesis_report"]
    assert report["review_item_ids"]
    assert report["provenance"]["derived_from"] == [
        proposal["output_id"],
        evaluation["output_id"],
    ]
    assert len(report["items"]) == len(workflow.sink.items)
    for item in report["items"]:
        assert item["provenance"]["source"] == "canonical"

    # Nothing canonical was written: every change is a pending review item.
    assert workflow.sink.items
    for item in workflow.sink.items:
        assert item.status == "pending"
        assert item.source == "agent"
        assert item.agent_name == "synthesis"
        assert item.agent_output_id == report["output_id"]
        assert item.adviser_model_id == "test-model"
        assert item.prompt_version == "test-prompt-v1"

    status_items = [i for i in workflow.sink.items if i.candidate_kind == "status_update"]
    assert status_items and status_items[0].committable is False


@pytest.mark.asyncio
async def test_approval_is_the_only_way_the_candidate_commits(workflow):
    state = create_initial_state()
    state = await workflow.ranking.run(state)
    state = {**state, "selected_problem_id": workflow.source_id}
    state = await workflow.continuation.run(state)
    state = {**state, "proposal_approved": True}
    state = await workflow.evaluation.run(state)
    state = {**state, "evaluation_approved": True}
    state = await workflow.synthesis.run(state)

    from agentic_kg.migration.curation.commit import commit_candidate
    from kg_contracts.testing.memory import MemoryGraphStore

    store = MemoryGraphStore()
    entity_items = [item for item in workflow.sink.items if item.candidate_kind == "entity"]
    assert entity_items
    assert store.find_entities(entity_type="Problem") == []

    result = commit_candidate(
        entity_items[0].candidate_payload,
        namespace="staging",
        canonical_store=store,
    )
    assert result.committed is True
    assert len(store.find_entities(entity_type="Problem")) == 1


def test_agent_config_reads_the_graph_source(monkeypatch):
    monkeypatch.setenv("AGENT_GRAPH_SOURCE", "canonical")
    from agentic_kg.agents.config import reset_agent_config

    reset_agent_config()
    try:
        assert AgentConfig().graph_source == "canonical"
    finally:
        reset_agent_config()


def test_llm_config_defaults_are_unchanged():
    assert AgentLLMConfig().model == "gpt-4o"
