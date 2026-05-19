"""
Dedup threshold calibration harness for ResearchConcept (E-2, AC-12).

The goal is to pick a cosine-similarity threshold for
``repository.create_or_merge_research_concept`` that maximizes F1 on a
hand-labeled set of (concept_a, concept_b, label) pairs, where label is
``same`` or ``different``.

This module is a pure harness — it loads pairs, runs real embeddings,
and produces a report. It does not modify the repository or the stored
default threshold. Wire it up via the ``calibrate-concepts`` CLI
command (see ``cli.py``).

Pair fixture shape (YAML)::

    - a: "attention mechanism"
      b: "self-attention"
      label: same

    - a: "transfer learning"
      b: "domain adaptation"
      label: different

Output shape (per-threshold row)::

    ThresholdResult(
        threshold=0.88,
        precision=0.92, recall=0.81, f1=0.86,
        true_positive=17, false_positive=1,
        true_negative=10, false_negative=4,
    )
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import yaml

logger = logging.getLogger(__name__)


DEFAULT_PAIR_FIXTURE = (
    Path(__file__).parent / "data" / "concept_calibration_pairs.yml"
)

LABEL_SAME = "same"
LABEL_DIFFERENT = "different"
_VALID_LABELS = {LABEL_SAME, LABEL_DIFFERENT}

# Candidate thresholds swept by default. Fine enough to see the shoulder
# around the typical "similar but distinct" region.
DEFAULT_THRESHOLDS = [
    0.70,
    0.75,
    0.80,
    0.82,
    0.84,
    0.86,
    0.88,
    0.90,
    0.92,
    0.94,
    0.96,
]


class CalibrationError(ValueError):
    """Raised when calibration input is malformed."""


@dataclass
class ConceptPair:
    """A single labeled pair."""

    a: str
    b: str
    label: str
    a_description: Optional[str] = None
    b_description: Optional[str] = None


@dataclass
class ScoredPair:
    """A pair whose cosine similarity has been measured."""

    pair: ConceptPair
    score: float


@dataclass
class ThresholdResult:
    """Classification metrics at a single threshold."""

    threshold: float
    precision: float
    recall: float
    f1: float
    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int


@dataclass
class CalibrationReport:
    """Full calibration output."""

    pairs_evaluated: int
    positives: int
    negatives: int
    rows: list[ThresholdResult]
    recommended_threshold: Optional[float]
    recommended_f1: Optional[float]


# =============================================================================
# Parsing
# =============================================================================


def load_concept_pairs(source: str | Path | list | None = None) -> list[ConceptPair]:
    """
    Parse a labeled pair file (or pre-parsed list of dicts) into
    ``ConceptPair`` records. Raises ``CalibrationError`` on malformed
    input so callers see a single exception type.
    """
    if source is None:
        source = DEFAULT_PAIR_FIXTURE

    if isinstance(source, (str, Path)) and not _is_inline_yaml(source):
        path = Path(source)
        if not path.exists():
            raise CalibrationError(f"Pair fixture not found: {path}")
        raw = yaml.safe_load(path.read_text())
    elif isinstance(source, str):
        raw = yaml.safe_load(source)
    else:
        raw = source

    if raw is None:
        raise CalibrationError("Pair fixture is empty")
    if not isinstance(raw, list):
        raise CalibrationError(
            f"Pair fixture must be a list of mappings, got {type(raw).__name__}"
        )

    pairs: list[ConceptPair] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise CalibrationError(
                f"Entry {i}: must be a mapping, got {type(entry).__name__}"
            )
        a = entry.get("a")
        b = entry.get("b")
        label = entry.get("label")
        if not isinstance(a, str) or not a.strip():
            raise CalibrationError(f"Entry {i}: 'a' is required and must be a non-empty string")
        if not isinstance(b, str) or not b.strip():
            raise CalibrationError(f"Entry {i}: 'b' is required and must be a non-empty string")
        if label not in _VALID_LABELS:
            raise CalibrationError(
                f"Entry {i}: 'label' must be one of {sorted(_VALID_LABELS)}, got {label!r}"
            )
        pairs.append(
            ConceptPair(
                a=a.strip(),
                b=b.strip(),
                label=label,
                a_description=entry.get("a_description"),
                b_description=entry.get("b_description"),
            )
        )
    return pairs


def _is_inline_yaml(value: str | Path) -> bool:
    """Heuristic: treat as inline YAML if it contains newlines or no file suffix."""
    if isinstance(value, Path):
        return False
    return "\n" in value or (
        not value.endswith(".yml") and not value.endswith(".yaml")
    )


# =============================================================================
# Scoring
# =============================================================================


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity between two equal-length non-zero vectors."""
    if len(a) != len(b):
        raise ValueError(f"Embedding length mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def compute_pair_similarities(pairs: list[ConceptPair]) -> list[ScoredPair]:
    """
    Embed every pair via ``generate_research_concept_embedding`` and
    return (pair, cosine) tuples. Requires OpenAI API access.
    """
    from agentic_kg.knowledge_graph.embeddings import (
        generate_research_concept_embedding,
    )

    scored: list[ScoredPair] = []
    for pair in pairs:
        emb_a = generate_research_concept_embedding(pair.a, pair.a_description)
        emb_b = generate_research_concept_embedding(pair.b, pair.b_description)
        score = cosine_similarity(emb_a, emb_b)
        logger.debug(f"[calibration] {pair.a!r} vs {pair.b!r} -> {score:.4f} ({pair.label})")
        scored.append(ScoredPair(pair=pair, score=score))
    return scored


# =============================================================================
# Threshold sweep
# =============================================================================


def analyze_thresholds(
    scored_pairs: list[ScoredPair],
    thresholds: Optional[list[float]] = None,
) -> list[ThresholdResult]:
    """
    For each candidate threshold, score the pairs as predicted-same
    (cosine >= threshold) and compute precision / recall / F1 against
    the ``label``.
    """
    if not scored_pairs:
        raise CalibrationError("Cannot analyze thresholds with no scored pairs")

    thresholds = thresholds or list(DEFAULT_THRESHOLDS)

    results: list[ThresholdResult] = []
    for t in thresholds:
        tp = fp = tn = fn = 0
        for sp in scored_pairs:
            predicted_same = sp.score >= t
            actual_same = sp.pair.label == LABEL_SAME
            if predicted_same and actual_same:
                tp += 1
            elif predicted_same and not actual_same:
                fp += 1
            elif not predicted_same and not actual_same:
                tn += 1
            else:
                fn += 1

        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall)
            else 0.0
        )

        results.append(
            ThresholdResult(
                threshold=t,
                precision=precision,
                recall=recall,
                f1=f1,
                true_positive=tp,
                false_positive=fp,
                true_negative=tn,
                false_negative=fn,
            )
        )
    return results


def recommend_threshold(
    rows: list[ThresholdResult],
) -> tuple[Optional[float], Optional[float]]:
    """
    Pick the threshold that maximizes F1. Ties broken by higher
    precision (fewer spurious merges), then higher threshold (favor
    conservatism). Returns (threshold, f1), or (None, None) if every
    row has F1 = 0 (no positive predictions at all).
    """
    if not rows:
        return None, None
    scored = [r for r in rows if r.f1 > 0]
    if not scored:
        return None, None
    scored.sort(
        key=lambda r: (r.f1, r.precision, r.threshold),
        reverse=True,
    )
    best = scored[0]
    return best.threshold, best.f1


def run_calibration(
    pairs_source: str | Path | list | None = None,
    thresholds: Optional[list[float]] = None,
) -> CalibrationReport:
    """End-to-end: load pairs, embed, sweep thresholds, recommend one."""
    pairs = load_concept_pairs(pairs_source)
    scored = compute_pair_similarities(pairs)
    rows = analyze_thresholds(scored, thresholds=thresholds)

    positives = sum(1 for sp in scored if sp.pair.label == LABEL_SAME)
    negatives = len(scored) - positives
    threshold, f1 = recommend_threshold(rows)

    return CalibrationReport(
        pairs_evaluated=len(scored),
        positives=positives,
        negatives=negatives,
        rows=rows,
        recommended_threshold=threshold,
        recommended_f1=f1,
    )


def format_report(report: CalibrationReport) -> str:
    """Render a human-readable report for the CLI."""
    lines = [
        f"Pairs evaluated: {report.pairs_evaluated} "
        f"({report.positives} same / {report.negatives} different)",
        "",
        f"{'threshold':>10} {'precision':>10} {'recall':>8} "
        f"{'f1':>6} {'tp':>4} {'fp':>4} {'tn':>4} {'fn':>4}",
    ]
    for row in report.rows:
        lines.append(
            f"{row.threshold:>10.2f} "
            f"{row.precision:>10.3f} {row.recall:>8.3f} {row.f1:>6.3f} "
            f"{row.true_positive:>4d} {row.false_positive:>4d} "
            f"{row.true_negative:>4d} {row.false_negative:>4d}"
        )
    lines.append("")
    if report.recommended_threshold is not None:
        lines.append(
            f"Recommended threshold: {report.recommended_threshold:.2f} "
            f"(F1 = {report.recommended_f1:.3f})"
        )
    else:
        lines.append("No threshold produced any positive predictions.")
    return "\n".join(lines)
