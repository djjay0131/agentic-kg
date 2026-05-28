"""Topic extraction with per-instance taxonomy snapshot.

``TopicExtractor`` is a sibling of ``ProblemExtractor`` but operates at the
paper level with a closed-set Literal schema bound to the taxonomy as
parsed when the instance was constructed. One Cloud Run Job invocation
→ one ``TopicExtractor`` → one taxonomy snapshot, consistent for every
paper in that batch (see spec AC-15 and the taxonomy-reload lifecycle
section).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field as PField
from pydantic import create_model

from agentic_kg.extraction.llm_client import (
    BaseLLMClient,
    LLMError,
    get_openai_client,
)
from agentic_kg.extraction.prompts.templates import build_topic_prompt
from agentic_kg.extraction.schemas import _ExtractedTopicAssignmentBase
from agentic_kg.knowledge_graph.taxonomy import (
    DEFAULT_TAXONOMY_PATH,
    flatten_taxonomy,
    parse_taxonomy,
)

logger = logging.getLogger(__name__)


class TopicExtractor:
    """Single paper-level LLM call against a closed-set taxonomy.

    The accepted ``topic_name`` set is snapshotted at ``__init__`` and bolted
    onto a dynamically constructed Pydantic envelope model. Mid-flight
    taxonomy edits on disk do NOT affect existing instances — operators
    are expected to instantiate one extractor per batch.
    """

    def __init__(
        self,
        client: Optional[BaseLLMClient] = None,
        taxonomy_path: Path = DEFAULT_TAXONOMY_PATH,
        min_confidence: float = 0.7,
    ) -> None:
        self.client = client if client is not None else get_openai_client()
        self.min_confidence = min_confidence
        self.taxonomy_path = Path(taxonomy_path)

        parsed = parse_taxonomy(self.taxonomy_path)
        flat = flatten_taxonomy(parsed)
        self.taxonomy_names: tuple[str, ...] = tuple(flat.keys())
        self.taxonomy_levels: dict[str, str] = dict(flat)

        # Dynamic Pydantic model with a Literal[*names] topic_name field.
        # Literal accepts a tuple via __class_getitem__ in Python 3.11+.
        literal_type = Literal[self.taxonomy_names]  # type: ignore[valid-type]
        self.assignment_model = create_model(
            "ExtractedTopicAssignment",
            __base__=_ExtractedTopicAssignmentBase,
            topic_name=(literal_type, PField(...)),
        )
        self.envelope_model = create_model(
            "ExtractedTopicEnvelope",
            topics=(
                list[self.assignment_model],
                PField(default_factory=list, max_length=5),
            ),
        )

        self._system_prompt, self._user_prompt_tpl = build_topic_prompt(
            self.taxonomy_names
        )

    async def extract(
        self, paper_title: str, sections_text: str
    ) -> list[_ExtractedTopicAssignmentBase]:
        """Run one LLM call against the paper-level abstract+intro text.

        Returns an empty list — never raises — for empty input, known
        ``LLMError``, or low-confidence results. Unknown exceptions
        propagate to the orchestrator, which records an
        ``ExtractionFailure``.
        """
        if not sections_text or not sections_text.strip():
            logger.info("Topic extraction skipped: no input sections")
            return []

        prompt = self._user_prompt_tpl.format(
            paper_title=paper_title, section_text=sections_text
        )
        try:
            response = await self.client.extract(
                prompt=prompt,
                response_model=self.envelope_model,
                system_prompt=self._system_prompt,
            )
        except LLMError as e:
            logger.warning("Topic extraction failed: %s", e)
            return []

        return [
            t
            for t in response.content.topics
            if t.confidence >= self.min_confidence
        ]
