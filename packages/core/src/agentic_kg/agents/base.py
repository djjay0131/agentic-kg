"""
Base agent interface.

All research agents extend BaseAgent and implement the run() method.
Each agent receives shared dependencies via constructor injection.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from agentic_kg.agents.state import ResearchState

if TYPE_CHECKING:
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
    """

    def __init__(
        self,
        llm_client: BaseLLMClient,
        repository: Neo4jRepository,
        search_service: SearchService | None = None,
        relation_service: RelationService | None = None,
    ) -> None:
        self.llm = llm_client
        self.repo = repository
        self.search = search_service
        self.relations = relation_service

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
