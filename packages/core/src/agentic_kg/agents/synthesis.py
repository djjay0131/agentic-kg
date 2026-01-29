"""
Synthesis Agent.

Summarizes workflow outcomes, identifies new research directions,
and writes new problems and relations back to the knowledge graph.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from agentic_kg.agents.base import BaseAgent
from agentic_kg.agents.prompts import SYNTHESIS_SYSTEM_PROMPT, SYNTHESIS_USER_PROMPT
from agentic_kg.agents.schemas import (
    ContinuationProposal,
    EvaluationResult,
    GraphUpdate,
    SynthesisReport,
    WorkflowStatus,
)
from agentic_kg.agents.state import ResearchState, add_message

logger = logging.getLogger(__name__)


class SynthesisAgent(BaseAgent):
    """Synthesizes workflow results and writes new discoveries back to the KG."""

    @property
    def name(self) -> str:
        return "synthesis"

    async def run(self, state: ResearchState) -> ResearchState:
        """
        Summarize the workflow, identify new problems, and update the KG.
        """
        self._log("Starting synthesis")
        state = {
            **state,
            "current_step": "synthesis",
            "status": WorkflowStatus.RUNNING.value,
        }

        eval_data = state.get("evaluation_result")
        proposal_data = state.get("proposal")
        problem_id = state.get("selected_problem_id")

        if not eval_data or not proposal_data:
            state = add_message(
                state, self.name, "Missing evaluation or proposal data"
            )
            return {
                **state,
                "errors": state.get("errors", [])
                + ["Missing evaluation or proposal data"],
            }

        try:
            proposal = ContinuationProposal.model_validate(proposal_data)
            eval_result = EvaluationResult.model_validate(eval_data)
            problem = self.repo.get_problem(problem_id or proposal.problem_id)

            # Step 1: Generate synthesis report via LLM
            report = await self._generate_report(problem, proposal, eval_result)
            state = add_message(
                state,
                self.name,
                f"Generated report: {len(report.new_problems)} new problems identified",
            )

            # Step 2: Write new problems to KG
            graph_updates = await self._apply_graph_updates(
                problem, report, eval_result
            )
            report.graph_updates = graph_updates
            state = add_message(
                state, self.name, f"Applied {len(graph_updates)} graph updates"
            )

            return {
                **state,
                "synthesis_report": report.model_dump(mode="json"),
                "status": WorkflowStatus.COMPLETED.value,
            }

        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            state = add_message(state, self.name, f"Error: {e}")
            return {**state, "errors": state.get("errors", []) + [str(e)]}

    async def _generate_report(
        self,
        problem: Any,
        proposal: ContinuationProposal,
        eval_result: EvaluationResult,
    ) -> SynthesisReport:
        """Use LLM to synthesize workflow results."""
        # Format proposal summary
        steps_text = "\n".join(
            f"  {s.step_number}. {s.description}" for s in proposal.experimental_steps
        )
        proposal_summary = (
            f"Title: {proposal.title}\n"
            f"Methodology: {proposal.methodology}\n"
            f"Expected Outcome: {proposal.expected_outcome}\n"
            f"Steps:\n{steps_text}\n"
            f"Confidence: {proposal.confidence}"
        )

        # Format metrics results
        metrics_text = "None"
        if eval_result.metrics_results:
            metrics_text = "\n".join(
                f"  - {m.name}: {m.value}"
                + (f" (baseline: {m.baseline_value})" if m.baseline_value else "")
                + (f" [improvement: {m.improvement:.1%}]" if m.improvement else "")
                for m in eval_result.metrics_results
            )

        user_prompt = SYNTHESIS_USER_PROMPT.format(
            statement=problem.statement,
            domain=problem.domain or "unspecified",
            proposal_summary=proposal_summary,
            feasibility_score=eval_result.feasibility_score,
            verdict=eval_result.verdict,
            metrics_results=metrics_text,
            execution_output=(eval_result.execution_output or "")[:2000],
            limitations=", ".join(eval_result.limitations) or "None noted",
        )

        response = await self.llm.structured_extract(
            response_model=SynthesisReport,
            system_prompt=SYNTHESIS_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        return response.content

    async def _apply_graph_updates(
        self,
        source_problem: Any,
        report: SynthesisReport,
        eval_result: EvaluationResult,
    ) -> list[GraphUpdate]:
        """Write new problems and relations to the KG."""
        updates: list[GraphUpdate] = []

        # Create new problem nodes
        for statement in report.new_problems:
            try:
                new_id = f"synth-{uuid.uuid4().hex[:12]}"
                self.repo.create_problem(
                    id=new_id,
                    statement=statement,
                    domain=source_problem.domain,
                    status="open",
                )
                updates.append(
                    GraphUpdate(
                        action="create_problem",
                        target_id=new_id,
                        details=f"New problem: {statement[:80]}",
                    )
                )

                # Create relation from source problem
                if self.relations:
                    self.relations.create_relation(
                        source_id=source_problem.id,
                        target_id=new_id,
                        relation_type="EXTENDS",
                    )
                    updates.append(
                        GraphUpdate(
                            action="create_relation",
                            target_id=new_id,
                            details=f"EXTENDS from {source_problem.id}",
                        )
                    )
            except Exception as e:
                logger.warning(f"Failed to create problem '{statement[:50]}': {e}")

        # Create any additional relations from the report
        for rel in report.new_relations:
            try:
                if self.relations and rel.get("source_id") and rel.get("target_id"):
                    self.relations.create_relation(
                        source_id=rel["source_id"],
                        target_id=rel["target_id"],
                        relation_type=rel.get("type", "RELATED_TO"),
                    )
                    updates.append(
                        GraphUpdate(
                            action="create_relation",
                            target_id=rel["target_id"],
                            details=f"{rel.get('type', 'RELATED_TO')} from {rel['source_id']}",
                        )
                    )
            except Exception as e:
                logger.warning(f"Failed to create relation: {e}")

        # Update source problem status if evaluation was conclusive
        if eval_result.verdict == "promising":
            try:
                self.repo.update_problem(
                    source_problem.id, status="in_progress"
                )
                updates.append(
                    GraphUpdate(
                        action="update_status",
                        target_id=source_problem.id,
                        details="Status updated to in_progress (promising evaluation)",
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to update problem status: {e}")

        return updates
