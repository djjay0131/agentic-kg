"""Unit tests for RelationService without a live Neo4j.

The full relation behaviour is covered by ``test_relations.py`` (integration).
These tests pin the pure decode/consumer logic that the PR #115 review found
broken:

- ``get_related_problems`` must ignore non-``RelationType`` edges such as the
  ``DERIVED_FROM`` provenance edge, rather than raising ``ValueError``.
"""

from unittest.mock import MagicMock

from agentic_kg.knowledge_graph.models import RelationType
from agentic_kg.knowledge_graph.relations import RelationService


class _FakeTx:
    def __init__(self, records):
        self._records = records

    def run(self, query, *args, **kwargs):
        return self._records


class _FakeSession:
    def __init__(self, records):
        self._records = records

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute_read(self, fn):
        return fn(_FakeTx(self._records))


def _problem_record(node_id: str, statement: str) -> dict:
    return {
        "related": {
            "id": node_id,
            "statement": statement,
            "status": "open",
        },
        "r": {"confidence": 0.8},
        "rel_type": None,  # filled in by the caller
        "direction": "outgoing",
    }


def _service(records: list[dict]) -> RelationService:
    repo = MagicMock()
    repo.session.return_value = _FakeSession(records)
    return RelationService(repository=repo)


class TestGetRelatedProblemsDecode:
    def test_skips_non_enum_edge_types(self):
        """A DERIVED_FROM edge is skipped, not decoded as a RelationType."""
        derived = _problem_record("derived", "Agent-derived problem statement x")
        derived["rel_type"] = "DERIVED_FROM"
        peer = _problem_record("peer", "A related problem statement long enough")
        peer["rel_type"] = RelationType.EXTENDS.value

        related = _service([derived, peer]).get_related_problems("src")

        assert [problem.id for problem, _ in related] == ["peer"]
        assert related[0][1].relation_type == RelationType.EXTENDS

    def test_all_enum_edge_types_decode(self):
        """Every RelationType member round-trips through the decode."""
        records = []
        for i, rel in enumerate(RelationType):
            record = _problem_record(f"p{i}", f"A related problem statement number {i}")
            record["rel_type"] = rel.value
            records.append(record)

        related = _service(records).get_related_problems("src")

        assert [r.relation_type for _, r in related] == list(RelationType)
