"""Metrics: hand-computed micro/macro PRF, section stratification, bootstrap."""

from __future__ import annotations

import pytest
from agentic_kg.evaluation.matching import MatchConfig
from agentic_kg.evaluation.metrics import (
    DEFAULT_SEED,
    build_metrics,
    build_report_data,
)
from agentic_kg.evaluation.schema import (
    AnnotationRecord,
    CoverageRecord,
    FieldType,
)


def _gold(
    paper: str,
    text: str,
    *,
    field: FieldType = FieldType.PROBLEM,
    section: str = "introduction",
    quote: str | None = None,
    implicit: bool = False,
) -> AnnotationRecord:
    return AnnotationRecord(
        paper_doi=paper,
        field_type=field,
        section=section,
        text=text,
        evidence_quote=quote,
        implicit=implicit,
        annotator_id="gold",
    )


def _pred(
    paper: str,
    text: str,
    *,
    field: FieldType = FieldType.PROBLEM,
    section: str = "introduction",
    quote: str | None = None,
) -> AnnotationRecord:
    return AnnotationRecord(
        paper_doi=paper,
        field_type=field,
        section=section,
        text=text,
        evidence_quote=quote,
        annotator_id="prediction",
    )


def _coverage(paper: str, *fields: FieldType) -> CoverageRecord:
    return CoverageRecord(
        paper_doi=paper,
        annotator_id="gold",
        fields=tuple(fields),
    )


def _metrics(gold, coverage, preds, **kwargs):
    data = build_report_data(gold, coverage, preds, match_config=MatchConfig(), **kwargs)
    return data, build_metrics(data, n_resamples=0, bootstrap=False)


class TestMicroMacro:
    def test_hand_computed_two_papers(self):
        gold = [
            _gold("doi:a", "problem one"),
            _gold("doi:a", "problem two"),
            _gold("doi:b", "problem three"),
        ]
        preds = [
            _pred("doi:a", "problem one"),
            _pred("doi:b", "problem three"),
            _pred("doi:b", "hallucinated four"),
        ]
        coverage = [_coverage("doi:a", FieldType.PROBLEM), _coverage("doi:b", FieldType.PROBLEM)]
        _, metrics = _metrics(gold, coverage, preds)
        micro = metrics["overall"]["micro"]
        macro = metrics["overall"]["macro"]
        # Micro: tp=2 fp=1 fn=1 -> P=2/3, R=2/3, F1=2/3.
        assert micro["precision"].value == pytest.approx(2 / 3)
        assert micro["recall"].value == pytest.approx(2 / 3)
        assert micro["f1"].value == pytest.approx(2 / 3)
        # Paper A: P=1 R=1/2 F1=2/3. Paper B: P=1/2 R=1 F1=2/3.
        assert macro["precision"].value == pytest.approx(0.75)
        assert macro["recall"].value == pytest.approx(0.75)
        assert macro["f1"].value == pytest.approx(2 / 3)
        assert (micro["tp"], micro["fp"], micro["fn"]) == (2, 1, 1)

    def test_precision_is_null_when_nothing_predicted(self):
        gold = [_gold("doi:a", "problem one")]
        coverage = [_coverage("doi:a", FieldType.PROBLEM)]
        _, metrics = _metrics(gold, coverage, [])
        precision = metrics["overall"]["micro"]["precision"]
        assert precision.value is None
        assert "no predictions" in precision.note

    def test_recall_is_null_when_nothing_annotated(self):
        preds = [_pred("doi:a", "problem one")]
        coverage = [_coverage("doi:a", FieldType.PROBLEM)]
        _, metrics = _metrics([], coverage, preds)
        recall = metrics["overall"]["micro"]["recall"]
        assert recall.value is None
        assert "no gold" in recall.note


class TestStratification:
    def test_by_field_keeps_fields_separate(self):
        gold = [
            _gold("doi:a", "problem", field=FieldType.PROBLEM),
            _gold("doi:a", "assumption", field=FieldType.ASSUMPTION),
        ]
        preds = [
            _pred("doi:a", "problem", field=FieldType.PROBLEM),
            _pred("doi:a", "different assumption", field=FieldType.ASSUMPTION),
        ]
        coverage = [_coverage("doi:a", FieldType.PROBLEM, FieldType.ASSUMPTION)]
        _, metrics = _metrics(gold, coverage, preds)
        assert metrics["by_field"]["problem"]["micro"]["precision"].value == 1.0
        assert metrics["by_field"]["assumption"]["micro"]["recall"].value == 0.0

    def test_by_section_attributes_wrong_section_tp_to_gold_section(self):
        gold = [_gold("doi:a", "same text", section="abstract")]
        preds = [
            _pred("doi:a", "same text", section="methods"),
            _pred("doi:a", "unmatched methods prediction", section="methods"),
        ]
        coverage = [_coverage("doi:a", FieldType.PROBLEM)]
        data, metrics = _metrics(gold, coverage, preds)
        assert data.matches[0].pairs[0].section_mismatch is True
        assert metrics["by_section"]["abstract"]["micro"]["recall"].value == 1.0
        assert metrics["by_section"]["methods"]["micro"]["tp"] == 0
        assert metrics["by_section"]["methods"]["micro"]["fp"] == 1
        assert metrics["error_buckets"]["counts"]["wrong_section"] == 1

    def test_ungraded_field_is_honest_null_not_false_positive(self):
        gold = [_gold("doi:a", "problem")]
        preds = [
            _pred("doi:a", "problem"),
            _pred("doi:a", "SciBERT", field=FieldType.DATASET),
        ]
        coverage = [_coverage("doi:a", FieldType.PROBLEM)]
        data, metrics = _metrics(gold, coverage, preds)
        assert data.ungraded_predictions == 1
        assert metrics["by_field"]["dataset"]["micro"]["precision"].value is None
        assert metrics["overall"]["micro"]["tp"] == 1
        assert metrics["overall"]["micro"]["fp"] == 0


class TestBootstrap:
    def test_same_seed_is_reproducible(self):
        gold = [
            _gold("doi:a", "one"),
            _gold("doi:b", "two"),
            _gold("doi:c", "three"),
        ]
        preds = [
            _pred("doi:a", "one"),
            _pred("doi:b", "two"),
            _pred("doi:c", "hallucinated"),
        ]
        coverage = [
            _coverage("doi:a", FieldType.PROBLEM),
            _coverage("doi:b", FieldType.PROBLEM),
            _coverage("doi:c", FieldType.PROBLEM),
        ]
        data = build_report_data(gold, coverage, preds)
        first = build_metrics(data, n_resamples=100, seed=DEFAULT_SEED)
        second = build_metrics(data, n_resamples=100, seed=DEFAULT_SEED)
        first_precision = first["overall"]["micro"]["precision"]
        second_precision = second["overall"]["micro"]["precision"]
        assert first_precision.ci_low == second_precision.ci_low
        assert first_precision.ci_high == second_precision.ci_high

    def test_too_few_papers_withholds_interval(self):
        gold = [_gold("doi:a", "one")]
        coverage = [_coverage("doi:a", FieldType.PROBLEM)]
        _, metrics = _metrics(gold, coverage, [_pred("doi:a", "one")])
        assert metrics["overall"]["micro"]["precision"].ci_low is None


class TestErrorBuckets:
    def test_implicit_assumption_miss_is_its_own_bucket(self):
        gold = [
            _gold(
                "doi:a",
                "the corpus is English",
                field=FieldType.ASSUMPTION,
                implicit=True,
            )
        ]
        coverage = [_coverage("doi:a", FieldType.ASSUMPTION)]
        _, metrics = _metrics(gold, coverage, [])
        assert metrics["error_buckets"]["counts"]["missed_implicit_assumption"] == 1
        assert "missed_field" not in metrics["error_buckets"]["counts"]

    def test_hallucination_and_bad_evidence_are_separate(self):
        gold = [_gold("doi:a", "entity merging", quote="we merge entities")]
        preds = [
            _pred("doi:a", "entity merging"),  # matched but no evidence
            _pred("doi:a", "a made up problem"),  # unmatched
        ]
        coverage = [_coverage("doi:a", FieldType.PROBLEM)]
        _, metrics = _metrics(gold, coverage, preds)
        counts = metrics["error_buckets"]["counts"]
        assert counts["bad_evidence_link"] == 1
        assert counts["hallucinated_field"] == 1
