"""
Continuation Agent.

Given a selected research problem, proposes next experiments,
proofs, or algorithmic extensions.
"""

from __future__ import annotations

import logging
from typing import Any

from agentic_kg.agents.base import BaseAgent
from agentic_kg.agents.prompts import (
    CONTINUATION_SYSTEM_PROMPT,
    CONTINUATION_USER_PROMPT,
)
from agentic_kg.agents.schemas import ContinuationProposal, WorkflowStatus
from agentic_kg.agents.state import ResearchState, add_message

logger = logging.getLogger(__name__)


class ContinuationAgent(BaseAgent):
    """Proposes research continuations for a selected problem."""

    @property
    def name(self) -> str:
        return "continuation"

    async def run(self, state: ResearchState) -> ResearchState:
        """
        Load full problem context from KG, then generate a structured
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
            return {**state, "errors": state.get("errors", []) + ["No problem selected"]}

        try:
            # Load full problem context
            context = self._load_problem_context(problem_id)
            state = add_message(
                state, self.name, f"Loaded context for problem {problem_id}"
            )

            # Generate proposal
            proposal = await self._generate_proposal(context)
            state = add_message(
                state, self.name, f"Generated proposal: {proposal.title}"
            )

            return {
                **state,
                "proposal": proposal.model_dump(mode="json"),
                "proposal_approved": False,
            }

        except Exception as e:
            logger.error(f"Continuation failed: {e}")
            state = add_message(state, self.name, f"Error: {e}")
            return {**state, "errors": state.get("errors", []) + [str(e)]}

    def _load_problem_context(self, problem_id: str) -> dict:
        """Load full problem context from the KG."""
        problem = self.repo.get_problem(problem_id)

        context = {
            "id": problem.id,
            "statement": problem.statement,
            "domain": problem.domain or "unspecified",
            "status": problem.status.value if problem.status else "open",
            "constraints": [],
            "datasets": [],
            "baselines": [],
            "metrics": [],
            "related_problems": [],
        }

        # Constraints
        for c in problem.constraints or []:
            context["constraints"].append(f"[{c.type.value}] {c.text}")

        # Datasets
        for d in problem.datasets or []:
            avail = "available" if d.available else "unavailable"
            url = f" ({d.url})" if d.url else ""
            context["datasets"].append(f"{d.name} - {avail}{url}")

        # Baselines
        for b in problem.baselines or []:
            doi = f" (DOI: {b.paper_doi})" if b.paper_doi else ""
            context["baselines"].append(f"{b.name}{doi}")

        # Metrics
        for m in problem.metrics or []:
            baseline_val = f" (baseline: {m.baseline_value})" if m.baseline_value else ""
            desc = f" - {m.description}" if m.description else ""
            context["metrics"].append(f"{m.name}{desc}{baseline_val}")

        # Related problems
        if self.relations:
            try:
                related = self.relations.get_related_problems(
                    problem_id, direction="both", limit=10
                )
                for rel in related:
                    context["related_problems"].append(
                        f"[{rel.get('type', 'RELATED')}] {rel.get('statement', 'Unknown')}"
                    )
            except Exception as e:
                logger.warning(f"Could not load related problems: {e}")

        return context

    async def _generate_proposal(self, context: dict) -> ContinuationProposal:
        """Generate a structured proposal via LLM."""
        user_prompt = CONTINUATION_USER_PROMPT.format(
            statement=context["statement"],
            domain=context["domain"],
            status=context["status"],
            constraints="\n".join(context["constraints"]) or "None specified",
            datasets="\n".join(context["datasets"]) or "None specified",
            baselines="\n".join(context["baselines"]) or "None specified",
            metrics="\n".join(context["metrics"]) or "None specified",
            related_problems="\n".join(context["related_problems"]) or "None found",
        )

        response = await self.llm.structured_extract(
            response_model=ContinuationProposal,
            system_prompt=CONTINUATION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

        proposal = response.content
        # Ensure problem_id is set
        if not proposal.problem_id:
            proposal.problem_id = context["id"]
        return proposal
