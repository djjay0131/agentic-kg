"""
Continuation Agent.

Given a selected research problem, proposes next experiments,
proofs, or algorithmic extensions.
"""

from __future__ import annotations

import logging

from agentic_kg.agents.base import BaseAgent
from agentic_kg.agents.prompts import (
    CONTINUATION_SYSTEM_PROMPT,
    CONTINUATION_USER_PROMPT,
)
from agentic_kg.agents.provenance import build_provenance, record_agent_output
from agentic_kg.agents.schemas import ContinuationProposal, WorkflowStatus
from agentic_kg.agents.state import ResearchState, add_error, add_message

logger = logging.getLogger(__name__)


class ContinuationAgent(BaseAgent):
    """Proposes research continuations for a selected problem."""

    @property
    def name(self) -> str:
        return "continuation"

    async def run(self, state: ResearchState) -> ResearchState:
        """
        Load full problem context from the graph, then generate a structured
        continuation proposal via LLM.
        """
        self._log("Starting continuation proposal")
        state = {
            **state,
            "current_step": "continuation",
            "status": WorkflowStatus.RUNNING.value,
        }

        problem_id = state.get("selected_problem_id")
        if not problem_id:
            state = add_message(state, self.name, "No problem selected")
            return add_error(state, "No problem selected")

        try:
            context = self.port.load_context(problem_id)
            state = add_message(state, self.name, f"Loaded context for problem {problem_id}")

            proposal, state = await self._generate_proposal(context, state)
            # The selected problem id is authoritative. A model that writes its
            # own slug into ``problem_id`` must not become a foreign key the
            # evaluation agent then looks up (the staging failure was
            # "Problem not found: llm-hallucination-knowledge-graphs").
            self._bind_problem_id(proposal, problem_id)
            self._attach_provenance(
                proposal,
                context,
                state=state,
                problem_id=problem_id,
            )
            state = add_message(state, self.name, f"Generated proposal: {proposal.title}")

            return {
                **state,
                "proposal": proposal.model_dump(mode="json"),
                "proposal_approved": False,
            }

        except Exception as e:
            logger.error(f"Continuation failed: {e}")
            state = add_message(state, self.name, f"Error: {e}")
            return add_error(state, str(e))

    @staticmethod
    def _bind_problem_id(proposal: ContinuationProposal, problem_id: str) -> None:
        """Make the selected problem the proposal's only source id."""
        if proposal.problem_id and proposal.problem_id != problem_id:
            logger.warning(
                "Continuation proposal returned problem_id %r; binding to the "
                "selected problem %r",
                proposal.problem_id,
                problem_id,
            )
        proposal.problem_id = problem_id

    def _attach_provenance(self, proposal, context, *, state, problem_id) -> None:
        run_id = state.get("run_id") or ""
        trace_id = state.get("trace_id") or run_id
        upstream = self._upstream_output_ids(state, problem_id)
        provenance = build_provenance(
            agent=self.name,
            run_id=run_id,
            trace_id=trace_id,
            source=self.source,
            assertion_ids=context.assertion_ids,
            assertion_evidence=context.assertion_evidence,
            evidence_ids=context.evidence_ids,
            derived_from=upstream,
            problem_ids=[problem_id],
            model=self.model,
            prompt_version=self.prompt_version,
            rationale=proposal.rationale or proposal.methodology,
        )
        payload = proposal.model_dump(mode="json", exclude={"provenance"})
        proposal.output_id = record_agent_output(
            self.output_store,
            agent=self.name,
            run_id=run_id,
            source=self.source,
            output_kind="continuation_proposal",
            payload=payload,
            provenance=provenance,
            model=self.model,
            prompt_version=self.prompt_version,
        )
        proposal.provenance = provenance

    @staticmethod
    def _upstream_output_ids(state: ResearchState, problem_id: str) -> list[str]:
        for ranked in state.get("ranked_problems", []):
            if ranked.get("problem_id") == problem_id and ranked.get("output_id"):
                return [str(ranked["output_id"])]
        return []

    def _load_problem_context(self, problem_id: str) -> dict:
        """Backward-compatible dict view of the port's problem context."""
        context = self.port.load_context(problem_id)
        return {
            "id": context.problem_id,
            "statement": context.statement,
            "topic": context.topic,
            "status": context.status,
            "constraints": list(context.constraints),
            "datasets": [
                f"{d.name} - {'available' if d.available else 'unavailable'}"
                + (f" ({d.url})" if d.url else "")
                for d in context.datasets
            ],
            "baselines": list(context.baselines),
            "metrics": [
                f"{m.name}"
                + (f" - {m.description}" if m.description else "")
                + (f" (baseline: {m.baseline_value})" if m.baseline_value else "")
                for m in context.metrics
            ],
            "related_problems": list(context.related_problems),
        }

    async def _generate_proposal(
        self, context, state
    ) -> tuple[ContinuationProposal, ResearchState]:
        """Generate a structured proposal via LLM."""
        datasets = (
            "\n".join(
                f"{d.name} - {'available' if d.available else 'unavailable'}"
                + (f" ({d.url})" if d.url else "")
                for d in context.datasets
            )
            or "None specified"
        )
        metrics = (
            "\n".join(
                f"{m.name}"
                + (f" - {m.description}" if m.description else "")
                + (f" (baseline: {m.baseline_value})" if m.baseline_value else "")
                for m in context.metrics
            )
            or "None specified"
        )
        user_prompt = CONTINUATION_USER_PROMPT.format(
            statement=context.statement,
            topic=context.topic,
            status=context.status,
            constraints="\n".join(context.constraints) or "None specified",
            datasets=datasets,
            baselines="\n".join(context.baselines) or "None specified",
            metrics=metrics,
            related_problems="\n".join(context.related_problems) or "None found",
        )

        response = await self.llm.structured_extract(
            response_model=ContinuationProposal,
            system_prompt=CONTINUATION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=self.max_tokens,
            call_site="continuation.proposal",
        )
        state = self._record_llm_usage(
            state, "continuation.proposal", response, budget=self.max_tokens
        )

        proposal = response.content
        # Bind after parsing as well, so a caller that uses the return value
        # directly cannot observe a model-authored id.
        self._bind_problem_id(proposal, context.problem_id)
        return proposal, state
