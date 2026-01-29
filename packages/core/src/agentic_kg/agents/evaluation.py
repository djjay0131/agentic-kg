"""
Evaluation Agent.

Assesses feasibility of continuation proposals and executes
evaluation code in a sandboxed Docker environment.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agentic_kg.agents.base import BaseAgent
from agentic_kg.agents.prompts import (
    EVALUATION_CODE_PROMPT,
    EVALUATION_SYSTEM_PROMPT,
)
from agentic_kg.agents.sandbox import DockerSandbox, SandboxResult
from agentic_kg.agents.schemas import (
    ContinuationProposal,
    EvaluationResult,
    MetricResult,
    WorkflowStatus,
)
from agentic_kg.agents.state import ResearchState, add_message

logger = logging.getLogger(__name__)


class EvaluationAgent(BaseAgent):
    """Evaluates continuation proposals via LLM analysis and sandboxed code execution."""

    def __init__(self, *args, sandbox: DockerSandbox | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._sandbox = sandbox

    @property
    def name(self) -> str:
        return "evaluation"

    @property
    def sandbox(self) -> DockerSandbox:
        if self._sandbox is None:
            self._sandbox = DockerSandbox()
        return self._sandbox

    async def run(self, state: ResearchState) -> ResearchState:
        """
        Assess feasibility of the proposal, generate evaluation code,
        and run it in a sandboxed container.
        """
        self._log("Starting evaluation")
        state = {
            **state,
            "current_step": "evaluation",
            "status": WorkflowStatus.RUNNING.value,
        }

        proposal_data = state.get("proposal")
        if not proposal_data:
            state = add_message(state, self.name, "No proposal to evaluate")
            return {**state, "errors": state.get("errors", []) + ["No proposal"]}

        try:
            proposal = ContinuationProposal.model_validate(proposal_data)

            # Load problem context for code generation
            problem = self.repo.get_problem(proposal.problem_id)

            # Step 1: Generate evaluation code
            code = await self._generate_code(proposal, problem)
            state = add_message(
                state, self.name, f"Generated evaluation code ({len(code)} chars)"
            )

            # Step 2: Execute in sandbox
            sandbox_result = self._execute_code(code)
            state = add_message(
                state,
                self.name,
                f"Sandbox execution: {'success' if sandbox_result.success else 'failed'} "
                f"(exit code {sandbox_result.exit_code})",
            )

            # Step 3: Interpret results with LLM
            eval_result = await self._interpret_results(
                proposal, sandbox_result, problem
            )
            state = add_message(
                state, self.name, f"Verdict: {eval_result.verdict}"
            )

            return {
                **state,
                "evaluation_result": eval_result.model_dump(mode="json"),
                "evaluation_approved": False,
            }

        except Exception as e:
            logger.error(f"Evaluation failed: {e}")
            state = add_message(state, self.name, f"Error: {e}")
            return {**state, "errors": state.get("errors", []) + [str(e)]}

    async def _generate_code(self, proposal: ContinuationProposal, problem: Any) -> str:
        """Generate Python evaluation script via LLM."""
        steps_text = "\n".join(
            f"  {s.step_number}. {s.description}" for s in proposal.experimental_steps
        )
        datasets_text = "\n".join(
            f"  - {d.name}" for d in (problem.datasets or [])
        ) or "None specified"
        metrics_text = "\n".join(
            f"  - {m.name}" for m in (problem.metrics or [])
        ) or "None specified"

        user_prompt = EVALUATION_CODE_PROMPT.format(
            statement=problem.statement,
            methodology=proposal.methodology,
            expected_outcome=proposal.expected_outcome,
            metrics=metrics_text,
            datasets=datasets_text,
            steps=steps_text or "None specified",
        )

        response = await self.llm.extract(
            system_prompt=EVALUATION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

        # Extract code block from response
        code = response.content
        if isinstance(code, str):
            # Strip markdown code fences if present
            if "```python" in code:
                code = code.split("```python", 1)[1]
                code = code.split("```", 1)[0]
            elif "```" in code:
                code = code.split("```", 1)[1]
                code = code.split("```", 1)[0]
        return code.strip()

    def _execute_code(self, code: str) -> SandboxResult:
        """Execute code in the Docker sandbox."""
        return self.sandbox.execute(code)

    async def _interpret_results(
        self,
        proposal: ContinuationProposal,
        sandbox_result: SandboxResult,
        problem: Any,
    ) -> EvaluationResult:
        """Use LLM to interpret sandbox results and produce final evaluation."""
        metrics_data = sandbox_result.parse_metrics()
        metrics_results = []

        if metrics_data.get("metrics"):
            for name, value in metrics_data["metrics"].items():
                # Try to find baseline for this metric
                baseline = None
                for m in problem.metrics or []:
                    if m.name.lower() == name.lower():
                        baseline = m.baseline_value
                        break

                improvement = None
                if baseline and value and baseline != 0:
                    improvement = (value - baseline) / abs(baseline)

                metrics_results.append(
                    MetricResult(
                        name=name,
                        value=value,
                        baseline_value=baseline,
                        improvement=improvement,
                    )
                )

        # Determine verdict based on execution
        if sandbox_result.timed_out:
            verdict = "not_viable"
            feasibility = 0.1
        elif not sandbox_result.success:
            verdict = "inconclusive"
            feasibility = 0.3
        elif metrics_results and any(
            r.improvement and r.improvement > 0 for r in metrics_results
        ):
            verdict = "promising"
            feasibility = 0.8
        else:
            verdict = "inconclusive"
            feasibility = 0.5

        limitations = []
        if sandbox_result.timed_out:
            limitations.append("Execution timed out")
        if not sandbox_result.success:
            limitations.append(f"Execution error: {sandbox_result.stderr[:200]}")
        if not metrics_data:
            limitations.append("No structured metrics output produced")

        return EvaluationResult(
            proposal_id=proposal.problem_id,
            feasibility_score=feasibility,
            code_generated=sandbox_result.stdout[:5000] if not sandbox_result.success else "",
            execution_output=sandbox_result.stdout[:5000],
            execution_success=sandbox_result.success,
            metrics_results=metrics_results,
            limitations=limitations,
            verdict=verdict,
        )
