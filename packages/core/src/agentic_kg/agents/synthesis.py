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
from agentic_kg.knowledge_graph.models import (
    Derivation,
    DerivationInput,
    Problem,
    ProblemStatus,
    RelationType,
)

logger = logging.getLogger(__name__)

# Mark agent-invented problems so they are never mistaken for extracted ones
# (the citation-laundering failure mode). See issue #114 and the KGPS
# provenance audit.
AGENT_ORIGIN = "agent:synthesis"
DERIVATION_METHOD = "synthesis"
DERIVATION_IMPLEMENTATION_VERSION = "1.0.0"
_DEFAULT_RELATION_TYPE = RelationType.RELATED_TO
_EXTENDS_CONFIDENCE = 0.8


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
                problem,
                report,
                eval_result,
                run_id=state.get("run_id"),
                trace_id=state.get("trace_id") or state.get("run_id"),
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
            topic="unspecified",
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

    @staticmethod
    def _build_derivations(source_problem: Any) -> list[Derivation]:
        """Build the ``Derivation`` lineage for problems synthesized from
        ``source_problem``: the source problem id plus any evidence DOI used.

        Shaped like ``kg_contracts.Derivation`` so it maps onto KGIS/KGCS
        once the adopter layer lands.
        """
        inputs: list[DerivationInput] = []
        source_id = getattr(source_problem, "id", None)
        if isinstance(source_id, str) and source_id:
            inputs.append(DerivationInput(kind="problem", ref=source_id))

        evidence = getattr(source_problem, "evidence", None)
        doi = getattr(evidence, "source_doi", None) if evidence is not None else None
        if isinstance(doi, str) and doi:
            inputs.append(DerivationInput(kind="paper", ref=doi))

        if not inputs:
            return []
        return [
            Derivation(
                method=DERIVATION_METHOD,
                inputs=inputs,
                implementation_version=DERIVATION_IMPLEMENTATION_VERSION,
            )
        ]

    @staticmethod
    def _coerce_relation_type(value: Any) -> RelationType:
        """Coerce an LLM-reported relation type into the KG enum.

        Unknown types degrade to ``RELATED_TO`` rather than raising, so a
        single bad relation never drops the rest of the report.
        """
        if isinstance(value, RelationType):
            return value
        if not value:
            return _DEFAULT_RELATION_TYPE
        try:
            return RelationType(str(value).upper())
        except ValueError:
            logger.warning(
                "Unknown relation type %r from synthesis report; using RELATED_TO",
                value,
            )
            return _DEFAULT_RELATION_TYPE

    async def _apply_graph_updates(
        self,
        source_problem: Any,
        report: SynthesisReport,
        eval_result: EvaluationResult,
        *,
        run_id: str | None = None,
        trace_id: str | None = None,
    ) -> list[GraphUpdate]:
        """Write new problems and relations to the KG.

        Every problem created here carries agent provenance (``origin``,
        ``workflow_run_id``, ``trace_id``, ``derived_from``) and a
        ``DERIVED_FROM`` edge to each source problem, so it is never mistaken
        for an extracted problem.
        """
        updates: list[GraphUpdate] = []
        source_id = getattr(source_problem, "id", None)
        derivations = self._build_derivations(source_problem)

        # Create new problem nodes
        for statement in report.new_problems:
            try:
                new_id = f"synth-{uuid.uuid4().hex[:12]}"
                new_problem = Problem(
                    id=new_id,
                    statement=statement,
                    status=ProblemStatus.OPEN,
                    origin=AGENT_ORIGIN,
                    workflow_run_id=run_id,
                    trace_id=trace_id,
                    derived_from=derivations,
                )
                self.repo.create_problem(new_problem)
                updates.append(
                    GraphUpdate(
                        action="create_problem",
                        target_id=new_id,
                        details=f"New problem: {statement[:80]}",
                    )
                )

                # Provenance: DERIVED_FROM edges to each source problem.
                for derivation in derivations:
                    for inp in derivation.inputs:
                        if inp.kind != "problem":
                            continue
                        try:
                            self.repo.create_derived_from(
                                new_id,
                                inp.ref,
                                method=derivation.method,
                                workflow_run_id=run_id,
                                trace_id=trace_id,
                            )
                        except Exception as e:
                            logger.warning(
                                f"Failed to create DERIVED_FROM edge "
                                f"{new_id} -> {inp.ref}: {e}"
                            )

                # Create relation from source problem
                if self.relations and source_id:
                    self.relations.create_relation(
                        source_id,
                        new_id,
                        RelationType.EXTENDS,
                        confidence=_EXTENDS_CONFIDENCE,
                        metadata={
                            "origin": AGENT_ORIGIN,
                            "workflow_run_id": run_id,
                            "trace_id": trace_id,
                        },
                    )
                    updates.append(
                        GraphUpdate(
                            action="create_relation",
                            target_id=new_id,
                            details=f"EXTENDS from {source_id}",
                        )
                    )
            except Exception as e:
                logger.warning(f"Failed to create problem '{statement[:50]}': {e}")

        # Create any additional relations from the report
        for rel in report.new_relations:
            try:
                if self.relations and rel.get("source_id") and rel.get("target_id"):
                    rel_type = self._coerce_relation_type(rel.get("type"))
                    self.relations.create_relation(
                        rel["source_id"],
                        rel["target_id"],
                        rel_type,
                        metadata={
                            "origin": AGENT_ORIGIN,
                            "workflow_run_id": run_id,
                            "trace_id": trace_id,
                        },
                    )
                    updates.append(
                        GraphUpdate(
                            action="create_relation",
                            target_id=rel["target_id"],
                            details=f"{rel_type.value} from {rel['source_id']}",
                        )
                    )
            except Exception as e:
                logger.warning(f"Failed to create relation: {e}")

        # Update source problem status if evaluation was conclusive. The
        # source may be a legacy :Problem or a canonical :ProblemConcept, so
        # the repository resolves the label.
        if eval_result.verdict == "promising" and source_id:
            try:
                self.repo.set_problem_status(
                    source_id, ProblemStatus.IN_PROGRESS
                )
                updates.append(
                    GraphUpdate(
                        action="update_status",
                        target_id=source_id,
                        details="Status updated to in_progress (promising evaluation)",
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to update problem status: {e}")

        return updates
