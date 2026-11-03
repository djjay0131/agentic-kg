"""GraphPort adapters (ADR-0010): legacy unchanged, canonical read-only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from agentic_kg.agents.graph_port import (
    SOURCE_CANONICAL,
    SOURCE_LEGACY,
    CanonicalGraphPort,
    LegacyGraphPort,
    build_graph_port,
    canonical_graph_score,
    graph_source_from_env,
)


class FakeReader:
    """The subset of ``Neo4jCanonicalGraphReader`` the port uses."""

    def __init__(self, entities, assertions):
        self._entities = {e["identity_id"]: e for e in entities}
        self._assertions = assertions

    def current_epoch(self):
        return 7

    def find_entities(self, entity_type=None, alias=None, options=None):
        return [
            e
            for e in self._entities.values()
            if entity_type is None or e["entity_type"] == entity_type
        ]

    def get_entity(self, identity_id, options=None):
        return self._entities.get(identity_id)

    def assertions_for(self, identity_id, options=None):
        return [a for a in self._assertions if a["subject_identity"] == identity_id]


def _entity(identity_id, display_name, created_days_ago=10, entity_type="Problem"):
    return {
        "identity_id": identity_id,
        "entity_type": entity_type,
        "display_name": display_name,
        "status": "ACTIVE",
        "created_at": datetime.now(timezone.utc) - timedelta(days=created_days_ago),
        "aliases": [],
    }


def _assertion(
    aid,
    subject,
    predicate,
    *,
    object_value=None,
    object_identity=None,
    evidence_ids=(),
):
    return {
        "assertion_id": aid,
        "subject_identity": subject,
        "predicate": predicate,
        "object_value": object_value,
        "object_identity": object_identity,
        "status": "ACTIVE",
        "evidence_refs": [{"evidence_id": eid, "relationship": "SUPPORTS"} for eid in evidence_ids],
    }


@pytest.fixture
def reader():
    return FakeReader(
        entities=[
            _entity("prob-a", "Problem A", created_days_ago=1),
            _entity("prob-b", "Problem B", created_days_ago=900),
            _entity("topic-1", "Graph ML", entity_type="Topic"),
        ],
        assertions=[
            _assertion(
                "a1",
                "prob-a",
                "statement",
                object_value="How to scale A?",
                evidence_ids=("ev1",),
            ),
            _assertion("a2", "prob-a", "dataset", object_value="OGB", evidence_ids=("ev1",)),
            _assertion("a3", "prob-a", "metric", object_value="accuracy", evidence_ids=("ev2",)),
            _assertion("a4", "prob-a", "belongs_to", object_identity="topic-1"),
            _assertion("a5", "prob-b", "statement", object_value="Old problem?"),
        ],
    )


class TestCanonicalGraphPort:
    def test_lists_problem_entities_with_graph_features(self, reader):
        port = CanonicalGraphPort(reader)
        problems = port.list_candidates(topic_filter=None, status_filter=None, max_problems=10)
        assert [p.problem_id for p in problems] == ["prob-a", "prob-b"]
        first = problems[0]
        assert first.statement == "Problem A"
        assert first.dataset_count == 1
        assert first.metric_count == 1
        assert first.assertion_count == 4
        assert first.evidence_strength == pytest.approx(0.5)
        assert first.connectivity == pytest.approx(0.5)
        assert first.freshness > problems[1].freshness
        assert first.graph_native_score == canonical_graph_score(
            evidence_strength=first.evidence_strength,
            data_availability=first.data_availability,
            connectivity=first.connectivity,
            freshness=first.freshness,
        )

    def test_topic_and_status_filters_are_applied(self, reader):
        port = CanonicalGraphPort(reader)
        assert [
            p.problem_id
            for p in port.list_candidates(
                topic_filter="topic-1", status_filter=None, max_problems=10
            )
        ] == ["prob-a"]
        assert (
            port.list_candidates(topic_filter=None, status_filter="resolved", max_problems=10) == []
        )

    def test_max_problems_is_respected(self, reader):
        port = CanonicalGraphPort(reader)
        assert len(port.list_candidates(topic_filter=None, status_filter=None, max_problems=1)) == 1

    def test_context_carries_assertion_and_evidence_ids(self, reader):
        context = CanonicalGraphPort(reader).load_context("prob-a")
        assert context.statement == "Problem A"
        assert set(context.assertion_ids) == {"a1", "a2", "a3", "a4"}
        assert set(context.evidence_ids) == {"ev1", "ev2"}
        assert [m.name for m in context.metrics] == ["accuracy"]

    def test_missing_problem_is_a_key_error(self, reader):
        with pytest.raises(KeyError):
            CanonicalGraphPort(reader).load_context("missing")

    def test_port_holds_no_write_surface(self, reader):
        port = CanonicalGraphPort(reader)
        for name in ("apply", "create_identity", "attach_assertion", "store"):
            assert not hasattr(port, name)

    def test_is_not_a_graph_mutation_store(self, reader):
        pytest.importorskip("kg_contracts")
        from kg_contracts.stores import GraphMutationStore

        assert not isinstance(CanonicalGraphPort(reader), GraphMutationStore)


class TestScoreFormula:
    def test_all_terms_zero(self):
        assert (
            canonical_graph_score(
                evidence_strength=0.0,
                data_availability=0.0,
                connectivity=0.0,
                freshness=0.0,
            )
            == 0.0
        )

    def test_all_terms_one(self):
        assert (
            canonical_graph_score(
                evidence_strength=1.0,
                data_availability=1.0,
                connectivity=1.0,
                freshness=1.0,
            )
            == 1.0
        )

    def test_weights_are_the_documented_ones(self):
        assert (
            canonical_graph_score(
                evidence_strength=1.0,
                data_availability=0.0,
                connectivity=0.0,
                freshness=0.0,
            )
            == 0.35
        )
        assert (
            canonical_graph_score(
                evidence_strength=0.0,
                data_availability=1.0,
                connectivity=0.0,
                freshness=0.0,
            )
            == 0.25
        )


class TestLegacyGraphPort:
    def test_uses_search_when_available(self):
        from types import SimpleNamespace

        class Search:
            def __init__(self):
                self.calls = []

            def structured_search(self, **kwargs):
                self.calls.append(kwargs)
                problem = SimpleNamespace(
                    id="p1",
                    statement="A legacy problem",
                    status=None,
                    extraction_metadata=None,
                )
                return [SimpleNamespace(problem=problem)]

        search = Search()
        port = LegacyGraphPort(repository=None, search_service=search)
        problems = port.list_candidates(topic_filter="t1", status_filter=None, max_problems=5)
        assert [p.problem_id for p in problems] == ["p1"]
        # Pre-ADR behaviour: a missing status is ``None`` (unfiltered), not the
        # "open" string, which ``structured_search`` would dereference as an
        # enum and raise on (defect D-4).
        assert search.calls == [{"topic_id": "t1", "status": None, "top_k": 5}]
        assert problems[0].graph_native_score is None

    def test_falls_back_to_repository(self):
        from types import SimpleNamespace

        class Repo:
            def list_problems(self, **kwargs):
                assert kwargs == {"status": None, "limit": 3}
                return [
                    SimpleNamespace(
                        id="p2",
                        statement="Repo problem",
                        status=SimpleNamespace(value="open"),
                        extraction_metadata=None,
                    )
                ]

        problems = LegacyGraphPort(Repo()).list_candidates(
            topic_filter=None, status_filter=None, max_problems=3
        )
        assert problems[0].problem_id == "p2"
        assert problems[0].status == "open"


class TestSourceSelection:
    def test_default_is_legacy(self):
        assert graph_source_from_env({}) == SOURCE_LEGACY

    def test_canonical_is_accepted(self):
        assert graph_source_from_env({"AGENT_GRAPH_SOURCE": "canonical"}) == SOURCE_CANONICAL

    def test_unknown_value_fails_loudly(self):
        with pytest.raises(ValueError, match="AGENT_GRAPH_SOURCE"):
            graph_source_from_env({"AGENT_GRAPH_SOURCE": "graphs"})

    def test_build_rejects_unknown_source(self):
        with pytest.raises(ValueError):
            build_graph_port("nope", repository=None)
