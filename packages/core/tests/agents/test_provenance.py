"""Agent provenance envelopes and output storage (ADR-0009)."""

from __future__ import annotations

import pytest
from agentic_kg.agents.graph_port import SOURCE_CANONICAL, SOURCE_LEGACY
from agentic_kg.agents.provenance import (
    AgentOutput,
    InMemoryAgentOutputStore,
    build_provenance,
    record_agent_output,
)


class TestBuildProvenance:
    def test_legacy_output_abstains_honestly(self):
        prov = build_provenance(agent="ranking", run_id="run-1", source=SOURCE_LEGACY)
        assert prov.abstained is True
        assert prov.citations == []
        assert "legacy" in (prov.rationale or "").lower()
        assert prov.sender == "retriever"
        assert prov.kind == "retrieval_result"

    def test_canonical_output_cites_assertions_and_evidence(self):
        prov = build_provenance(
            agent="ranking",
            run_id="run-1",
            source=SOURCE_CANONICAL,
            assertion_ids=["a1", "a2"],
            assertion_evidence={"a1": ["ev1"], "a2": ["ev2", "ev3"]},
            evidence_ids=["ev1", "ev2", "ev3"],
            problem_ids=["p1"],
            derived_from=["upstream-1"],
        )
        assert prov.abstained is False
        assert [c.assertion_id for c in prov.citations] == ["a1", "a2"]
        assert prov.citations[1].evidence_ids == ["ev2", "ev3"]
        assert prov.used_evidence_ids == ["ev1", "ev2", "ev3"]
        assert prov.used_problem_ids == ["p1"]
        assert prov.derived_from == ["upstream-1"]

    def test_partial_evidence_map_is_preserved(self):
        prov = build_provenance(
            agent="continuation",
            run_id="run-1",
            source=SOURCE_CANONICAL,
            assertion_ids=["a1"],
        )
        assert prov.abstained is False
        assert prov.citations[0].evidence_ids == []

    def test_empty_assertion_ids_abstain(self):
        prov = build_provenance(agent="synthesis", run_id="run-1", source=SOURCE_CANONICAL)
        assert prov.abstained is True
        assert "did not resolve" in (prov.rationale or "")


class TestKgpsEnvelope:
    def test_validates_as_a_real_provenance_envelope(self):
        pytest.importorskip("kgps")
        prov = build_provenance(
            agent="ranking",
            run_id="run-1",
            source=SOURCE_CANONICAL,
            assertion_ids=["a1"],
            assertion_evidence={"a1": ["ev1"]},
            rationale="ranked because evidence",
        )
        envelope = prov.to_kgps_envelope()
        assert envelope.sender.value == "retriever"
        assert envelope.citations[0].assertion_id == "a1"
        assert envelope.abstained is False

    def test_answer_envelope_carries_a_grounded_answer(self):
        pytest.importorskip("kgps")
        prov = build_provenance(
            agent="continuation",
            run_id="run-1",
            source=SOURCE_CANONICAL,
            assertion_ids=["a1"],
            rationale="A methodology that is grounded.",
        )
        envelope = prov.to_kgps_envelope()
        assert envelope.kind.value == "answer"
        assert envelope.answer is not None
        assert envelope.answer.sentences[0].citations[0].assertion_id == "a1"

    def test_legacy_answer_degrades_to_abstaining_retrieval(self):
        pytest.importorskip("kgps")
        prov = build_provenance(agent="continuation", run_id="run-1", source=SOURCE_LEGACY)
        envelope = prov.to_kgps_envelope()
        assert envelope.kind.value == "retrieval_result"
        assert envelope.abstained is True
        assert envelope.answer is None


class TestOutputStore:
    def test_record_and_get_round_trip(self):
        store = InMemoryAgentOutputStore()
        prov = build_provenance(
            agent="ranking",
            run_id="run-1",
            source=SOURCE_CANONICAL,
            assertion_ids=["a1"],
        )
        output_id = record_agent_output(
            store,
            agent="ranking",
            run_id="run-1",
            source=SOURCE_CANONICAL,
            output_kind="ranked_problem",
            payload={"score": 0.5},
            provenance=prov,
        )
        output = store.get(output_id)
        assert isinstance(output, AgentOutput)
        assert output.agent == "ranking"
        assert output.provenance.citations[0].assertion_id == "a1"
        assert output.to_neo4j_properties()["payload"]

    def test_no_store_returns_an_id_without_persisting(self):
        prov = build_provenance(agent="ranking", run_id="run-1", source=SOURCE_LEGACY)
        output_id = record_agent_output(
            None,
            agent="ranking",
            run_id="run-1",
            source=SOURCE_LEGACY,
            payload={},
            provenance=prov,
        )
        assert output_id.startswith("agentout_")
