"""
Ranking Agent.

Prioritizes extracted research problems by tractability,
data availability, and cross-domain impact.
"""

from __future__ import annotations

import logging

from agentic_kg.agents.base import BaseAgent
from agentic_kg.agents.graph_port import (
    SOURCE_CANONICAL,
    GraphProblem,
    blend_canonical_score,
    format_problems,
)
from agentic_kg.agents.prompts import RANKING_SYSTEM_PROMPT, RANKING_USER_PROMPT
from agentic_kg.agents.provenance import build_provenance, record_agent_output
from agentic_kg.agents.schemas import RankedProblem, RankingResult, WorkflowStatus
from agentic_kg.agents.state import ResearchState, add_message

logger = logging.getLogger(__name__)


class RankingAgent(BaseAgent):
    """Ranks research problems by tractability, data availability, and impact."""

    @property
    def name(self) -> str:
        return "ranking"

    async def run(self, state: ResearchState) -> ResearchState:
        """
        Query the graph for candidate problems, then use LLM
        to score and rank them.
        """
        self._log("Starting problem ranking")
        state = {
            **state,
            "current_step": "ranking",
            "status": WorkflowStatus.RUNNING.value,
        }

        try:
            # Gather candidate problems from the graph port
            candidates = self.port.list_candidates(
                topic_filter=state.get("topic_filter"),
                status_filter=state.get("status_filter", "open"),
                max_problems=state.get("max_problems", 20),
            )
            if not candidates:
                state = add_message(state, self.name, "No candidate problems found")
                return {**state, "ranked_problems": [], "total_candidates": 0}

            state = add_message(state, self.name, f"Found {len(candidates)} candidate problems")

            problems_text = format_problems(candidates)

            # Ask LLM to rank
            result = await self._rank_with_llm(
                problems_text, len(candidates), state.get("topic_filter")
            )

            ranked = self._attach_graph_features(
                result.ranked_problems,
                candidates,
                run_id=state.get("run_id") or "",
                trace_id=state.get("trace_id") or state.get("run_id") or "",
            )
            state = add_message(state, self.name, f"Ranked {len(ranked)} problems")

            return {
                **state,
                "ranked_problems": [rp.model_dump(mode="json") for rp in ranked],
                "total_candidates": result.total_candidates or len(candidates),
            }

        except Exception as e:
            logger.error(f"Ranking failed: {e}")
            state = add_message(state, self.name, f"Error: {e}")
            return {**state, "errors": state.get("errors", []) + [str(e)]}

    def _attach_graph_features(
        self,
        ranked: list[RankedProblem],
        candidates: list[GraphProblem],
        *,
        run_id: str,
        trace_id: str,
    ) -> list[RankedProblem]:
        """Attach canonical features, provenance, and blend the score.

        On the legacy source the candidate carries no graph-native facts, so
        the historical LLM score is left untouched and the provenance envelope
        abstains (it honestly has no canonical assertion ids). On the canonical
        source the deterministic graph score is blended in and every output
        cites the assertions/evidence the problem rests on.
        """
        by_id = {c.problem_id: c for c in candidates}
        enriched: list[RankedProblem] = []
        for rp in ranked:
            candidate = by_id.get(rp.problem_id)
            if candidate is not None and candidate.graph_native_score is not None:
                rp.graph_native_score = candidate.graph_native_score
                rp.problem_age_days = candidate.age_days
                rp.evidence_strength = candidate.evidence_strength
                rp.connectivity = candidate.connectivity
                rp.dataset_count = candidate.dataset_count
                rp.metric_count = candidate.metric_count
                rp.score = blend_canonical_score(rp.score, candidate.graph_native_score)

            provenance = build_provenance(
                agent=self.name,
                run_id=run_id,
                trace_id=trace_id,
                source=self.source,
                assertion_ids=(candidate.assertion_ids if candidate else []),
                assertion_evidence=(candidate.assertion_evidence if candidate else {}),
                evidence_ids=(candidate.evidence_ids if candidate else []),
                problem_ids=[rp.problem_id],
                model=self.model,
                prompt_version=self.prompt_version,
                rationale=rp.rationale,
            )
            payload = rp.model_dump(mode="json", exclude={"provenance"})
            rp.output_id = record_agent_output(
                self.output_store,
                agent=self.name,
                run_id=run_id,
                source=self.source,
                output_kind="ranked_problem",
                payload=payload,
                provenance=provenance,
                model=self.model,
                prompt_version=self.prompt_version,
            )
            rp.provenance = provenance
            enriched.append(rp)

        if self.source == SOURCE_CANONICAL:
            enriched.sort(key=lambda item: item.score, reverse=True)
        return enriched

    @staticmethod
    def _format_problems(candidates: list[dict]) -> str:
        """Backward-compatible formatter for callers passing plain dicts."""
        lines = []
        for i, candidate in enumerate(candidates, 1):
            problem_id = candidate.get("id") or candidate.get("problem_id")
            lines.append(
                f"{i}. (ID: {problem_id})\n"
                f"   Statement: {candidate.get('statement')}\n"
                f"   Status: {candidate.get('status', 'open')}"
            )
        return "\n\n".join(lines)

    async def _rank_with_llm(
        self, problems_text: str, count: int, topic: str | None
    ) -> RankingResult:
        """Use LLM to score and rank problems."""
        user_prompt = RANKING_USER_PROMPT.format(
            count=count,
            topic=topic or "all topics",
            problems_text=problems_text,
        )

        response = await self.llm.structured_extract(
            response_model=RankingResult,
            system_prompt=RANKING_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        return response.content
