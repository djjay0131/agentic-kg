"""
Tests for agentic_kg.agents.synthesis.

The repository / relation mocks are autospecced (see ``conftest.py``), so a
write-back call that drifts from the real signature raises ``TypeError``
instead of being silently swallowed by the agent's error handling.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from agentic_kg.agents.schemas import SynthesisReport
from agentic_kg.agents.synthesis import (
    AGENT_ORIGIN,
    DERIVATION_METHOD,
    SynthesisAgent,
)
from agentic_kg.knowledge_graph.models import (
    Evidence,
    Problem,
    ProblemStatus,
    RelationType,
)


class TestSynthesisAgent:
    """Tests for SynthesisAgent."""

    @pytest.fixture
    def agent(self, mock_llm, mock_repo, mock_search, mock_relations):
        return SynthesisAgent(
            llm_client=mock_llm,
            repository=mock_repo,
            search_service=mock_search,
            relation_service=mock_relations,
        )

    @pytest.fixture
    def synthesis_report(self):
        return SynthesisReport(
            summary=(
                "Investigation of GNN scalability yielded promising "
                "approaches via subgraph sampling."
            ),
            new_problems=["Can importance sampling be applied to heterogeneous graphs?"],
            new_relations=[
                {"source_id": "prob-1", "target_id": "prob-2", "type": "RELATED_TO"},
            ],
            source_problem_id="prob-1",
            recommendations=["Run on larger datasets"],
        )

    @pytest.fixture
    def source_with_evidence(self):
        problem = MagicMock()
        problem.id = "prob-1"
        problem.statement = "How to improve GNN scalability?"
        problem.status = SimpleNamespace(value="open")
        problem.evidence = Evidence(
            source_doi="10.1234/source",
            source_title="Source Paper",
            section="Introduction",
            quoted_text="we could not scale",
        )
        return problem

    def test_name(self, agent):
        assert agent.name == "synthesis"

    def test_autospec_rejects_signature_drift(self, mock_repo, mock_relations):
        """The autospecced mocks fail loudly on the old (wrong) calls.

        This is the guarantee the issue asked for: write-back signature
        drift cannot hide behind a loose mock.
        """
        with pytest.raises(TypeError):
            mock_repo.create_problem(id="x", statement="y", status="open")
        with pytest.raises(TypeError):
            mock_relations.create_relation(
                source_id="a", target_id="b", relation_type="EXTENDS"
            )

    @pytest.mark.asyncio
    async def test_run_happy_path(self, agent, mock_llm, synthesis_report, state_with_evaluation):
        """Run generates report and applies graph updates."""
        mock_llm.structured_extract.return_value = SimpleNamespace(content=synthesis_report)

        result = await agent.run(state_with_evaluation)

        assert result["synthesis_report"] is not None
        assert result["status"] == "completed"
        # Should have created problem + relation for new_problems, plus the explicit relation
        mock_repo = agent.repo
        mock_repo.create_problem.assert_called_once()
        agent.relations.create_relation.assert_called()

    @pytest.mark.asyncio
    async def test_create_problem_uses_real_signature_and_provenance(
        self, agent, mock_llm, synthesis_report, state_with_evaluation
    ):
        """The created problem is a Problem model carrying agent provenance."""
        mock_llm.structured_extract.return_value = SimpleNamespace(content=synthesis_report)

        await agent.run(state_with_evaluation)

        (created,), _kwargs = agent.repo.create_problem.call_args
        assert isinstance(created, Problem)
        assert created.origin == AGENT_ORIGIN
        assert created.status == ProblemStatus.OPEN
        assert created.workflow_run_id == state_with_evaluation["run_id"]
        assert created.trace_id == state_with_evaluation["trace_id"]
        # derived_from carries the source problem id.
        assert created.derived_from
        refs = {
            (inp.kind, inp.ref)
            for d in created.derived_from
            for inp in d.inputs
        }
        assert ("problem", "prob-1") in refs
        assert all(d.method == DERIVATION_METHOD for d in created.derived_from)

    @pytest.mark.asyncio
    async def test_derived_from_edge_and_extends_relation(
        self, agent, mock_llm, synthesis_report, state_with_evaluation
    ):
        """A DERIVED_FROM edge and an EXTENDS relation are written per problem."""
        mock_llm.structured_extract.return_value = SimpleNamespace(content=synthesis_report)

        await agent.run(state_with_evaluation)

        (created,), _ = agent.repo.create_problem.call_args
        agent.repo.create_derived_from.assert_any_call(
            created.id,
            "prob-1",
            method=DERIVATION_METHOD,
            workflow_run_id=state_with_evaluation["run_id"],
            trace_id=state_with_evaluation["trace_id"],
        )

        first_relation = agent.relations.create_relation.call_args_list[0]
        args, _kwargs = first_relation
        assert args[0] == "prob-1"
        assert args[1] == created.id
        assert args[2] is RelationType.EXTENDS

    @pytest.mark.asyncio
    async def test_new_relation_type_coerced_to_enum(
        self, agent, mock_llm, synthesis_report, state_with_evaluation
    ):
        """A report relation type string becomes a RelationType enum member."""
        mock_llm.structured_extract.return_value = SimpleNamespace(content=synthesis_report)

        await agent.run(state_with_evaluation)

        rel_calls = agent.relations.create_relation.call_args_list
        related_call = next(
            call for call in rel_calls if call.args[0] == "prob-1" and call.args[1] == "prob-2"
        )
        assert related_call.args[2] is RelationType.RELATED_TO

    @pytest.mark.asyncio
    async def test_unknown_relation_type_degrades_to_related_to(
        self, agent, mock_llm, state_with_evaluation
    ):
        report = SynthesisReport(
            summary="Investigation completed but the type was not understood.",
            new_problems=[],
            new_relations=[
                {"source_id": "prob-1", "target_id": "prob-9", "type": "MADE_UP"}
            ],
        )
        mock_llm.structured_extract.return_value = SimpleNamespace(content=report)

        await agent.run(state_with_evaluation)

        call = agent.relations.create_relation.call_args
        assert call.args[2] is RelationType.RELATED_TO

    @pytest.mark.asyncio
    async def test_run_missing_evaluation(self, agent, state_with_selected_problem):
        """Returns error when evaluation data is missing."""
        result = await agent.run(state_with_selected_problem)
        assert any("Missing evaluation or proposal" in e for e in result["errors"])

    @pytest.mark.asyncio
    async def test_run_missing_proposal(self, agent, initial_state):
        """Returns error when proposal is missing."""
        result = await agent.run(initial_state)
        assert any("Missing" in e for e in result["errors"])

    @pytest.mark.asyncio
    async def test_run_handles_llm_error(self, agent, mock_llm, state_with_evaluation):
        """Handles LLM failure gracefully."""
        mock_llm.structured_extract.side_effect = RuntimeError("LLM fail")

        result = await agent.run(state_with_evaluation)

        assert any("LLM fail" in e for e in result["errors"])

    @pytest.mark.asyncio
    async def test_run_updates_problem_status_on_promising(
        self, agent, mock_llm, mock_repo, synthesis_report, state_with_evaluation
    ):
        """Source problem status updated to in_progress when verdict is promising."""
        mock_llm.structured_extract.return_value = SimpleNamespace(content=synthesis_report)

        await agent.run(state_with_evaluation)

        mock_repo.set_problem_status.assert_called_once_with(
            "prob-1", ProblemStatus.IN_PROGRESS
        )

    @pytest.mark.asyncio
    async def test_run_no_relations_service(self, mock_llm, mock_repo, state_with_evaluation):
        """Works without relation service (no relation creation)."""
        agent = SynthesisAgent(
            llm_client=mock_llm,
            repository=mock_repo,
            search_service=None,
            relation_service=None,
        )
        report = SynthesisReport(
            summary="Investigation completed with no new relations to create in this case.",
            new_problems=["New problem discovered"],
            new_relations=[],
            source_problem_id="prob-1",
        )
        mock_llm.structured_extract.return_value = SimpleNamespace(content=report)

        result = await agent.run(state_with_evaluation)

        assert result["synthesis_report"] is not None
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_graph_update_failure_does_not_crash(
        self, agent, mock_llm, mock_repo, synthesis_report, state_with_evaluation
    ):
        """Failures in graph updates are logged but do not crash the agent."""
        mock_repo.create_problem.side_effect = RuntimeError("DB error")
        mock_llm.structured_extract.return_value = SimpleNamespace(content=synthesis_report)

        result = await agent.run(state_with_evaluation)

        # Should still complete (errors are logged as warnings, not raised)
        assert result["synthesis_report"] is not None
