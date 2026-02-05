"""
Ranking Agent.

Prioritizes extracted research problems by tractability,
data availability, and cross-domain impact.
"""

from __future__ import annotations

import logging
from typing import Any

from agentic_kg.agents.base import BaseAgent
from agentic_kg.agents.prompts import RANKING_SYSTEM_PROMPT, RANKING_USER_PROMPT
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
        Query the knowledge graph for candidate problems, then use LLM
        to score and rank them.
        """
        self._log("Starting problem ranking")
        state = {
            **state,
            "current_step": "ranking",
            "status": WorkflowStatus.RUNNING.value,
        }

        try:
            # Gather candidate problems from the KG
            candidates = self._query_candidates(state)
            if not candidates:
                state = add_message(state, self.name, "No candidate problems found")
                return {**state, "ranked_problems": [], "total_candidates": 0}

            state = add_message(
                state, self.name, f"Found {len(candidates)} candidate problems"
            )

            # Format problems for the LLM
            problems_text = self._format_problems(candidates)

            # Ask LLM to rank
            result = await self._rank_with_llm(
                problems_text, len(candidates), state.get("domain_filter")
            )

            ranked = [rp.model_dump() for rp in result.ranked_problems]
            state = add_message(
                state, self.name, f"Ranked {len(ranked)} problems"
            )

            return {
                **state,
                "ranked_problems": ranked,
                "total_candidates": result.total_candidates or len(candidates),
            }

        except Exception as e:
            logger.error(f"Ranking failed: {e}")
            state = add_message(state, self.name, f"Error: {e}")
            return {**state, "errors": state.get("errors", []) + [str(e)]}

    def _query_candidates(self, state: ResearchState) -> list[dict]:
        """Query KG for candidate problems matching filters."""
        domain = state.get("domain_filter")
        status_filter = state.get("status_filter", "open")
        max_problems = state.get("max_problems", 20)

        if self.search:
            # Use hybrid search if a domain is specified
            if domain:
                results = self.search.structured_search(
                    domain=domain,
                    status=status_filter,
                    top_k=max_problems,
                )
            else:
                results = self.search.structured_search(
                    status=status_filter,
                    top_k=max_problems,
                )
            return [
                {
                    "id": r.problem.id,
                    "statement": r.problem.statement,
                    "domain": r.problem.domain,
                    "status": r.problem.status.value if r.problem.status else "open",
                    "confidence": getattr(
                        r.problem, "extraction_metadata", None
                    )
                    and r.problem.extraction_metadata.confidence_score,
                }
                for r in results
            ]
        else:
            # Fallback: list problems directly from repo
            problems = self.repo.list_problems(
                status=status_filter,
                domain=domain,
                limit=max_problems,
            )
            return [
                {
                    "id": p.id,
                    "statement": p.statement,
                    "domain": p.domain,
                    "status": p.status.value if p.status else "open",
                    "confidence": getattr(p, "extraction_metadata", None)
                    and p.extraction_metadata.confidence_score,
                }
                for p in problems
            ]

    def _format_problems(self, candidates: list[dict]) -> str:
        """Format problems as numbered text for the LLM."""
        lines = []
        for i, c in enumerate(candidates, 1):
            domain_str = f" [{c.get('domain', 'unknown')}]" if c.get("domain") else ""
            lines.append(
                f"{i}. (ID: {c['id']}){domain_str}\n"
                f"   Statement: {c['statement']}\n"
                f"   Status: {c.get('status', 'open')}"
            )
        return "\n\n".join(lines)

    async def _rank_with_llm(
        self, problems_text: str, count: int, domain: str | None
    ) -> RankingResult:
        """Use LLM to score and rank problems."""
        user_prompt = RANKING_USER_PROMPT.format(
            count=count,
            domain=domain or "all domains",
            problems_text=problems_text,
        )

        response = await self.llm.structured_extract(
            response_model=RankingResult,
            system_prompt=RANKING_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        return response.content
