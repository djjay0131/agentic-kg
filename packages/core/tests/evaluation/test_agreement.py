"""Agreement: hand-computed Cohen's kappa and Krippendorff's alpha."""

from __future__ import annotations

import pytest
from agentic_kg.evaluation.agreement import (
    cohen_kappa,
    compute_agreement,
    krippendorff_alpha_nominal,
)
from agentic_kg.evaluation.schema import (
    AnnotationRecord,
    CoverageRecord,
    FieldType,
)


def _record(paper: str, text: str, annotator: str, field=FieldType.PROBLEM):
    return AnnotationRecord(
        paper_doi=paper,
        field_type=field,
        text=text,
        annotator_id=annotator,
    )


def _coverage(paper: str, annotator: str, field=FieldType.PROBLEM):
    return CoverageRecord(paper_doi=paper, annotator_id=annotator, fields=(field,))


class TestCohenKappa:
    def test_perfect_agreement(self):
        value, observed, expected, note = cohen_kappa([1, 1, 0, 0], [1, 1, 0, 0])
        assert value == 1.0
        assert observed == 1.0
        assert note is None

    def test_hand_computed_half_agreement(self):
        # Two items: both annotators say 1 on item one; split on item two.
        # po = 0.5; marginals 1.0 and 0.5 -> pe = 0.5; kappa = 0.
        value, observed, expected, note = cohen_kappa([1, 1], [1, 0])
        assert observed == 0.5
        assert expected == 0.5
        assert value == pytest.approx(0.0)
        assert note is None

    def test_no_variation_is_undefined(self):
        value, observed, expected, note = cohen_kappa([1, 1], [1, 1])
        assert value is None
        assert "no label variation" in note

    def test_empty_is_undefined(self):
        value, observed, expected, note = cohen_kappa([], [])
        assert value is None
        assert "no commonly labelled" in note


class TestKrippendorff:
    def test_hand_computed_mixed_units(self):
        # Units: (1,1) and (1,0). N=4 ratings.
        # Do = 2/4 = 0.5; De = 6/12 = 0.5; alpha = 0.
        value, observed, expected, units, ratings, note = krippendorff_alpha_nominal(
            [[1, 1], [1, 0]]
        )
        assert observed == pytest.approx(0.5)
        assert expected == pytest.approx(0.5)
        assert value == pytest.approx(0.0)
        assert (units, ratings) == (2, 4)
        assert note is None

    def test_perfect_agreement(self):
        value, _, _, _, _, _ = krippendorff_alpha_nominal([[1, 1], [0, 0]])
        assert value == pytest.approx(1.0)

    def test_missing_ratings_are_allowed(self):
        value, _, _, _, _, _ = krippendorff_alpha_nominal([[1, 1, None], [0, 0, None]])
        assert value == pytest.approx(1.0)

    def test_single_rating_per_unit_is_undefined(self):
        value, _, _, _, _, note = krippendorff_alpha_nominal([[1], [0]])
        assert value is None
        assert "single rating" in note


class TestComputeAgreement:
    def test_two_annotators_use_union_and_coverage(self):
        records = [
            _record("doi:a", "shared", "human"),
            _record("doi:a", "only human", "human"),
            _record("doi:a", "shared", "claude"),
        ]
        coverage = [_coverage("doi:a", "human"), _coverage("doi:a", "claude")]
        report = compute_agreement(records, coverage)
        assert report.annotators == ("claude", "human")
        pair = report.pairwise_kappa[0]
        # Items: shared (1,1), only human (1,0) -> kappa 0.
        assert pair.n_items == 2
        assert pair.value == pytest.approx(0.0)
        assert report.krippendorff_alpha.value == pytest.approx(0.0)

    def test_uncovered_cell_is_not_a_negative_label(self):
        records = [
            _record("doi:a", "shared", "human"),
            _record("doi:a", "shared", "claude"),
            _record("doi:b", "human only paper", "human"),
        ]
        coverage = [
            _coverage("doi:a", "human"),
            _coverage("doi:a", "claude"),
            _coverage("doi:b", "human"),
        ]
        report = compute_agreement(records, coverage)
        assert report.pairwise_kappa[0].n_items == 1
        # The one shared item is present for both, so kappa is undefined on
        # variation rather than being polluted by the uncovered paper.
        assert report.pairwise_kappa[0].value is None

    def test_single_annotator_is_undefined(self):
        records = [_record("doi:a", "shared", "human")]
        coverage = [_coverage("doi:a", "human")]
        report = compute_agreement(records, coverage)
        assert report.pairwise_kappa == ()
        assert report.krippendorff_alpha is None
        assert "fewer than two" in report.notes[0]
