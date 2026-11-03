"""Matcher: literal/normalized strings, span IoU, embeddings, determinism."""

from __future__ import annotations

import pytest
from agentic_kg.evaluation.matching import (
    MatchConfig,
    match_group,
    match_items,
    span_iou,
)
from agentic_kg.evaluation.schema import AnnotationRecord, CharSpan, FieldType


def _item(
    text: str,
    *,
    field: FieldType = FieldType.PROBLEM,
    section: str = "introduction",
    span: tuple[int, int] | None = None,
    quote: str | None = None,
    paper: str = "doi:one",
    aliases: tuple[str, ...] = (),
    annotator: str = "gold",
) -> AnnotationRecord:
    return AnnotationRecord(
        paper_doi=paper,
        field_type=field,
        section=section,
        text=text,
        aliases=aliases,
        char_span=CharSpan(start=span[0], end=span[1]) if span else None,
        evidence_quote=quote,
        annotator_id=annotator,
    )


class TestSpanIou:
    def test_identical(self):
        assert span_iou(CharSpan(start=0, end=10), CharSpan(start=0, end=10)) == 1.0

    def test_disjoint(self):
        assert span_iou(CharSpan(start=0, end=5), CharSpan(start=10, end=20)) == 0.0

    def test_half_overlap(self):
        # [0,10) and [5,15): intersection 5, union 15.
        assert span_iou(CharSpan(start=0, end=10), CharSpan(start=5, end=15)) == pytest.approx(
            1 / 3
        )

    def test_missing_side(self):
        assert span_iou(None, CharSpan(start=0, end=1)) is None


class TestStringMatching:
    def test_exact_normalized_match(self):
        gold = [_item("Entity resolution")]
        pred = [_item("entity   resolution", annotator="pred")]
        result = match_group(gold, pred)
        assert result.tp == 1
        assert result.pairs[0].method == "string"

    def test_ligature_and_case(self):
        gold = [_item("reiﬁcation")]
        pred = [_item("Reification", annotator="pred")]
        assert match_group(gold, pred).tp == 1

    def test_alias_matches(self):
        gold = [_item("entity merging", aliases=("entity resolution",))]
        pred = [_item("entity resolution", annotator="pred")]
        assert match_group(gold, pred).tp == 1

    def test_unrelated_is_miss_and_hallucination(self):
        gold = [_item("entity merging")]
        pred = [_item("negative sampling", annotator="pred")]
        result = match_group(gold, pred)
        assert result.tp == 0
        assert len(result.unmatched_gold) == 1
        assert len(result.unmatched_predictions) == 1

    def test_one_to_one_greedy_does_not_double_count(self):
        gold = [_item("entity merging")]
        pred = [
            _item("entity merging", annotator="pred"),
            _item("entity merging", annotator="pred"),
        ]
        result = match_group(gold, pred)
        assert result.tp == 1
        assert result.fp == 1


class TestSpanMatching:
    def test_span_match_when_text_differs(self):
        gold = [_item("A long problem statement", span=(100, 150))]
        pred = [_item("Terse restatement", span=(105, 145), annotator="pred")]
        result = match_group(gold, pred, MatchConfig(span_iou_threshold=0.5))
        assert result.tp == 1
        assert result.pairs[0].method == "span"

    def test_span_below_threshold_does_not_match(self):
        gold = [_item("A long problem statement", span=(100, 150))]
        pred = [_item("Terse restatement", span=(100, 110), annotator="pred")]
        result = match_group(gold, pred, MatchConfig(span_iou_threshold=0.5))
        assert result.tp == 0


class TestEmbeddingMatching:
    def test_embedding_flag_off_never_matches(self):
        gold = [_item("A long problem statement")]
        pred = [_item("Terse restatement", annotator="pred")]
        result = match_group(
            gold,
            pred,
            MatchConfig(
                embedding_enabled=False,
                similarity_fn=lambda left, right: 1.0,
            ),
        )
        assert result.tp == 0

    def test_embedding_flag_on_uses_similarity(self):
        gold = [_item("A long problem statement")]
        pred = [_item("Terse restatement", annotator="pred")]
        result = match_group(
            gold,
            pred,
            MatchConfig(
                embedding_enabled=True,
                embedding_threshold=0.8,
                similarity_fn=lambda left, right: 0.9,
            ),
        )
        assert result.tp == 1
        assert result.pairs[0].method == "embedding"

    def test_embedding_below_threshold_does_not_match(self):
        gold = [_item("A long problem statement")]
        pred = [_item("Terse restatement", annotator="pred")]
        result = match_group(
            gold,
            pred,
            MatchConfig(
                embedding_enabled=True,
                embedding_threshold=0.8,
                similarity_fn=lambda left, right: 0.5,
            ),
        )
        assert result.tp == 0


class TestClassification:
    def test_section_mismatch_is_still_a_tp(self):
        gold = [_item("entity merging", section="methods")]
        pred = [_item("entity merging", section="introduction", annotator="pred")]
        pair = match_group(gold, pred).pairs[0]
        assert pair.section_mismatch is True

    def test_missing_evidence_is_bad_link(self):
        gold = [_item("entity merging", quote="we merge entities")]
        pred = [_item("entity merging", annotator="pred")]
        pair = match_group(gold, pred).pairs[0]
        assert pair.bad_evidence_link is True

    def test_overlapping_quote_is_good_link(self):
        gold = [_item("entity merging", quote="we merge entities with the same meaning")]
        pred = [_item("entity merging", quote="we merge entities", annotator="pred")]
        pair = match_group(gold, pred).pairs[0]
        assert pair.bad_evidence_link is False

    def test_disjoint_spans_are_bad_link(self):
        gold = [_item("entity merging", span=(0, 10))]
        pred = [_item("entity merging", span=(100, 110), annotator="pred")]
        pair = match_group(gold, pred).pairs[0]
        assert pair.bad_evidence_link is True


class TestGroupingAndDeterminism:
    def test_groups_by_paper_and_field(self):
        gold = [
            _item("one", paper="doi:a", field=FieldType.PROBLEM),
            _item("two", paper="doi:b", field=FieldType.PROBLEM),
            _item("three", paper="doi:a", field=FieldType.DATASET),
        ]
        result = match_items(gold, [])
        assert len(result) == 3
        assert {group.field_type for group in result} == {"problem", "dataset"}

    def test_repeated_runs_assign_identically(self):
        gold = [_item("alpha"), _item("beta")]
        pred = [_item("alpha", annotator="pred"), _item("beta", annotator="pred")]
        first = [(pair.gold.text, pair.prediction.text) for pair in match_group(gold, pred).pairs]
        second = [(pair.gold.text, pair.prediction.text) for pair in match_group(gold, pred).pairs]
        assert first == second
