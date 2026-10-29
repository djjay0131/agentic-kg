"""
Synthesis Agent.

Summarizes workflow outcomes, identifies new research directions, and writes
new problems and relations back to the knowledge graph.

On the canonical source (ADR-0010) it writes **nothing** directly: every
proposed change becomes a review-queue item, and only a human approval
re-enters the KGCS ``run_curation`` -> ``PlanExecutor`` path.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from agentic_kg.agents.base import BaseAgent
from agentic_kg.agents.graph_port import SOURCE_CANONICAL
from agentic_kg.agents.prompts import SYNTHESIS_SYSTEM_PROMPT, SYNTHESIS_USER_PROMPT
from agentic_kg.agents.provenance import (
    build_provenance,
    new_output_id,
    record_agent_output,
)
from agentic_kg.agents.schemas import (
    ContinuationProposal,
    EvaluationResult,
    GraphUpdate,
    SynthesisItem,
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
    """Synthesizes workflow results; writes legacy, queues canonical changes."""

    def __init__(
        self,
        *args: Any,
        canonical_sink: Any | None = None,
        namespace: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._canonical_sink = canonical_sink
        self._namespace = namespace

    @property
    def name(self) -> str:
        return "synthesis"

    async def run(self, state: ResearchState) -> ResearchState:
        """
        Summarize the workflow, identify new problems, and update the graph.
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
            state = add_message(state, self.name, "Missing evaluation or proposal data")
            return {
                **state,
                "errors": state.get("errors", []) + ["Missing evaluation or proposal data"],
            }

        try:
            proposal = ContinuationProposal.model_validate(proposal_data)
            eval_result = EvaluationResult.model_validate(eval_data)
            context = self.port.load_context(problem_id or proposal.problem_id)

            # Step 1: Generate synthesis report via LLM
            report = await self._generate_report(context, proposal, eval_result)
            state = add_message(
                state,
                self.name,
                f"Generated report: {len(report.new_problems)} new problems identified",
            )

            run_id = state.get("run_id") or ""
            trace_id = state.get("trace_id") or run_id
            evidence_ids = self._evidence_ids(context, proposal, eval_result)
            provenance = self._report_provenance(
                context=context,
                proposal=proposal,
                eval_result=eval_result,
                report=report,
                run_id=run_id,
                trace_id=trace_id,
            )
            report.output_id = new_output_id()
            report.provenance = provenance

            # Step 2: write back (legacy) or queue for approval (canonical)
            if self.source == SOURCE_CANONICAL:
                graph_updates = self._queue_canonical_updates(
                    report=report,
                    context=context,
                    run_id=run_id,
                    trace_id=trace_id,
                    evidence_ids=evidence_ids,
                )
                state = add_message(
                    state,
                    self.name,
                    f"Queued {len(report.review_item_ids)} review item(s); no "
                    "canonical write was made",
                )
            else:
                graph_updates = await self._apply_graph_updates(
                    context,
                    report,
                    eval_result,
                    run_id=run_id,
                    trace_id=trace_id,
                    provenance=provenance,
                )
                state = add_message(state, self.name, f"Applied {len(graph_updates)} graph updates")

            report.graph_updates = graph_updates
            payload = report.model_dump(mode="json", exclude={"provenance"})
            record_agent_output(
                self.output_store,
                agent=self.name,
                run_id=run_id,
                source=self.source,
                output_kind="synthesis_report",
                payload=payload,
                provenance=provenance,
                model=self.model,
                prompt_version=self.prompt_version,
                output_id=report.output_id,
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

    # -- provenance --------------------------------------------------------

    def _evidence_ids(self, context, proposal, eval_result) -> list[str]:
        ids = list(context.evidence_ids)
        for source in (proposal.provenance, eval_result.provenance):
            if source is None:
                continue
            ids.extend(source.used_evidence_ids)
            for citation in source.citations:
                ids.extend(citation.evidence_ids)
        return list(dict.fromkeys(i for i in ids if i))

    def _report_provenance(self, *, context, proposal, eval_result, report, run_id, trace_id):
        upstream = [oid for oid in (proposal.output_id, eval_result.output_id) if oid]
        return build_provenance(
            agent=self.name,
            run_id=run_id,
            trace_id=trace_id,
            source=self.source,
            assertion_ids=context.assertion_ids,
            assertion_evidence=context.assertion_evidence,
            evidence_ids=self._evidence_ids(context, proposal, eval_result),
            derived_from=upstream,
            problem_ids=[context.problem_id],
            model=self.model,
            prompt_version=self.prompt_version,
            rationale=report.summary,
        )

    # -- canonical path: queue, never write --------------------------------

    def _queue_canonical_updates(
        self, *, report, context, run_id, trace_id, evidence_ids
    ) -> list[GraphUpdate]:
        if self._canonical_sink is None:
            raise RuntimeError(
                "canonical synthesis requires a review-queue sink; refusing to "
                "fall back to a direct canonical write"
            )
        from agentic_kg.agents.canonical_write import (
            build_synthesis_review_items,
            graph_updates_from_items,
        )

        namespace = self._namespace or getattr(self._canonical_sink, "namespace", None)
        if not namespace:
            raise RuntimeError("canonical synthesis sink has no namespace")

        items, outputs = build_synthesis_review_items(
            report,
            namespace=namespace,
            run_id=run_id,
            trace_id=trace_id,
            source_problem_id=context.problem_id,
            model=self.model,
            prompt_version=self.prompt_version,
            output_id=report.output_id,
            provenance=report.provenance,
            evidence_ids=evidence_ids,
        )
        for item in items:
            self._canonical_sink.save(item)
        report.review_item_ids = [item.id for item in items]
        report.items = outputs
        return graph_updates_from_items(items, outputs)

    # -- report generation -------------------------------------------------

    async def _generate_report(
        self,
        context,
        proposal: ContinuationProposal,
        eval_result: EvaluationResult,
    ) -> SynthesisReport:
        """Use LLM to synthesize workflow results."""
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

        metrics_text = "None"
        if eval_result.metrics_results:
            metrics_text = "\n".join(
                f"  - {m.name}: {m.value}"
                + (f" (baseline: {m.baseline_value})" if m.baseline_value else "")
                + (f" [improvement: {m.improvement:.1%}]" if m.improvement else "")
                for m in eval_result.metrics_results
            )

        user_prompt = SYNTHESIS_USER_PROMPT.format(
            statement=context.statement,
            topic=context.topic,
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

    # -- legacy path (unchanged semantics) ---------------------------------

    @staticmethod
    def _build_derivations(source_problem: Any) -> list[Derivation]:
        """Build the ``Derivation`` lineage for problems synthesized from
        ``source_problem``: the source problem id plus any evidence DOI used.
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
        """Coerce an LLM-reported relation type into the KG enum."""
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
        provenance: Any | None = None,
    ) -> list[GraphUpdate]:
        """Write new problems and relations to the legacy graph.

        Every problem created here carries agent provenance (``origin``,
        ``workflow_run_id``, ``trace_id``, ``derived_from``) and a
        ``DERIVED_FROM`` edge to each source problem.
        """
        updates: list[GraphUpdate] = []
        # The legacy path deals in the repository's Problem model; the port's
        # context is a safe view of the same row.
        raw_problem = self.repo.get_problem(source_problem.problem_id)
        source_id = getattr(raw_problem, "id", None)
        derivations = self._build_derivations(raw_problem)
        items: list[SynthesisItem] = []

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
                items.append(
                    SynthesisItem(
                        action="create_problem",
                        target_id=new_id,
                        statement=statement,
                        provenance=provenance,
                    )
                )

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
                                f"Failed to create DERIVED_FROM edge {new_id} -> {inp.ref}: {e}"
                            )

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
                    items.append(
                        SynthesisItem(
                            action="create_relation",
                            target_id=str(rel["target_id"]),
                            relation_type=rel_type.value,
                            provenance=provenance,
                        )
                    )
            except Exception as e:
                logger.warning(f"Failed to create relation: {e}")

        if eval_result.verdict == "promising" and source_id:
            try:
                self.repo.set_problem_status(source_id, ProblemStatus.IN_PROGRESS)
                updates.append(
                    GraphUpdate(
                        action="update_status",
                        target_id=source_id,
                        details="Status updated to in_progress (promising evaluation)",
                    )
                )
                items.append(
                    SynthesisItem(
                        action="update_status",
                        target_id=source_id,
                        provenance=provenance,
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to update problem status: {e}")

        report.items = items
        return updates
