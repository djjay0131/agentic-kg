"""Integration test: SynthesisAgent write-back to a live Neo4j.

The unit tests autospec the repository/relation services, which catches
signature drift but cannot prove the Cypher actually runs. This test exercises
the real write path against an ephemeral Neo4j (testcontainers) and asserts
that agent-derived problems are persisted with provenance and lineage edges —
the regression from issue #114 where every write raised ``TypeError`` and was
swallowed.

Runs only under the ``integration`` marker; skips cleanly without Docker /
``NEO4J_URI`` (see ``tests/conftest.py``).
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from agentic_kg.agents.schemas import (
    ContinuationProposal,
    EvaluationResult,
    SynthesisReport,
)
from agentic_kg.agents.state import create_initial_state
from agentic_kg.agents.synthesis import AGENT_ORIGIN, SynthesisAgent
from agentic_kg.knowledge_graph.models import (
    Problem,
    ProblemStatus,
    RelationType,
)
from agentic_kg.knowledge_graph.relations import RelationService

pytestmark = pytest.mark.integration


@pytest.fixture
def source_problem(neo4j_repository):
    """A legacy :Problem that synthesis will extend."""
    token = uuid.uuid4().hex[:10]
    problem = Problem(
        id=f"TEST_SYNTH_SRC_{token}",
        statement=f"TEST_SYNTH_{token} How to scale graph neural networks?",
        status=ProblemStatus.OPEN,
    )
    neo4j_repository.create_problem(problem)
    return problem


def _make_agent(repo, report: SynthesisReport) -> SynthesisAgent:
    llm = MagicMock()
    llm.structured_extract = AsyncMock(return_value=SimpleNamespace(content=report))
    return SynthesisAgent(
        llm_client=llm,
        repository=repo,
        relation_service=RelationService(repository=repo),
    )


def _state(source_problem) -> dict:
    proposal = ContinuationProposal(
        problem_id=source_problem.id,
        title="Subgraph sampling for scalable GNNs",
        methodology="Use importance-weighted neighbor sampling to cut compute.",
        expected_outcome="2x speedup with <5% accuracy loss",
        confidence=0.7,
    )
    evaluation = EvaluationResult(
        proposal_id=source_problem.id,
        feasibility_score=0.8,
        execution_success=True,
        verdict="promising",
    )
    return {
        **create_initial_state(),
        "selected_problem_id": source_problem.id,
        "proposal": proposal.model_dump(mode="json"),
        "evaluation_result": evaluation.model_dump(mode="json"),
    }


class TestSynthesisWritebackIntegration:
    @pytest.mark.asyncio
    async def test_writes_problem_provenance_and_derived_from(
        self, neo4j_repository, source_problem
    ):
        token = uuid.uuid4().hex[:10]
        statement = (
            f"TEST_SYNTH_{token} Can importance sampling help heterogeneous graphs?"
        )
        report = SynthesisReport(
            summary="Investigation of GNN scalability found promising sampling directions.",
            new_problems=[statement],
            new_relations=[],
            source_problem_id=source_problem.id,
        )
        agent = _make_agent(neo4j_repository, report)
        state = _state(source_problem)

        result = await agent.run(state)
        assert result["status"] == "completed"

        with neo4j_repository.session() as session:
            record = session.run(
                "MATCH (p:Problem {statement: $statement}) RETURN p",
                statement=statement,
            ).single()
            assert record is not None, "agent-derived problem was not written"
            props = dict(record["p"])

            assert props["origin"] == AGENT_ORIGIN
            assert props["workflow_run_id"] == state["run_id"]
            assert props["trace_id"] == state["trace_id"]

            derivations = json.loads(props["derived_from"])
            assert derivations[0]["method"] == "synthesis"
            refs = {(i["kind"], i["ref"]) for i in derivations[0]["inputs"]}
            assert ("problem", source_problem.id) in refs

            new_id = props["id"]

            derived = session.run(
                """
                MATCH (p:Problem {id: $new})-[:DERIVED_FROM]->(src)
                RETURN src.id AS id
                """,
                new=new_id,
            ).single()
            assert derived is not None
            assert derived["id"] == source_problem.id

            extends = session.run(
                """
                MATCH (src {id: $src})-[r]->(p {id: $new})
                WHERE type(r) = $rel
                RETURN r
                """,
                src=source_problem.id,
                new=new_id,
                rel=RelationType.EXTENDS.value,
            ).single()
            assert extends is not None

            status = session.run(
                "MATCH (src:Problem {id: $id}) RETURN src.status AS status",
                id=source_problem.id,
            ).single()["status"]
            assert status == ProblemStatus.IN_PROGRESS.value

        # Review finding #1: the DERIVED_FROM provenance edge shares both
        # endpoints with a relation-traversal query. get_related_problems must
        # ignore it (it is not a RelationType) and still return the EXTENDS
        # neighbour, on both the source and the derived problem.
        relations = RelationService(repository=neo4j_repository)

        from_source = relations.get_related_problems(source_problem.id)
        assert [p.id for p, _ in from_source] == [new_id]

        from_derived = relations.get_related_problems(new_id)
        assert [p.id for p, _ in from_derived] == [source_problem.id]
