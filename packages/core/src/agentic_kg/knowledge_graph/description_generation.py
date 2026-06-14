"""E-6 Create-time entity description generation with LLM self-validation.

The :class:`DescriptionWithSelfCheck` Pydantic schema is the structured
response the LLM produces. It carries the description and four boolean
self-validation gates (``is_factually_grounded``, ``is_concise``,
``is_specific``, ``is_not_tautological``). Acceptance requires all four
True; otherwise the helper returns ``None`` and the caller proceeds
without persisting a description.

Per the saved ``feedback_llm_self_validation`` memory: prefer in-call
self-validation over separate critic calls or retry loops.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal, Optional

from pydantic import BaseModel, Field

from agentic_kg.extraction.prompts.templates import (
    DESCRIPTION_GENERATION_SYSTEM_PROMPT_V1,
    DESCRIPTION_GENERATION_USER_PROMPT_TEMPLATE_V1,
)

if TYPE_CHECKING:
    from agentic_kg.extraction.llm_client import BaseLLMClient

logger = logging.getLogger(__name__)


EntityKind = Literal["topic", "concept", "model", "method"]


class DescriptionWithSelfCheck(BaseModel):
    """LLM response shape for create-time description generation.

    The LLM produces both the description and its own self-evaluation
    against four gates in a single ``instructor.extract()`` call.
    Acceptance requires every gate to be True.
    """

    description: str = Field(..., min_length=20, max_length=400)

    # Self-validation gates.
    is_factually_grounded: bool = Field(
        description=(
            "True if the description is grounded in well-known facts "
            "about the entity, not speculation."
        ),
    )
    is_concise: bool = Field(
        description="True if the description is 1-2 sentences.",
    )
    is_specific: bool = Field(
        description=(
            "True if the description names what distinguishes this "
            "entity from similar ones."
        ),
    )
    is_not_tautological: bool = Field(
        description=(
            "True if the description doesn't just rephrase the entity name."
        ),
    )
    rejection_reason: Optional[str] = Field(
        default=None,
        description=(
            "If any gate above is False, name which one and why."
        ),
    )

    @property
    def passes_self_validation(self) -> bool:
        return all(
            [
                self.is_factually_grounded,
                self.is_concise,
                self.is_specific,
                self.is_not_tautological,
            ]
        )


def _build_aliases_hint(aliases: list[str]) -> str:
    """Render the aliases fragment for the user prompt.

    Empty list → empty string. Otherwise: ``" (also known as: a, b, c)"``
    capped at three aliases to avoid prompt bloat on heavily-merged
    entities.
    """
    if not aliases:
        return ""
    visible = [a for a in aliases[:3] if a]
    if not visible:
        return ""
    return f" (also known as: {', '.join(visible)})"


async def generate_description_with_self_check(
    *,
    entity_type: EntityKind,
    name: str,
    aliases: list[str],
    llm_client: "BaseLLMClient",
) -> Optional[str]:
    """Generate a self-validated description.

    Returns:
        The description string when all self-validation gates pass.
        ``None`` on self-validation rejection OR LLM call failure.

    Never raises — the caller continues with ``description=None``.
    """
    aliases_hint = _build_aliases_hint(aliases)
    user_prompt = DESCRIPTION_GENERATION_USER_PROMPT_TEMPLATE_V1.format(
        entity_type=entity_type,
        name=name,
        aliases_hint=aliases_hint,
    )

    try:
        response = await llm_client.extract(
            prompt=user_prompt,
            response_model=DescriptionWithSelfCheck,
            system_prompt=DESCRIPTION_GENERATION_SYSTEM_PROMPT_V1,
        )
    except Exception as e:
        logger.warning(
            "Description generation: LLM call failed for %s %r: %s",
            entity_type, name, e,
        )
        return None

    result = response.content
    if not result.passes_self_validation:
        logger.warning(
            "Description generation: self-validation rejected for %s %r: %s",
            entity_type,
            name,
            result.rejection_reason or "(no reason given)",
        )
        return None

    return result.description
