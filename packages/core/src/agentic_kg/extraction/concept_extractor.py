"""Concept extraction (open set, paper-level).

Sibling of ``ProblemExtractor`` and ``TopicExtractor``. There is no closed
set, so ``ConceptExtractor`` does not need a per-instance schema — the
``ExtractedResearchConcept`` model in ``schemas`` is static. Dedup across
papers happens at write time via ``repo.create_or_merge_research_concept``
(E-2).
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from agentic_kg.extraction.llm_client import (
    BaseLLMClient,
    LLMError,
    get_openai_client,
)
from agentic_kg.extraction.prompts.templates import build_concept_prompt
from agentic_kg.extraction.schemas import ExtractedResearchConcept

logger = logging.getLogger(__name__)


class _ConceptEnvelope(BaseModel):
    """Instructor response envelope; not part of the public API."""

    concepts: list[ExtractedResearchConcept] = Field(
        default_factory=list, max_length=20
    )


class ConceptExtractor:
    """Single paper-level LLM call extracting research concepts."""

    def __init__(
        self,
        client: Optional[BaseLLMClient] = None,
        min_confidence: float = 0.7,
    ) -> None:
        self.client = client if client is not None else get_openai_client()
        self.min_confidence = min_confidence
        self._system_prompt, self._user_prompt_tpl = build_concept_prompt()

    async def extract(
        self, paper_title: str, sections_text: str
    ) -> list[ExtractedResearchConcept]:
        if not sections_text or not sections_text.strip():
            logger.info("Concept extraction skipped: no input sections")
            return []

        prompt = self._user_prompt_tpl.format(
            paper_title=paper_title, section_text=sections_text
        )
        try:
            response = await self.client.extract(
                prompt=prompt,
                response_model=_ConceptEnvelope,
                system_prompt=self._system_prompt,
            )
        except LLMError as e:
            logger.warning("Concept extraction failed: %s", e)
            return []

        return [
            c
            for c in response.content.concepts
            if c.confidence >= self.min_confidence
        ]
