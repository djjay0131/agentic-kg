"""Report-integrity properties: the gate's escapes, and caveats a reader can see.

This PR's subject is that a report should not be able to mislead. Two classes of
defect follow from that and neither is caught by the metric tests:

* an **escape hatch that over-corrects** — a gate that lets through a value which
  is not actually decision-independent, publishing it as settled;
* a **caveat that exists only outside the report** — in a PR body or a commit
  message, i.e. nowhere that the person who opens the report six months from now
  will look.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agentic_kg.migration.evaluation.adapter import build_goldset
from agentic_kg.migration.evaluation.arms import (
    GOLD_ARM,
    LEGACY_ARM,
    build_legacy_arm,
    restrict_to_entity_type,
)
from agentic_kg.migration.evaluation.corpus import ArmEntity, ArmPaper
from agentic_kg.migration.evaluation.metrics import (
    CalibrationSample,
    CurationMetricProvider,
    NamedResourceDisposition,
    expected_calibration_error,
    named_resource_emissions_by_bucket,
)
from agentic_kg.migration.evaluation.runner import (
    _gate_precision,
    render_report,
    run_evaluation,
)
from kg_eval.metrics import evaluate_extraction


def _typed(arm_report, entity_type: str):
    return next(t for t in arm_report.typed if t.entity_type == entity_type)


# --------------------------------------------------------------------------
# V1 — the fp == 0 escape must not outrank the affected check
# --------------------------------------------------------------------------


def _one_correct_plus_one_named_resource(papers, surface_index, disposition):
    """An arm with a single correct Method and a single named-resource Method."""
    arm_papers = (
        ArmPaper(
            "cskg",
            "extracted",
            (
                ArmEntity("cskg", "methods", "entity merging"),  # real gold Method
                ArmEntity("cskg", "methods", "CS-KG"),  # inventoried named resource
            ),
        ),
    )
    arm = build_legacy_arm(
        arm_papers,
        surface_index,
        graded_slugs=frozenset({"cskg"}),
        disposition=disposition,
    )
    gold = build_goldset(
        papers, gold_set_id="t", buckets=("methods",), include_relations=False
    )
    metrics = evaluate_extraction(restrict_to_entity_type(arm.output, "Method"), gold)
    affected = named_resource_emissions_by_bucket(arm.outcomes).get("methods", 0)
    return metrics, affected


def test_zero_false_positives_alone_does_not_prove_decision_independence(
    papers, surface_index
) -> None:
    """`fp` is counted AFTER the disposition-dependent exclusion has run.

    So `fp == 0` can itself be an artefact of the exclusion under question. This
    arm scores fp=0 under PENDING (the named resource was dropped) and fp=1
    under option B — precision 1.000 vs 0.500. Letting the fp escape fire first
    publishes a decision-dependent value as settled: F1 one layer down.
    """
    pending, affected_pending = _one_correct_plus_one_named_resource(
        papers, surface_index, NamedResourceDisposition.PENDING
    )
    option_b, affected_b = _one_correct_plus_one_named_resource(
        papers, surface_index, NamedResourceDisposition.EXCLUDED_AS_FALSE_POSITIVE
    )

    # The premise: same arm, two dispositions, two different raw precisions.
    assert affected_pending == 1 and affected_b == 1
    assert pending.entity.fp == 0
    assert pending.entity.precision.value == pytest.approx(1.0)
    assert option_b.entity.fp == 1
    assert option_b.entity.precision.value == pytest.approx(0.5)

    # The gate must therefore null the PENDING value rather than publish 1.000.
    gated = _gate_precision(pending, NamedResourceDisposition.PENDING, affected_pending)
    assert gated.value is None
    assert "named-resource decision" in gated.note


def test_the_escape_still_fires_when_both_conditions_hold(report) -> None:
    """The control arm: no false positives AND no affected emissions.

    Over-correcting here would make the runner's own self-check unreadable.
    """
    control = report.arm(GOLD_ARM)
    for typed in control.typed:
        assert typed.metrics.entity.fp == 0
        assert typed.named_resource_emissions == 0
        assert typed.precision.value == pytest.approx(1.0)


def test_real_arm_types_with_no_fp_and_no_affected_still_publish(report) -> None:
    """Model has fp=0 and zero affected emissions, so 1.000 is genuinely settled."""
    model = _typed(report.arm(LEGACY_ARM), "Model")
    assert model.metrics.entity.fp == 0
    assert model.named_resource_emissions == 0
    assert model.precision.value == pytest.approx(1.0)


# --------------------------------------------------------------------------
# V3 — the report must not contradict itself one cell apart
# --------------------------------------------------------------------------


def test_a_gated_precision_marks_its_neighbouring_recall(report) -> None:
    """Where precision is nulled for A/C, the recall beside it says why it is not.

    The gate's note says options A and C "move recall's denominator". Publishing
    an ungated recall in the adjacent cell without explanation reads as a
    contradiction, and a reader who spots it has to distrust both numbers.
    """
    concept = _typed(report.arm(LEGACY_ARM), "ResearchConcept")
    assert concept.precision.value is None
    assert concept.recall.value is not None
    assert concept.recall_is_decision_sensitive

    # Types the decision cannot touch are not marked.
    for entity_type in ("Model", "Method", "Topic"):
        assert not _typed(report.arm(LEGACY_ARM), entity_type).recall_is_decision_sensitive


def test_the_report_explains_why_recall_is_published_and_precision_is_not(
    report,
) -> None:
    text = render_report(report)
    assert "†" in text
    assert "why recall is published where precision is not" in text
    assert "*today's* answer key" in text
    assert "valid-as-scoped" in text


# --------------------------------------------------------------------------
# Caveats must render in the report, not only in a PR body
# --------------------------------------------------------------------------


def test_the_one_paper_caveat_renders(report) -> None:
    """All 13 TPs come from cskg2; cskg contributes none, of any type.

    Every per-type recall is therefore a one-paper measurement, which belongs
    next to the numbers rather than in a caveats appendix nobody opens.
    """
    legacy = report.arm(LEGACY_ARM)
    contributing = {s for t in legacy.typed for s in t.contributing_slugs}
    assert contributing == {"cskg2"}
    assert sum(t.metrics.entity.tp for t in legacy.typed) == 13

    text = render_report(report)
    assert "Effectively a one-paper measurement" in text
    assert "`cskg` contributes none" in text
    assert "no cross-paper variance" in text


def test_the_topic_persistence_caveat_renders(report) -> None:
    """Topic 0.000 is a BELONGS_TO persistence bug, not absent extraction.

    Without this, 0.000 reads as "no topic extraction exists" — false, and the
    same misreading the type-insensitive column corrects for ResearchConcept.
    """
    topic = _typed(report.arm(LEGACY_ARM), "Topic")
    assert topic.recall.value == pytest.approx(0.0)
    assert topic.metrics.entity.tp == 0

    text = render_report(report)
    assert "persistence bug, not absent extraction" in text
    assert "BELONGS_TO" in text
    assert "topics_linked" in text


def test_contributing_papers_appear_in_the_table(report) -> None:
    text = render_report(report)
    assert "Papers contributing" in text
    assert "1 of 2 (cskg2)" in text
    assert "0 of 2" in text  # Topic contributes nothing anywhere


def test_the_caveats_are_derived_not_hardcoded(
    chain_root: Path, importer_output_dir: Path
) -> None:
    """Mutation: an arm that scores on both papers must NOT get the caveat."""
    both = run_evaluation(
        chain_root=chain_root,
        importer_output_dir=importer_output_dir,
        new_arm_papers=(
            ArmPaper("cskg", "extracted", (ArmEntity("cskg", "methods", "lemmatization"),)),
            ArmPaper(
                "cskg2", "extracted", (ArmEntity("cskg2", "methods", "lemmatization"),)
            ),
        ),
    )
    method = _typed(both.arm("new"), "Method")
    assert set(method.contributing_slugs) == {"cskg", "cskg2"}
    # The legacy arm still carries it, so the caveat is per-arm, not global.
    legacy_text = render_report(both)
    assert "Effectively a one-paper measurement" in legacy_text


# --------------------------------------------------------------------------
# V4 — validate the estimator inputs
# --------------------------------------------------------------------------


def test_ece_of_an_empty_sample_raises_rather_than_returning_zero() -> None:
    """0.0 is the BEST possible calibration score.

    Returning it for "nothing was measured" is this package's own anti-pattern
    sitting in its own new code.
    """
    with pytest.raises(ValueError, match="empty sample"):
        expected_calibration_error([])


def test_ece_rejects_a_non_positive_bin_count() -> None:
    with pytest.raises(ValueError, match="bins must be >= 1"):
        expected_calibration_error([CalibrationSample(0.5, True)], bins=0)


@pytest.mark.parametrize("bad", [-0.5, 1.7, -0.0001, 1.0001])
def test_confidence_outside_zero_one_is_rejected(bad: float) -> None:
    """Both failure modes are silent in a binned estimator.

    A negative confidence indexes a bin from the END of the list (−0.5 lands in
    the top bin, inverting the observation); a value above 1 is clamped into the
    top bin, understating the miscalibration it is evidence of.
    """
    with pytest.raises(ValueError, match=r"confidence must be in \[0, 1\]"):
        CalibrationSample(bad, True)


def test_valid_confidences_at_the_boundaries_are_accepted() -> None:
    assert CalibrationSample(0.0, False).confidence == 0.0
    assert CalibrationSample(1.0, True).confidence == 1.0


def test_negative_review_count_is_rejected() -> None:
    with pytest.raises(ValueError, match="review_count must be >= 0"):
        CurationMetricProvider(gold_item_count=53, review_count=-1)


def test_review_count_exceeding_candidates_is_rejected_not_clamped() -> None:
    """A clamp would hide the inconsistency behind a plausible-looking rate."""
    from test_diagnostics import _arm_output, _goldset

    provider = CurationMetricProvider(gold_item_count=53, review_count=5)
    with pytest.raises(ValueError, match="different populations"):
        provider.evaluate(_arm_output(n_candidates=2), _goldset())


def test_invalid_provider_construction_is_rejected() -> None:
    with pytest.raises(ValueError, match="calibration_bins must be >= 1"):
        CurationMetricProvider(gold_item_count=53, calibration_bins=0)
    with pytest.raises(ValueError, match="gold_item_count must be >= 0"):
        CurationMetricProvider(gold_item_count=-1)


# --------------------------------------------------------------------------
# The three new estimators stay dark on this corpus
# --------------------------------------------------------------------------


def test_no_new_number_reaches_the_report(report) -> None:
    """New code must produce no new claims at the end of a fix loop.

    The runner passes None for all three estimator inputs, so every one of them
    is still an honest null on this corpus. Adding machinery is not the same as
    having measured something with it.
    """
    for arm_id in (LEGACY_ARM, GOLD_ARM):
        metrics = report.arm(arm_id).provider_metrics
        for key in ("curation.false_merge_rate", "curation.false_split_rate",
                    "curation.calibration.ece", "curation.review.rate"):
            assert metrics[key].value is None, (arm_id, key)
            assert metrics[key].sufficient is False
