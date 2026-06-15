"""Model extraction (open set, paper-level).

E-8 V2 — direct sibling of :class:`ConceptExtractor`. Open-set: dedup
across papers happens at write time via ``repo.create_or_merge_model``
(E-3). The extractor never marks ``is_canonical=True`` — canonical
status is reserved for ``seed_models.yml``.
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
from agentic_kg.extraction.prompts.templates import build_model_prompt
from agentic_kg.extraction.schemas import ExtractedModel

logger = logging.getLogger(__name__)


class _ModelEnvelope(BaseModel):
    """Instructor response envelope; not part of the public API."""

    models: list[ExtractedModel] = Field(default_factory=list, max_length=20)


class ModelExtractor:
    """Single paper-level LLM call extracting ML models / architectures."""

    def __init__(
        self,
        client: Optional[BaseLLMClient] = None,
        min_confidence: float = 0.7,
    ) -> None:
        self.client = client if client is not None else get_openai_client()
        self.min_confidence = min_confidence
        self._system_prompt, self._user_prompt_tpl = build_model_prompt()

    async def extract(
        self, paper_title: str, sections_text: str
    ) -> list[ExtractedModel]:
        if not sections_text or not sections_text.strip():
            logger.info("Model extraction skipped: no input sections")
            return []

        prompt = self._user_prompt_tpl.format(
            paper_title=paper_title, section_text=sections_text
        )
        try:
            response = await self.client.extract(
                prompt=prompt,
                response_model=_ModelEnvelope,
                system_prompt=self._system_prompt,
            )
        except LLMError as e:
            logger.warning("Model extraction failed: %s", e)
            return []

        return [
            m
            for m in response.content.models
            if m.confidence >= self.min_confidence
        ]
