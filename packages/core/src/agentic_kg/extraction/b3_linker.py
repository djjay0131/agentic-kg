"""B3 heuristic linker (Problem ↔ Concept).

Pure Python alias-substring linker that draws ``INVOLVES_CONCEPT`` edges
from ``ProblemConcept`` to ``ResearchConcept`` using the surface forms a
single paper's extractor emitted. Per the spec (pattern i, "pollution
immunity"), the linker NEVER uses the merged ``ResearchConcept``'s
accumulated alias list — only the per-paper extraction object.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from agentic_kg.extraction.fixtures.b3_deny_list import DEFAULT_ALIAS_DENY_LIST
from agentic_kg.extraction.schemas import ExtractedResearchConcept

logger = logging.getLogger(__name__)


def link_problems_to_concepts(
    *,
    mentions: Iterable[Any],
    paper_extractions: list[tuple[ExtractedResearchConcept, str]],
    min_alias_length: int = 4,
    alias_deny_list: frozenset[str] = DEFAULT_ALIAS_DENY_LIST,
) -> list[tuple[str, str]]:
    """Find ``(problem_concept_id, research_concept_id)`` edges via aliases.

    Args:
        mentions: ProblemMention-like objects with ``.statement``,
            ``.quoted_text``, and ``.concept_id`` attributes. Mentions
            whose ``concept_id`` is ``None`` (not yet linked to a
            ProblemConcept) are skipped — there is nothing to link from.
        paper_extractions: ``(ExtractedResearchConcept_for_this_paper,
            merged_research_concept_id)`` pairs. The merged id is for
            graph writes; surface forms come from the extraction object
            — never from accumulated historical aliases.
        min_alias_length: Drop surface forms shorter than this. Default
            ``4`` suppresses ``ML``, ``AI``, ``GNN`` etc. — we'd rather
            miss those than mislabel every mention containing them.
        alias_deny_list: Lowercased terms that bypass length filtering
            and are always rejected. Defaults to ``DEFAULT_ALIAS_DENY_LIST``
            loaded from ``b3_deny_list.yml``.

    Returns:
        Unique list of ``(problem_concept_id, research_concept_id)``
        pairs, preserving discovery order. Duplicates collapse so audit
        logs stay clean.
    """
    edges: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for extracted, research_concept_id in paper_extractions:
        surface = _filter_surface_forms(
            candidates=[extracted.name, *extracted.aliases],
            min_alias_length=min_alias_length,
            alias_deny_list=alias_deny_list,
        )
        if not surface:
            continue

        pattern = re.compile(
            r"\b(" + "|".join(re.escape(s) for s in surface) + r")\b",
            flags=re.IGNORECASE,
        )

        for mention in mentions:
            if mention.concept_id is None:
                continue
            haystack = f"{mention.statement or ''} {mention.quoted_text or ''}"
            match = pattern.search(haystack)
            if not match:
                continue

            edge = (mention.concept_id, research_concept_id)
            if edge in seen:
                continue
            seen.add(edge)
            edges.append(edge)
            logger.debug(
                "B3-link: mention=%s -> research_concept=%s matched=%s",
                mention.id,
                research_concept_id,
                match.group(1),
            )

    return edges


def _filter_surface_forms(
    *,
    candidates: list[str],
    min_alias_length: int,
    alias_deny_list: frozenset[str],
) -> list[str]:
    """Return the candidates that pass length and deny-list filters."""
    filtered = []
    for raw in candidates:
        if not raw:
            continue
        lowered = raw.lower()
        if lowered in alias_deny_list:
            continue
        if len(raw) < min_alias_length:
            continue
        filtered.append(raw)
    return filtered
