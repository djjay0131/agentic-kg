"""
Base agent interface.

All research agents extend BaseAgent and implement the run() method.
Each agent receives shared dependencies via constructor injection.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional

from agentic_kg.agents.graph_port import (
    SOURCE_LEGACY,
    GraphPort,
    LegacyGraphPort,
)
from agentic_kg.agents.state import ResearchState, add_message

if TYPE_CHECKING:
    from agentic_kg.agents.provenance import AgentOutputStore
    from agentic_kg.extraction.llm_client import BaseLLMClient
    from agentic_kg.knowledge_graph.relations import RelationService
    from agentic_kg.knowledge_graph.repository import Neo4jRepository
    from agentic_kg.knowledge_graph.search import SearchService

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """
    Abstract base for all research workflow agents.

    Each agent receives injectable dependencies for KG access and LLM calls.
    The run() method takes the shared ResearchState, performs its work,
    and returns the updated state.

    ``graph_port`` is the graph the agent reads (ADR-0010). When it is not
    supplied, a :class:`LegacyGraphPort` wraps the historical repository/search
    /relation dependencies, so existing call sites keep their exact behaviour.
    """

    def __init__(
        self,
        llm_client: BaseLLMClient,
        repository: Neo4jRepository,
        search_service: SearchService | None = None,
        relation_service: RelationService | None = None,
        *,
        graph_port: GraphPort | None = None,
        output_store: AgentOutputStore | None = None,
        source: str = SOURCE_LEGACY,
        model: str | None = None,
        prompt_version: str = "",
        max_tokens: int | None = None,
    ) -> None:
        self.llm = llm_client
        self.repo = repository
        self.search = search_service
        self.relations = relation_service
        self.port: GraphPort = graph_port or LegacyGraphPort(
            repository, search_service, relation_service
        )
        self.output_store = output_store
        self.source = source
        self.model = model
        self.prompt_version = prompt_version
        self.max_tokens = max_tokens

    @property
    @abstractmethod
    def name(self) -> str:
        """Agent name for logging and audit trail."""
        ...

    @abstractmethod
    async def run(self, state: ResearchState) -> ResearchState:
        """
        Execute the agent's task.

        Args:
            state: Current workflow state with inputs from prior steps.

        Returns:
            Updated state with this agent's outputs added.
        """
        ...

    def _log(self, message: str) -> None:
        """Log with agent name prefix."""
        logger.info(f"[{self.name}] {message}")

    def _record_llm_usage(
        self,
        state: ResearchState,
        call_site: str,
        response: object,
        *,
        budget: Optional[int] = None,
    ) -> ResearchState:
        """Record per-call token usage in the workflow audit trail.

        Reasoning tokens are the number that makes a budget explainable, so all
        three counters (input, reasoning, output) are kept. The client already
        logs the same record; this puts it where a run's messages are visible
        and where a future cost-accounting hook can read it.
        """
        usage = getattr(response, "usage", None)
        if usage is None:
            return state
        record = {
            "call_site": call_site,
            "agent": self.name,
            "model": getattr(response, "model", self.model) or "",
            "budget": budget if budget is not None else self.max_tokens,
            "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "reasoning_tokens": int(getattr(usage, "reasoning_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            "finish_reason": getattr(response, "finish_reason", None),
        }
        existing = list(state.get("llm_usage", []))
        existing.append(record)
        logger.info(
            "[%s] LLM usage call_site=%s input=%d reasoning=%d output=%d "
            "total=%d budget=%s finish_reason=%s",
            self.name,
            call_site,
            record["input_tokens"],
            record["reasoning_tokens"],
            record["output_tokens"],
            record["total_tokens"],
            record["budget"],
            record["finish_reason"],
        )
        return add_message(
            state,
            self.name,
            f"LLM usage {call_site}: input={record['input_tokens']} "
            f"reasoning={record['reasoning_tokens']} "
            f"output={record['output_tokens']} total={record['total_tokens']} "
            f"budget={record['budget']}",
        ) | {"llm_usage": existing}
