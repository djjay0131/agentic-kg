"""Deterministic item alignment for the RQ1 harness.

Three matchers are supported, in priority order:

1. **normalized string** — the default. Two items match when their normalized
   surface sets intersect (canonical text and any aliases). Literal equality
   after :func:`agentic_kg.evaluation.schema.normalize_text`; no threshold.
2. **span overlap** — when both items carry a character span, their intersection
   over union (IoU) must meet ``span_iou_threshold``. Offsets are recovered from
   the committed paper text by the converter, so this matcher is only as good as
   those spans and is never the sole reason a match is reported.
3. **embedding similarity** — opt-in and explicitly flagged. It is the only
   non-deterministic-looking option, so it is off by default; enabling it
   requires a supplied similarity function or the optional
   ``sentence-transformers`` package. CI never enables it.

Alignment is one-to-one and greedy over the best available pair. Ties are broken
by gold index and then prediction index, so the same inputs always produce the
same assignment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from agentic_kg.evaluation.schema import AnnotationRecord, CharSpan

#: Pair kind priority: a literal match always outranks a span match, which
#: outranks an embedding match. Lower is better.
_KIND_PRIORITY = {"string": 0, "span": 1, "embedding": 2}


def span_iou(a: CharSpan | None, b: CharSpan | None) -> float | None:
    """Intersection-over-union for two half-open intervals, or ``None``."""
    if a is None or b is None:
        return None
    intersection = max(0, min(a.end, b.end) - max(a.start, b.start))
    union = max(a.end, b.end) - min(a.start, b.start)
    if union <= 0:
        return None
    return intersection / union


def _quotes_overlap(a: str | None, b: str | None) -> bool | None:
    """Do two evidence quotes overlap (one contains the other), case-folded?

    ``None`` means at least one side has no quote, so the quote test is
    inconclusive and the caller should not mark the evidence link bad on this
    basis alone.
    """
    if not a or not b:
        return None
    left = " ".join(a.split()).casefold()
    right = " ".join(b.split()).casefold()
    return left in right or right in left


@dataclass(frozen=True)
class MatchConfig:
    span_iou_threshold: float = 0.5
    embedding_enabled: bool = False
    embedding_threshold: float = 0.8
    similarity_fn: Callable[[str, str], float] | None = None


@dataclass(frozen=True)
class MatchedPair:
    gold: AnnotationRecord
    prediction: AnnotationRecord
    method: str
    score: float
    section_mismatch: bool
    bad_evidence_link: bool


@dataclass
class GroupMatch:
    """The alignment outcome for one ``(paper, field_type)`` group."""

    paper_doi: str
    field_type: str
    pairs: list[MatchedPair] = field(default_factory=list)
    unmatched_gold: list[AnnotationRecord] = field(default_factory=list)
    unmatched_predictions: list[AnnotationRecord] = field(default_factory=list)

    @property
    def tp(self) -> int:
        return len(self.pairs)

    @property
    def fp(self) -> int:
        return len(self.unmatched_predictions)

    @property
    def fn(self) -> int:
        return len(self.unmatched_gold)


def _candidate_pairs(
    gold: Sequence[AnnotationRecord],
    predictions: Sequence[AnnotationRecord],
    config: MatchConfig,
) -> list[tuple[int, int, str, float]]:
    pairs: list[tuple[int, int, str, float]] = []
    for gi, gold_item in enumerate(gold):
        gold_surfaces = gold_item.surfaces()
        for pi, prediction in enumerate(predictions):
            method: str | None = None
            score = 0.0
            if gold_surfaces & prediction.surfaces():
                method, score = "string", 1.0
            else:
                iou = span_iou(gold_item.char_span, prediction.char_span)
                if iou is not None and iou >= config.span_iou_threshold:
                    method, score = "span", iou
                elif config.embedding_enabled and config.similarity_fn is not None:
                    similarity = float(config.similarity_fn(gold_item.text, prediction.text))
                    if similarity >= config.embedding_threshold:
                        method, score = "embedding", similarity
            if method is not None:
                pairs.append((gi, pi, method, score))
    # Deterministic order: best method, best score, then input order.
    pairs.sort(
        key=lambda item: (
            _KIND_PRIORITY[item[2]],
            -item[3],
            item[0],
            item[1],
        )
    )
    return pairs


def _evidence_is_bad(gold: AnnotationRecord, prediction: AnnotationRecord) -> bool:
    """Is the prediction's evidence link inconsistent with the gold grounding?

    A prediction with no evidence at all when gold has evidence is a bad link.
    Otherwise the check is conservative: quotes are compared only when both
    sides have one, spans only when both sides have one. The extraction schema
    carries ``quoted_text`` but not character offsets, so requiring offsets would
    mark every real prediction bad.
    """
    gold_has_evidence = bool(gold.evidence_quote or gold.char_span)
    if not gold_has_evidence:
        return False
    if not prediction.evidence_quote and prediction.char_span is None:
        return True
    quote_overlap = _quotes_overlap(gold.evidence_quote, prediction.evidence_quote)
    if quote_overlap is False:
        return True
    if quote_overlap is True:
        return False
    iou = span_iou(gold.char_span, prediction.char_span)
    if iou is not None:
        return iou == 0.0
    return False


def match_group(
    gold: Iterable[AnnotationRecord],
    predictions: Iterable[AnnotationRecord],
    config: MatchConfig | None = None,
) -> GroupMatch:
    """Align one paper+field's gold items to predictions, one-to-one."""
    config = config or MatchConfig()
    gold_items = [item for item in gold if item.present]
    prediction_items = [item for item in predictions if item.present]
    paper_doi = ""
    field_type = ""
    if gold_items:
        paper_doi, field_type = gold_items[0].paper_doi, gold_items[0].field_type.value
    elif prediction_items:
        paper_doi, field_type = (
            prediction_items[0].paper_doi,
            prediction_items[0].field_type.value,
        )

    pairs = _candidate_pairs(gold_items, prediction_items, config)
    used_gold: set[int] = set()
    used_predictions: set[int] = set()
    matched: list[MatchedPair] = []
    for gi, pi, method, score in pairs:
        if gi in used_gold or pi in used_predictions:
            continue
        used_gold.add(gi)
        used_predictions.add(pi)
        gold_item = gold_items[gi]
        prediction = prediction_items[pi]
        matched.append(
            MatchedPair(
                gold=gold_item,
                prediction=prediction,
                method=method,
                score=score,
                section_mismatch=gold_item.section != prediction.section,
                bad_evidence_link=_evidence_is_bad(gold_item, prediction),
            )
        )

    return GroupMatch(
        paper_doi=paper_doi,
        field_type=field_type,
        pairs=matched,
        unmatched_gold=[item for index, item in enumerate(gold_items) if index not in used_gold],
        unmatched_predictions=[
            item for index, item in enumerate(prediction_items) if index not in used_predictions
        ],
    )


def match_items(
    gold: Iterable[AnnotationRecord],
    predictions: Iterable[AnnotationRecord],
    config: MatchConfig | None = None,
) -> list[GroupMatch]:
    """Group by paper+field, then align each group."""
    gold_groups: dict[tuple[str, str], list[AnnotationRecord]] = {}
    prediction_groups: dict[tuple[str, str], list[AnnotationRecord]] = {}
    for item in gold:
        gold_groups.setdefault((item.paper_doi, item.field_type.value), []).append(item)
    for item in predictions:
        prediction_groups.setdefault((item.paper_doi, item.field_type.value), []).append(item)

    keys = sorted(set(gold_groups) | set(prediction_groups))
    return [
        match_group(
            gold_groups.get(key, []),
            prediction_groups.get(key, []),
            config,
        )
        for key in keys
    ]


def default_embedding_similarity() -> Callable[[str, str], float]:
    """Lazy sentence-transformers backend for ``--embedding-similarity``.

    Kept out of the default install: the RQ1 harness must run in CI with no
    model downloads. If the package is absent, the error says exactly what to
    install rather than silently disabling the flag.
    """
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "embedding similarity requested but sentence-transformers is not installed; "
            "install it with `uv pip install sentence-transformers` or run without "
            "--embedding-similarity"
        ) from exc

    model = SentenceTransformer("all-MiniLM-L6-v2")

    def similarity(left: str, right: str) -> float:  # pragma: no cover - model path
        embeddings = model.encode([left, right], normalize_embeddings=True)
        return float(embeddings[0] @ embeddings[1])

    return similarity
