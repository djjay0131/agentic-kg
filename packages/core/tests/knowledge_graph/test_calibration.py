"""
Tests for the ResearchConcept threshold calibration harness (E-2, AC-12).

All pure unit tests — the end-to-end embedding run is exercised via
synthetic ``ScoredPair`` values so we don't hit OpenAI.
"""

from pathlib import Path

import pytest

from agentic_kg.knowledge_graph.calibration import (
    DEFAULT_PAIR_FIXTURE,
    DEFAULT_THRESHOLDS,
    CalibrationError,
    ConceptPair,
    ScoredPair,
    analyze_thresholds,
    cosine_similarity,
    format_report,
    load_concept_pairs,
    recommend_threshold,
)


# =============================================================================
# Pair loading
# =============================================================================


class TestLoadConceptPairs:
    def test_loads_bundled_fixture(self):
        pairs = load_concept_pairs(DEFAULT_PAIR_FIXTURE)
        assert len(pairs) >= 20
        labels = {p.label for p in pairs}
        assert labels == {"same", "different"}

    def test_accepts_inline_list(self):
        pairs = load_concept_pairs(
            [
                {"a": "attention mechanism", "b": "self-attention", "label": "same"},
                {"a": "GNN", "b": "CNN", "label": "different"},
            ]
        )
        assert len(pairs) == 2
        assert pairs[0].a == "attention mechanism"
        assert pairs[0].label == "same"

    def test_strips_whitespace(self):
        pairs = load_concept_pairs(
            [{"a": "  x  ", "b": " y ", "label": "same"}]
        )
        assert pairs[0].a == "x"
        assert pairs[0].b == "y"

    def test_accepts_inline_yaml_string(self):
        yaml_doc = """
- a: attention
  b: self-attention
  label: same
"""
        pairs = load_concept_pairs(yaml_doc)
        assert len(pairs) == 1
        assert pairs[0].label == "same"

    def test_missing_file_raises(self):
        with pytest.raises(CalibrationError, match="not found"):
            load_concept_pairs(Path("/tmp/does-not-exist-xyz.yml"))

    def test_empty_string_raises(self):
        with pytest.raises(CalibrationError, match="empty"):
            load_concept_pairs("")

    def test_root_must_be_list(self):
        with pytest.raises(CalibrationError, match="list"):
            load_concept_pairs({"a": "x", "b": "y", "label": "same"})

    def test_missing_label_raises(self):
        with pytest.raises(CalibrationError, match="label"):
            load_concept_pairs([{"a": "x", "b": "y"}])

    def test_invalid_label_raises(self):
        with pytest.raises(CalibrationError, match="label"):
            load_concept_pairs(
                [{"a": "x", "b": "y", "label": "sorta"}]
            )

    def test_missing_names_raise(self):
        with pytest.raises(CalibrationError, match="'a' is required"):
            load_concept_pairs([{"b": "y", "label": "same"}])
        with pytest.raises(CalibrationError, match="'b' is required"):
            load_concept_pairs([{"a": "x", "label": "same"}])


# =============================================================================
# Cosine
# =============================================================================


class TestCosineSimilarity:
    def test_identical(self):
        v = [1.0, 2.0, 3.0]
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal(self):
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_zero_vector(self):
        assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])


# =============================================================================
# Threshold analysis
# =============================================================================


def _p(a: str, b: str, label: str) -> ConceptPair:
    return ConceptPair(a=a, b=b, label=label)


class TestAnalyzeThresholds:
    def test_returns_default_sweep(self):
        scored = [
            ScoredPair(pair=_p("x1", "y1", "same"), score=0.92),
            ScoredPair(pair=_p("x2", "y2", "different"), score=0.5),
        ]
        rows = analyze_thresholds(scored)
        assert len(rows) == len(DEFAULT_THRESHOLDS)
        assert {r.threshold for r in rows} == set(DEFAULT_THRESHOLDS)

    def test_confusion_matrix_at_threshold(self):
        scored = [
            ScoredPair(pair=_p("s1", "s2", "same"), score=0.95),       # TP at 0.90
            ScoredPair(pair=_p("s3", "s4", "same"), score=0.85),       # FN at 0.90
            ScoredPair(pair=_p("d1", "d2", "different"), score=0.92),  # FP at 0.90
            ScoredPair(pair=_p("d3", "d4", "different"), score=0.50),  # TN at 0.90
        ]
        rows = analyze_thresholds(scored, thresholds=[0.90])
        row = rows[0]
        assert (row.true_positive, row.false_positive) == (1, 1)
        assert (row.true_negative, row.false_negative) == (1, 1)
        assert row.precision == pytest.approx(0.5)
        assert row.recall == pytest.approx(0.5)
        assert row.f1 == pytest.approx(0.5)

    def test_perfect_separation_at_threshold(self):
        scored = [
            ScoredPair(pair=_p("s1", "s2", "same"), score=0.99),
            ScoredPair(pair=_p("s3", "s4", "same"), score=0.97),
            ScoredPair(pair=_p("d1", "d2", "different"), score=0.40),
            ScoredPair(pair=_p("d3", "d4", "different"), score=0.20),
        ]
        rows = analyze_thresholds(scored, thresholds=[0.90])
        row = rows[0]
        assert row.precision == pytest.approx(1.0)
        assert row.recall == pytest.approx(1.0)
        assert row.f1 == pytest.approx(1.0)
        assert (row.true_positive, row.false_positive) == (2, 0)
        assert (row.true_negative, row.false_negative) == (2, 0)

    def test_no_positives_predicted(self):
        scored = [ScoredPair(pair=_p("s1", "s2", "same"), score=0.1)]
        rows = analyze_thresholds(scored, thresholds=[0.90])
        row = rows[0]
        assert row.precision == 0.0
        assert row.recall == 0.0
        assert row.f1 == 0.0

    def test_empty_input_raises(self):
        with pytest.raises(CalibrationError):
            analyze_thresholds([])


# =============================================================================
# Threshold recommendation
# =============================================================================


class TestRecommendThreshold:
    def _row(self, t, p, r, f1=None):
        """Quick ThresholdResult builder with implied F1."""
        if f1 is None:
            f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        from agentic_kg.knowledge_graph.calibration import ThresholdResult

        return ThresholdResult(
            threshold=t,
            precision=p,
            recall=r,
            f1=f1,
            true_positive=0,
            false_positive=0,
            true_negative=0,
            false_negative=0,
        )

    def test_picks_f1_max(self):
        rows = [
            self._row(0.70, 0.50, 1.00),  # F1 ≈ 0.667
            self._row(0.90, 0.90, 0.90),  # F1 = 0.90 — winner
            self._row(0.95, 1.00, 0.50),  # F1 ≈ 0.667
        ]
        t, f1 = recommend_threshold(rows)
        assert t == 0.90
        assert f1 == pytest.approx(0.90)

    def test_tie_breaks_by_precision_then_threshold(self):
        rows = [
            self._row(0.88, 0.90, 0.90, f1=0.90),
            self._row(0.90, 0.90, 0.90, f1=0.90),  # same F1, same precision
            self._row(0.92, 0.95, 0.86, f1=0.90),  # same F1, higher precision
        ]
        t, _ = recommend_threshold(rows)
        assert t == 0.92

    def test_returns_none_when_all_zero(self):
        rows = [self._row(0.90, 0.0, 0.0, f1=0.0)]
        t, f1 = recommend_threshold(rows)
        assert t is None
        assert f1 is None

    def test_empty_rows(self):
        t, f1 = recommend_threshold([])
        assert t is None
        assert f1 is None


# =============================================================================
# Report formatting
# =============================================================================


class TestFormatReport:
    def test_renders_threshold_rows_and_recommendation(self):
        from agentic_kg.knowledge_graph.calibration import CalibrationReport, ThresholdResult

        report = CalibrationReport(
            pairs_evaluated=10,
            positives=6,
            negatives=4,
            rows=[
                ThresholdResult(
                    threshold=0.90,
                    precision=0.9,
                    recall=0.9,
                    f1=0.9,
                    true_positive=5,
                    false_positive=1,
                    true_negative=3,
                    false_negative=1,
                ),
            ],
            recommended_threshold=0.90,
            recommended_f1=0.9,
        )
        text = format_report(report)
        assert "Pairs evaluated: 10" in text
        assert "0.90" in text
        assert "Recommended threshold: 0.90" in text

    def test_renders_when_no_recommendation(self):
        from agentic_kg.knowledge_graph.calibration import CalibrationReport

        report = CalibrationReport(
            pairs_evaluated=0,
            positives=0,
            negatives=0,
            rows=[],
            recommended_threshold=None,
            recommended_f1=None,
        )
        text = format_report(report)
        assert "No threshold produced any positive predictions." in text
