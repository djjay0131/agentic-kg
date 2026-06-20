"""E-7 Cross-entity normalization.

Detects per-paper surface-form collisions across {Concept, Model, Method}
extractions and routes each via one self-validating LLM call to pick the
correct kind. Mutates the ``PaperExtractionResult`` in place: accepted
decisions drop the losing kinds; rejected decisions (gates fail, low
confidence, LLM exception, out-of-pair pick) keep both extractions per
the spec TL Q1 review.

Trigger is all-in (Q4): exact name match OR alias overlap OR embedding
cosine ≥ SIMILARITY_THRESHOLD. Cheap signals run first; embedding is
computed only for pairs that survived the cheap scan.

Per the saved ``feedback_llm_self_validation`` memory: self-validation
gates live INSIDE the structured response (DisambiguationDecision), not
in a separate critic call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Optional

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from agentic_kg.extraction.llm_client import BaseLLMClient
    from agentic_kg.extraction.schemas import (
        ExtractedMethod,
        ExtractedModel,
        ExtractedResearchConcept,
    )
    from agentic_kg.knowledge_graph.embeddings import EmbeddingService

logger = logging.getLogger(__name__)


EntityKind = Literal["concept", "model", "method"]

SIMILARITY_THRESHOLD = 0.85
MIN_DISAMBIGUATION_CONFIDENCE = 0.7
MAX_EXCERPT_CHARS = 4000


class DisambiguationDecision(BaseModel):
    """LLM response shape for the routing call.

    Single ``instructor.extract()`` carries both the pick and its
    self-evaluation. Acceptance requires both gates True AND
    ``confidence >= MIN_DISAMBIGUATION_CONFIDENCE``. The Pydantic
    ``Literal`` on ``picked_kind`` constrains the LLM regardless of any
    prompt-injection attempt in the paper excerpt (AC-20).
    """

    picked_kind: EntityKind
    confidence: float = Field(ge=0, le=1)
    is_grounded_in_paper_context: bool = Field(
        description=(
            "True if the chosen kind is grounded in the paper's "
            "quoted_text snippets, not abstract reasoning."
        ),
    )
    is_specific_to_one_kind: bool = Field(
        description=(
            "True if the paper clearly uses this surface form in one "
            "role only. False if both kinds are legitimate in the same "
            "paper (rare; treat as reject)."
        ),
    )
    rejection_reason: Optional[str] = Field(default=None)

    @property
    def passes_self_validation(self) -> bool:
        return self.is_grounded_in_paper_context and self.is_specific_to_one_kind


@dataclass
class AmbiguousPair:
    """One cross-kind collision detected in a single paper.

    ``extractions`` maps each kind (concept/model/method) to its source
    ``ExtractedX`` instance. Triple collisions land as a single pair
    with three entries.
    """

    surface: str
    extractions: dict  # EntityKind -> ExtractedConcept | ExtractedModel | ExtractedMethod
    trigger: Literal["exact", "alias", "embedding"]


@dataclass
class NormalizationAuditEntry:
    """One audit row per collision, persisted onto the Paper node.

    ``dropped_kinds`` is empty on reject paths (TL Q1 review: keep both
    extractions when the router itself can't decide).
    """

    surface: str
    trigger: Literal["exact", "alias", "embedding"]
    picked: Optional[EntityKind]
    dropped_kinds: list[str]
    rejection_reason: Optional[str] = None


@dataclass
class NormalizationResult:
    """Per-paper outcome of the normalizer."""

    pairs_detected: int = 0
    pairs_resolved: int = 0
    pairs_rejected: int = 0
    audit: list[NormalizationAuditEntry] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return self.pairs_detected == 0


# =============================================================================
# Cheap-trigger detection — exact name + alias overlap. O(n+m); no I/O.
# =============================================================================


def _iter_surfaces(extraction: Any) -> list[str]:
    """Return non-empty, lower-cased surface forms for an extraction.

    Includes the canonical name plus every non-empty alias. Empties are
    silently filtered (defensive — Pydantic guards against most but
    aliases can in principle carry blanks).
    """
    surfaces = [extraction.name]
    surfaces.extend(extraction.aliases or [])
    return [s.lower() for s in surfaces if s]


def _cheap_collisions(
    concepts: list["ExtractedResearchConcept"],
    models: list["ExtractedModel"],
    methods: list["ExtractedMethod"],
) -> list[AmbiguousPair]:
    """Detect cross-kind collisions via exact name + alias overlap.

    Each surface form (canonical name OR alias) is indexed across all
    three extraction lists. When the same surface form appears for two
    or more distinct kinds, one ``AmbiguousPair`` is emitted carrying
    the canonical extraction for each kind.

    AC-12 — multiple same-kind extractions on the same name are NOT a
    cross-entity collision (within-kind dedup is the repo layer's job).
    The pair is only emitted when ``len(kinds) >= 2``.

    The trigger is ``"exact"`` when at least one of the colliding
    extractions used the surface as its canonical name; otherwise
    ``"alias"`` (the collision was alias-vs-alias or alias-vs-canonical-
    of-the-other-kind).
    """
    # {surface_lower -> [(kind, extraction, surface_was_name)]}
    index: dict[str, list[tuple[EntityKind, Any, bool]]] = {}
    for kind, batch in (
        ("concept", concepts),
        ("model", models),
        ("method", methods),
    ):
        for ex in batch:
            name_lower = ex.name.lower()
            for surface in _iter_surfaces(ex):
                index.setdefault(surface, []).append(
                    (kind, ex, surface == name_lower),
                )

    pairs: list[AmbiguousPair] = []
    seen_signatures: set[tuple[int, ...]] = set()
    for surface, entries in index.items():
        kinds_present = {k for k, _, _ in entries}
        if len(kinds_present) < 2:
            # In-kind only — skip (AC-12).
            continue

        # Build (kind -> representative extraction). When the same kind
        # appears more than once on this surface, the first wins; the
        # in-kind dedup is handled by create_or_merge_X downstream.
        extractions: dict[EntityKind, Any] = {}
        for kind, ex, _ in entries:
            extractions.setdefault(kind, ex)

        # Dedupe identical pairs that show up via both name AND alias
        # hits on the same triplet of extractions.
        signature = tuple(sorted(id(ex) for ex in extractions.values()))
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)

        trigger: Literal["exact", "alias"] = (
            "exact" if any(was_name for _, _, was_name in entries) else "alias"
        )
        pairs.append(
            AmbiguousPair(
                surface=surface, extractions=extractions, trigger=trigger,
            )
        )
    return pairs


# =============================================================================
# Embedding-trigger detection — pairwise cosine over surviving pairs only.
# =============================================================================


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors.

    Pure helper; no error handling. Caller guarantees non-empty equal-
    length lists (embedding service contract).
    """
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:  # pragma: no cover - embedder never returns 0-vec
        return 0.0
    return dot / (norm_a * norm_b)


def _embed_with_cache(
    name: str,
    cache: dict[str, list[float]],
    embedder: "EmbeddingService",
) -> Optional[list[float]]:
    """Return cached or freshly-generated embedding for ``name``.

    Returns ``None`` on embedder failure — caller treats None as
    "fuzzy layer degraded, skip the pair" rather than raising.
    """
    key = name.lower()
    if key in cache:
        return cache[key]
    try:
        vec = embedder.generate_embedding(name)
    except Exception as e:
        logger.warning(
            "Embedding failed for %r during cross-entity scan: %s", name, e,
        )
        return None
    cache[key] = vec
    return vec


def _embedding_collisions(
    concepts: list["ExtractedResearchConcept"],
    models: list["ExtractedModel"],
    methods: list["ExtractedMethod"],
    *,
    already_paired_ids: set[int],
    embedder: "EmbeddingService",
    threshold: float,
) -> list[AmbiguousPair]:
    """Detect fuzzy cross-kind collisions via embedding cosine similarity.

    Only runs on extractions NOT already in a cheap-trigger pair (cost
    guard per AC-3). Embeddings are cached per-paper so each canonical
    name is embedded at most once. On embedder failure, the affected
    pair is skipped silently (per AC-13) — failure does NOT propagate.

    Pairwise scan across the three cross-kind axes: concept×model,
    concept×method, model×method.
    """
    cache: dict[str, list[float]] = {}
    candidates_concept = [c for c in concepts if id(c) not in already_paired_ids]
    candidates_model = [m for m in models if id(m) not in already_paired_ids]
    candidates_method = [m for m in methods if id(m) not in already_paired_ids]

    pairs: list[AmbiguousPair] = []
    seen_signatures: set[tuple[int, ...]] = set()

    def _scan(
        a_kind: EntityKind,
        a_batch: list[Any],
        b_kind: EntityKind,
        b_batch: list[Any],
    ) -> None:
        for a in a_batch:
            v_a = _embed_with_cache(a.name, cache, embedder)
            if v_a is None:
                continue
            for b in b_batch:
                v_b = _embed_with_cache(b.name, cache, embedder)
                if v_b is None:
                    continue
                score = _cosine(v_a, v_b)
                if score < threshold:
                    continue
                sig = tuple(sorted((id(a), id(b))))
                if sig in seen_signatures:
                    continue
                seen_signatures.add(sig)
                pairs.append(
                    AmbiguousPair(
                        surface=a.name,
                        extractions={a_kind: a, b_kind: b},
                        trigger="embedding",
                    )
                )

    _scan("concept", candidates_concept, "model", candidates_model)
    _scan("concept", candidates_concept, "method", candidates_method)
    _scan("model", candidates_model, "method", candidates_method)
    return pairs


# =============================================================================
# Composer + paper excerpt builder
# =============================================================================


def detect_ambiguous_pairs(
    concepts: list["ExtractedResearchConcept"],
    models: list["ExtractedModel"],
    methods: list["ExtractedMethod"],
    *,
    embedder: "EmbeddingService",
    similarity_threshold: float = SIMILARITY_THRESHOLD,
) -> list[AmbiguousPair]:
    """Run cheap-triggers, then embedding-triggers over what survives.

    Cheap signals (exact + alias) run first and cost nothing. Embedding
    signals only run on extractions NOT already in a cheap pair —
    bounded by AC-3's cost guard.
    """
    cheap = _cheap_collisions(concepts, models, methods)
    paired_ids: set[int] = set()
    for pair in cheap:
        for ex in pair.extractions.values():
            paired_ids.add(id(ex))
    fuzzy = _embedding_collisions(
        concepts, models, methods,
        already_paired_ids=paired_ids,
        embedder=embedder,
        threshold=similarity_threshold,
    )
    return cheap + fuzzy


def _build_paper_excerpt(extraction_result: Any, max_chars: int) -> str:
    """Concatenate every extraction's quoted_text into one excerpt.

    These are the LLM's ground-truth snippets — the same snippets the
    routing LLM should weigh when picking the kind. Truncates at
    ``max_chars`` to keep the prompt bounded.
    """
    snippets: list[str] = []
    for batch in (
        getattr(extraction_result, "concepts", []) or [],
        getattr(extraction_result, "models", []) or [],
        getattr(extraction_result, "methods", []) or [],
    ):
        for ex in batch:
            snippets.append(ex.quoted_text)
    excerpt = " ... ".join(snippets)
    return excerpt[:max_chars]


# =============================================================================
# Routing LLM call — disambiguate one pair
# =============================================================================


def _format_kinds_block(pair: AmbiguousPair) -> str:
    """Render the pair's detected-as block for the user prompt."""
    lines: list[str] = []
    for kind in ("concept", "model", "method"):
        ex = pair.extractions.get(kind)
        if ex is None:
            continue
        aliases = list(ex.aliases or [])
        lines.append(
            f"- {kind}: name=\"{ex.name}\", aliases={aliases}, "
            f"quoted_text=<quote-{kind}>{ex.quoted_text}</quote-{kind}>"
        )
    return "\n".join(lines)


async def disambiguate_pair(
    pair: AmbiguousPair,
    *,
    paper_title: str,
    paper_excerpt: str,
    llm_client: "BaseLLMClient",
    min_confidence: float = MIN_DISAMBIGUATION_CONFIDENCE,
) -> tuple[Optional[EntityKind], Optional[str]]:
    """Run one routing LLM call. Returns ``(picked_kind, reason)``.

    Returns ``(picked_kind, None)`` on accept (gates True AND confidence
    >= threshold AND picked_kind is in the pair).

    Returns ``(None, reason_str)`` on reject:
      * self-validation gates fail
      * confidence below threshold
      * picked_kind not in pair (AC-18 defensive guard)
      * LLM raises (AC-7)

    Never raises by contract — caller proceeds with the pre-routing
    state (keep both extractions per TL Q1 review).
    """
    # Imports are local so the module stays import-cheap for callers that
    # don't run the routing path (e.g. cheap-trigger detection tests).
    from agentic_kg.extraction.prompts.templates import (
        DISAMBIGUATION_SYSTEM_PROMPT_V1,
        DISAMBIGUATION_USER_PROMPT_TEMPLATE_V1,
    )

    user_prompt = DISAMBIGUATION_USER_PROMPT_TEMPLATE_V1.format(
        paper_title=paper_title,
        surface=pair.surface,
        kinds_block=_format_kinds_block(pair),
        paper_excerpt=paper_excerpt,
    )
    try:
        response = await llm_client.extract(
            prompt=user_prompt,
            response_model=DisambiguationDecision,
            system_prompt=DISAMBIGUATION_SYSTEM_PROMPT_V1,
        )
    except Exception as e:
        logger.warning(
            "Disambiguation failed for surface=%r: %s", pair.surface, e,
        )
        return None, f"llm call failed: {e}"

    d: DisambiguationDecision = response.content
    if not d.passes_self_validation:
        reason = d.rejection_reason or "self-validation gate False"
        logger.warning(
            "Disambiguation rejected for %r (gates): %s", pair.surface, reason,
        )
        return None, reason

    if d.confidence < min_confidence:
        reason = (
            f"confidence {d.confidence:.2f} below "
            f"threshold {min_confidence:.2f}"
        )
        logger.warning("Disambiguation rejected for %r: %s", pair.surface, reason)
        return None, reason

    if d.picked_kind not in pair.extractions:
        # AC-18: LLM returned a kind that wasn't part of this pair.
        # Defensive — treat as reject; the integrator preserves both.
        reason = (
            f"picked kind {d.picked_kind!r} not in pair "
            f"{sorted(pair.extractions)}"
        )
        logger.warning("Disambiguation rejected for %r: %s", pair.surface, reason)
        return None, reason

    return d.picked_kind, None


# =============================================================================
# Top-level entry point — normalize_cross_entity
# =============================================================================


def _drop_extraction(
    extraction_result: Any, kind: EntityKind, target: Any,
) -> None:
    """Remove ``target`` from the matching list on ``extraction_result``.

    In-place via list-slice assignment so all references to the bucket
    see the pruned state (TL Q2 review).
    """
    bucket_name = kind + "s"  # concept→concepts, model→models, method→methods
    bucket = getattr(extraction_result, bucket_name)
    bucket[:] = [e for e in bucket if e is not target]


async def normalize_cross_entity(
    extraction_result: Any,
    *,
    paper_title: str,
    embedder: "EmbeddingService",
    llm_client: "BaseLLMClient",
    similarity_threshold: float = SIMILARITY_THRESHOLD,
    min_confidence: float = MIN_DISAMBIGUATION_CONFIDENCE,
) -> NormalizationResult:
    """Detect cross-entity collisions in a paper's extractions, route
    each via one LLM call, and drop the losing kinds in place.

    Mutates ``extraction_result.concepts/.models/.methods`` in place
    when a collision is accepted (drop loser semantics, AC-8).
    Rejected collisions leave the extractions intact (TL Q1 review:
    keep both, AC-9). Either way an audit row lands in the returned
    ``NormalizationResult``.

    AC-11 / AC-19: when zero pairs are detected, NO LLM call is made
    and ``result.is_clean is True``. AC-19 also pins the cost ceiling
    at one LLM call per pair.
    """
    pairs = detect_ambiguous_pairs(
        extraction_result.concepts,
        extraction_result.models,
        extraction_result.methods,
        embedder=embedder,
        similarity_threshold=similarity_threshold,
    )
    result = NormalizationResult(pairs_detected=len(pairs))
    if not pairs:
        return result

    excerpt = _build_paper_excerpt(extraction_result, MAX_EXCERPT_CHARS)
    for pair in pairs:
        picked, reason = await disambiguate_pair(
            pair,
            paper_title=paper_title,
            paper_excerpt=excerpt,
            llm_client=llm_client,
            min_confidence=min_confidence,
        )
        if picked is None:
            # Reject path — keep both extractions (TL Q1 review). The
            # audit row marks the unresolved case; downstream integrator
            # writes both nodes.
            result.pairs_rejected += 1
            result.audit.append(
                NormalizationAuditEntry(
                    surface=pair.surface,
                    trigger=pair.trigger,
                    picked=None,
                    dropped_kinds=[],
                    rejection_reason=reason or "unknown",
                )
            )
            continue
        # Accept path — drop the losing kinds.
        dropped: list[str] = []
        for kind, ex in pair.extractions.items():
            if kind != picked:
                _drop_extraction(extraction_result, kind, ex)
                dropped.append(kind)
        result.pairs_resolved += 1
        result.audit.append(
            NormalizationAuditEntry(
                surface=pair.surface,
                trigger=pair.trigger,
                picked=picked,
                dropped_kinds=sorted(dropped),
            )
        )
    return result


def audit_to_json(result: NormalizationResult) -> str:
    """Serialize a NormalizationResult's audit list to JSON for Neo4j.

    Used by the integrator to populate ``Paper.normalization_audit``.
    Returns ``""`` when there's nothing to record (clean paper) so the
    integrator can skip the property write.
    """
    import json

    if result.is_clean:
        return ""
    return json.dumps([
        {
            "surface": entry.surface,
            "trigger": entry.trigger,
            "picked": entry.picked,
            "dropped_kinds": entry.dropped_kinds,
            "rejection_reason": entry.rejection_reason,
        }
        for entry in result.audit
    ])
