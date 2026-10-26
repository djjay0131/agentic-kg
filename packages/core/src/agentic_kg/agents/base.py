"""
Base agent interface.

All research agents extend BaseAgent and implement the run() method.
Each agent receives shared dependencies via constructor injection.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from agentic_kg.agents.graph_port import (
    SOURCE_LEGACY,
    GraphPort,
    LegacyGraphPort,
)
from agentic_kg.agents.state import ResearchState

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
