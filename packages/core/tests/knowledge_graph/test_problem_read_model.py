"""Unit tests for the canonical problem read model.

These exercise the pure normalization/adaptation helpers on
``Neo4jRepository`` (no database). The Neo4j-backed behaviour is covered by
the API integration test in
``packages/api/tests/integration/test_problem_read_model.py``.
"""

import json
from datetime import datetime, timezone

from agentic_kg.knowledge_graph.models import ProblemStatus
from agentic_kg.knowledge_graph.repository import Neo4jRepository


def _repo() -> Neo4jRepository:
    """A bare instance for the pure view-mapping helpers.

    None of ``_concept_view`` / ``_legacy_problem_view`` /
    ``problem_view_to_problem`` / ``_sort_views`` touches the driver, so no
    repository needs constructing -- and constructing one is refused by the
    ownership seam (#87: ``tests/conftest.py`` wraps
    ``Neo4jRepository.__init__`` and refuses any database this session has not
    declared). ``__new__`` skips ``__init__`` entirely, which is the same
    sanctioned fake ``test_aresolve_description.py`` and
    ``test_description_generation_sync_guard.py`` use for pure methods.
    """
    return Neo4jRepository.__new__(Neo4jRepository)


def _concept_node(**overrides) -> dict:
    data = {
        "id": "concept-1",
        "canonical_statement": "How can long-context transformers be made efficient?",
        "status": "open",
        "assumptions": json.dumps([]),
        "constraints": json.dumps([]),
        "datasets": json.dumps([]),
        "metrics": json.dumps([]),
        "verified_baselines": json.dumps(
            [{"name": "BERT-base", "paper_doi": "10.1/v", "performance": {}}]
        ),
        "claimed_baselines": json.dumps(
            [{"name": "GPT-3", "paper_doi": "10.1/c", "performance": {}}]
        ),
        "mention_count": 2,
        "paper_count": 1,
        "created_at": "2026-01-02T03:04:05+00:00",
        "updated_at": "2026-01-02T03:04:05+00:00",
    }
    data.update(overrides)
    return data


class TestConceptView:
    def test_maps_canonical_statement_to_statement(self):
        view = _repo()._concept_view(_concept_node())
        assert view["kind"] == "concept"
        assert view["statement"] == (
            "How can long-context transformers be made efficient?"
        )
        assert view["canonical_statement"] == view["statement"]

    def test_combines_verified_and_claimed_baselines(self):
        view = _repo()._concept_view(_concept_node())
        names = {b["name"] for b in view["baselines"]}
        assert names == {"BERT-base", "GPT-3"}

    def test_uses_explicit_mention_count_and_confidence(self):
        view = _repo()._concept_view(
            _concept_node(), mention_count=7, confidence=0.93
        )
        assert view["mention_count"] == 7
        assert view["confidence"] == 0.93

    def test_builds_evidence_from_first_mention_with_paper(self):
        mentions = [
            {
                "id": "m1",
                "statement": "long context is expensive",
                "quoted_text": "we found long context expensive",
                "section": "Introduction",
                "paper_doi": "10.1234/paper",
                "paper_title": "A Paper",
                "confidence": 0.97,
                "extraction_metadata": {"confidence_score": 0.97},
            }
        ]
        view = _repo()._concept_view(_concept_node(), mentions=mentions)
        assert view["evidence"] == {
            "source_doi": "10.1234/paper",
            "source_title": "A Paper",
            "section": "Introduction",
            "quoted_text": "we found long context expensive",
        }
        assert view["confidence"] == 0.97
        assert view["mentions"] == mentions


class TestLegacyProblemView:
    def test_decodes_nested_fields_and_confidence(self):
        node = {
            "id": "p1",
            "statement": "A legacy problem statement of sufficient length",
            "status": "open",
            "assumptions": json.dumps([{"text": "x", "implicit": False}]),
            "constraints": json.dumps([]),
            "datasets": json.dumps([]),
            "metrics": json.dumps([]),
            "baselines": json.dumps([]),
            "evidence": json.dumps(
                {
                    "source_doi": "10.1/x",
                    "source_title": "T",
                    "section": "S",
                    "quoted_text": "q",
                }
            ),
            "extraction_metadata": json.dumps(
                {"extraction_model": "gpt-4", "confidence_score": 0.8}
            ),
        }
        view = _repo()._legacy_problem_view(node)
        assert view["kind"] == "problem"
        assert view["canonical_statement"] is None
        assert view["confidence"] == 0.8
        assert view["assumptions"][0]["text"] == "x"

    def test_legacy_view_exposes_agent_origin_and_derivation(self):
        node = {
            "id": "synth-1",
            "statement": "A synthesized research direction of sufficient length",
            "status": "open",
            "origin": "agent:synthesis",
            "derived_from": json.dumps(
                [
                    {
                        "method": "synthesis",
                        "inputs": [{"kind": "problem", "ref": "p0"}],
                        "implementation_version": "1.0.0",
                    }
                ]
            ),
        }
        view = _repo()._legacy_problem_view(node)
        assert view["origin"] == "agent:synthesis"
        assert view["derived_from"][0]["inputs"][0]["ref"] == "p0"


class TestProblemViewToProblem:
    def test_concept_view_adapts_to_problem(self):
        repo = _repo()
        view = repo._concept_view(
            _concept_node(),
            mentions=[
                {
                    "id": "m1",
                    "statement": "s",
                    "quoted_text": "q",
                    "section": "Introduction",
                    "paper_doi": "10.1234/paper",
                    "paper_title": "A Paper",
                    "confidence": 0.9,
                }
            ],
        )
        problem = repo.problem_view_to_problem(view)
        assert problem.id == "concept-1"
        assert problem.statement == view["statement"]
        assert problem.status == ProblemStatus.OPEN
        assert problem.evidence is not None
        assert problem.evidence.source_doi == "10.1234/paper"

    def test_resolved_concept_without_evidence_degrades_to_open(self):
        repo = _repo()
        view = repo._concept_view(_concept_node(status="resolved"))
        problem = repo.problem_view_to_problem(view)
        # Problem requires evidence for resolved; the adapter surfaces OPEN
        # rather than dropping the canonical problem.
        assert problem.status == ProblemStatus.OPEN

    def test_datetime_round_trip(self):
        repo = _repo()
        problem = repo.problem_view_to_problem(repo._concept_view(_concept_node()))
        assert isinstance(problem.created_at, datetime)


class TestSortViews:
    def test_newest_first(self):
        repo = _repo()
        older = {"id": "a", "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}
        newer = {"id": "b", "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)}
        ordered = repo._sort_views([older, newer])
        assert [v["id"] for v in ordered] == ["b", "a"]
