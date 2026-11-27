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
from agentic_kg.agents.prompts import (
    RANKING_BATCH_SYSTEM_PROMPT,
    RANKING_BATCH_USER_PROMPT,
)
from agentic_kg.agents.provenance import build_provenance, record_agent_output
from agentic_kg.agents.schemas import (
    RankedProblem,
    RankingBatchResult,
    RankingResult,
    WorkflowStatus,
)
from agentic_kg.agents.state import ResearchState, add_error, add_message
from agentic_kg.llm_config import resolve_ranking_batch_size

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
            wanted = set(state.get("candidate_problem_ids") or [])
            if wanted:
                # A cycle selected these candidates; keep the port query as the
                # source of truth and filter to the ids it asked for.
                candidates = [c for c in candidates if c.problem_id in wanted]
            if not candidates:
                state = add_message(state, self.name, "No candidate problems found")
                return {**state, "ranked_problems": [], "total_candidates": 0}

            state = add_message(state, self.name, f"Found {len(candidates)} candidate problems")

            # Ask LLM to rank in bounded batches. The output size is a function
            # of the batch, never of the candidate list, so a long list cannot
            # exhaust the completion budget.
            result, state = await self._rank_with_llm(
                candidates, state.get("topic_filter"), state
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
                "total_candidates": len(candidates),
            }

        except Exception as e:
            logger.error(f"Ranking failed: {e}")
            state = add_message(state, self.name, f"Error: {e}")
            return add_error(state, str(e))

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
        self,
        candidates: list[GraphProblem],
        topic: str | None,
        state: ResearchState,
        *,
        batch_size: int | None = None,
    ) -> tuple[RankingResult, ResearchState]:
        """Score candidates in bounded batches and merge the results.

        A single call over a growing candidate list is what truncated staging:
        the visible output plus reasoning tokens outgrew the completion budget.
        Each batch asks only for ``(id, score, short rationale)`` and the caller
        expands it against the candidate it already holds, so the response is
        bounded by ``batch_size``.
        """
        size = (
            batch_size
            if batch_size is not None
            else resolve_ranking_batch_size()
        )
        by_id = {c.problem_id: c for c in candidates}
        merged: dict[str, RankedProblem] = {}
        unscored: list[str] = []

        for offset in range(0, len(candidates), size):
            batch = candidates[offset : offset + size]
            batch_number = offset // size + 1
            call_site = f"ranking.batch[{batch_number}]"
            problems_text = format_problems(batch)
            user_prompt = RANKING_BATCH_USER_PROMPT.format(
                count=len(batch),
                topic=topic or "all topics",
                problems_text=problems_text,
            )
            response = await self.llm.structured_extract(
                response_model=RankingBatchResult,
                system_prompt=RANKING_BATCH_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=self.max_tokens,
                call_site=call_site,
            )
            state = self._record_llm_usage(
                state, call_site, response, budget=self.max_tokens
            )
            state = add_message(
                state,
                self.name,
                f"Scored batch {batch_number} ({len(batch)} candidates)",
            )

            batch_ids = {c.problem_id for c in batch}
            returned: set[str] = set()
            for scored in response.content.scores:
                candidate = by_id.get(scored.problem_id)
                if candidate is None or scored.problem_id not in batch_ids:
                    logger.warning(
                        "Ranking batch returned unknown problem id %r; ignoring",
                        scored.problem_id,
                    )
                    continue
                returned.add(scored.problem_id)
                existing = merged.get(scored.problem_id)
                if existing is not None and existing.score >= scored.score:
                    continue
                merged[scored.problem_id] = RankedProblem(
                    problem_id=candidate.problem_id,
                    statement=candidate.statement,
                    score=scored.score,
                    # The compact batch schema reports one overall score; the
                    # historical per-dimension fields are informational.
                    tractability=scored.score,
                    data_availability=scored.score,
                    cross_domain_impact=scored.score,
                    rationale=scored.rationale,
                )
            unscored.extend(
                c.problem_id for c in batch if c.problem_id not in returned
            )

        ranked = sorted(merged.values(), key=lambda item: item.score, reverse=True)
        if unscored:
            logger.warning(
                "Ranking returned no score for %d candidate(s): %s",
                len(unscored),
                ", ".join(unscored[:10]),
            )
            state = add_message(
                state,
                self.name,
                f"No score returned for {len(unscored)} candidate(s)",
            )

        batch_count = len(range(0, len(candidates), size))
        return (
            RankingResult(
                ranked_problems=ranked,
                query_summary=(
                    f"{len(candidates)} candidates in {batch_count} batch(es)"
                ),
                total_candidates=len(candidates),
            ),
            state,
        )
