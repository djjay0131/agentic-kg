"""Type confusion, the renderer's honest-null obligation, and liftable nulls.

Three properties that a strict scorecard alone does not give you:

* **Type confusion.** A mis-typed entity is a real error and scores as a miss.
  But strict recall cannot distinguish "never found it" from "found it and filed
  it wrong", and those call for different work. The diagnostic sits beside the
  score, never in place of it.
* **The renderer honours honest-null too.** The rule is a property of the
  rendered report, not only of the model behind it.
* **Every null here is liftable.** A note that can only ever change wording is
  not "not yet measured"; it is "not implemented", and dressing that as a null
  misrepresents it. Each null names an input, and supplying the input yields a
  number.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agentic_kg.migration.evaluation.adapter import cross_type_matches
from agentic_kg.migration.evaluation.arms import GOLD_ARM, LEGACY_ARM
from agentic_kg.migration.evaluation.corpus import ArmEntity, ArmPaper, CorpusCoverage
from agentic_kg.migration.evaluation.metrics import (
    CalibrationSample,
    CurationMetricProvider,
    MentionClustering,
    expected_calibration_error,
)
from agentic_kg.migration.evaluation.runner import _fmt_ratio, render_report
from kg_contracts.candidates import (
    CandidateScores,
    EntityCandidate,
    SourceCoordinates,
)
from kg_contracts.identity import EntityRef
from kg_eval.arms import ArmConfig, ArmOutput
from kg_eval.goldset import GoldEntity, GoldSet


def _typed(arm_report, entity_type: str):
    return next(t for t in arm_report.typed if t.entity_type == entity_type)


def _goldset() -> GoldSet:
    return GoldSet(
        gold_set_id="t",
        entities=(GoldEntity(entity_type="Model", semantic_key="x/y"),),
    )


def _arm_output(
    n_candidates: int = 1, abstained: int = 0, attempted: int = 8
) -> ArmOutput:
    candidates = tuple(
        EntityCandidate(
            graph_id="g",
            producer="t",
            producer_run_id="r",
            ontology_version="v",
            source_coordinates=SourceCoordinates(source_type="s", locator="l"),
            semantic_key=f"x/y{i}",
            entity_type="Model",
            aliases=(EntityRef(entity_type="Model", namespace="ns", key=f"y{i}"),),
            scores=CandidateScores(extraction_confidence=0.5, source_reliability=0.5),
        )
        for i in range(n_candidates)
    )
    return ArmOutput(
        arm=ArmConfig(arm_id="t"),
        candidates=candidates,
        attempted=attempted,
        abstained=abstained,
    )


# --------------------------------------------------------------------------
# Type confusion
# --------------------------------------------------------------------------


def test_type_confusion_is_reported_alongside_strict_recall(report) -> None:
    """Strict recall stays the score; the diagnostic sits beside it.

    Without this, ResearchConcept recall of 0.071 reads as "the extractor is
    blind to research concepts" when the data says "it found three and mis-typed
    two of them". Different defect, far more tractable fix.
    """
    legacy = report.arm(LEGACY_ARM)

    concept = _typed(legacy, "ResearchConcept")
    assert concept.recall.value == pytest.approx(1 / 14)
    assert concept.type_confusion_count == 2
    assert concept.type_insensitive_recall.value == pytest.approx(3 / 14)

    model = _typed(legacy, "Model")
    assert model.recall.value == pytest.approx(8 / 12)
    assert model.type_confusion_count == 1
    assert model.type_insensitive_recall.value == pytest.approx(9 / 12)

    for entity_type in ("Topic", "Method"):
        typed = _typed(legacy, entity_type)
        assert typed.type_confusion_count == 0
        assert typed.type_insensitive_recall.value == pytest.approx(typed.recall.value)


def test_the_mis_typed_entities_are_named(report) -> None:
    """A count alone is not actionable; the report says which entities."""
    legacy = report.arm(LEGACY_ARM)
    found = {
        (m.slug, m.gold_canonical, m.emitted_bucket)
        for t in legacy.typed
        for m in t.cross_type_hits
    }
    assert found == {
        ("cskg", "information extraction pipeline", "methods"),
        ("cskg2", "reification", "methods"),
        ("cskg2", "CSO Classifier", "methods"),
    }


def test_a_gold_item_already_matched_strictly_is_not_double_counted(report) -> None:
    """`DyGIEpp` is emitted both as a Model (correct) and as "DyGIEpp Module".

    Counting the second would push type-insensitive recall above the truth.
    Model goes 8 -> 9, not 8 -> 10.
    """
    model = _typed(report.arm(LEGACY_ARM), "Model")
    assert "DyGIEpp" not in {m.gold_canonical for m in model.cross_type_hits}
    assert model.type_insensitive_recall.value == pytest.approx(9 / 12)


def test_type_insensitive_recall_is_bounded_and_never_below_strict(report) -> None:
    for arm in report.arms:
        for typed in arm.typed:
            value = typed.type_insensitive_recall.value
            if value is not None:
                assert 0.0 <= value <= 1.0
                assert value >= typed.recall.value


def test_type_confusion_moves_with_the_arm(surface_index) -> None:
    """Mutation: an arm that types everything correctly has zero confusion."""
    correct = (
        ArmPaper("cskg", "extracted", (ArmEntity("cskg", "concepts", "reification"),)),
    )
    assert cross_type_matches(correct, surface_index) == ()

    mistyped = (
        ArmPaper("cskg", "extracted", (ArmEntity("cskg", "methods", "reification"),)),
    )
    hits = cross_type_matches(mistyped, surface_index)
    assert len(hits) == 1
    assert hits[0].gold_bucket == "concepts"
    assert hits[0].emitted_bucket == "methods"
    assert hits[0].gold_canonical == "reification"


def test_control_arm_has_no_type_confusion(report) -> None:
    for typed in report.arm(GOLD_ARM).typed:
        assert typed.type_confusion_count == 0
        assert typed.type_insensitive_recall.value == pytest.approx(1.0)


def test_confusion_never_changes_the_strict_score(report) -> None:
    """The diagnostic is reported, not applied. TP counts stay strict."""
    legacy = report.arm(LEGACY_ARM)
    assert _typed(legacy, "ResearchConcept").metrics.entity.tp == 1
    assert _typed(legacy, "Model").metrics.entity.tp == 8


# --------------------------------------------------------------------------
# The renderer must not fabricate zeros
# --------------------------------------------------------------------------


def test_renderer_contains_no_none_to_zero_coalescing() -> None:
    """No `x or 0` anywhere in runner.py, checked on the AST.

    `f"{(x or 0.0):.3f}"` reads as a formatting convenience and prints 0.000 for
    an unmeasured value -- exactly the confusion `MetricValue` exists to
    prevent, one layer below where the policy is usually enforced.

    Parsed rather than grepped: a text scan trips over the very docstrings that
    warn against the pattern (it tripped over this module's own), and would
    equally miss `x or 0` spelled across a line break. The AST sees the
    expression and nothing else.
    """
    import ast

    import agentic_kg.migration.evaluation.runner as runner_module

    tree = ast.parse(Path(runner_module.__file__).read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
            continue
        for value in node.values[1:]:
            if isinstance(value, ast.Constant) and value.value in (0, 0.0, "", None, False):
                offenders.append(f"line {node.lineno}: `... or {value.value!r}`")
    assert offenders == [], (
        "coalescing to a falsy default in runner.py: "
        f"{offenders}. Route optional numbers through _fmt/_fmt_ratio so an "
        "unmeasured value renders as a null, not as a zero."
    )


def test_fmt_ratio_renders_none_as_a_null_and_zero_as_zero() -> None:
    assert _fmt_ratio(None, "no attempted inputs") == "insufficient (no attempted inputs)"
    assert _fmt_ratio(0.0, "x") == "0.000"
    assert _fmt_ratio(0.5, "x") == "0.500"


def test_an_unmeasurable_corpus_rate_renders_as_a_null_not_zero() -> None:
    """Asserted on rendered output, not on a substring of the whole report."""
    empty = CorpusCoverage(
        corpus_attempted=0, corpus_failed=0, graded_attempted=0, graded_failed=0
    )
    assert empty.corpus_failure_rate is None
    rendered = _fmt_ratio(empty.corpus_failure_rate, "no attempted inputs")
    assert "0.000" not in rendered
    assert rendered.startswith("insufficient")


def test_rendered_report_shows_both_recalls_and_the_confusion_list(report) -> None:
    text = render_report(report)
    assert "Recall (strict)" in text
    assert "Recall (type-insensitive)" in text
    assert "Type confusion" in text
    assert "information extraction pipeline" in text
    assert "was emitted as Method" in text


# --------------------------------------------------------------------------
# Liftable nulls
# --------------------------------------------------------------------------


def test_false_merge_and_split_compute_when_clusters_are_supplied() -> None:
    """Gold {0,1},{2,3}; predicted {0,1,2},{3}.

    Predicted pairs: (0,1),(0,2),(1,2) = 3, of which (0,2),(1,2) are not gold
    -> FM 2/3. Gold pairs: (0,1),(2,3) = 2, of which (2,3) was split -> FS 1/2.
    """
    clustering = MentionClustering(
        gold=("a", "a", "b", "b"), predicted=("x", "x", "x", "y")
    )
    metrics = CurationMetricProvider(
        gold_item_count=53, clustering=clustering
    ).evaluate(_arm_output(), _goldset())
    assert metrics["false_merge_rate"].value == pytest.approx(2 / 3)
    assert metrics["false_split_rate"].value == pytest.approx(1 / 2)


def test_cluster_metrics_are_null_without_labels_and_name_the_input() -> None:
    metrics = CurationMetricProvider(gold_item_count=53).evaluate(
        _arm_output(), _goldset()
    )
    for key in ("false_merge_rate", "false_split_rate"):
        assert metrics[key].value is None
        assert "no mention-level cluster labels" in metrics[key].note
        assert "Supply a MentionClustering" in metrics[key].note


def test_a_perfect_clustering_scores_zero_on_both() -> None:
    """Mutation check: the estimator is not returning a constant."""
    perfect = MentionClustering(
        gold=("a", "a", "b", "b"), predicted=("a", "a", "b", "b")
    )
    metrics = CurationMetricProvider(
        gold_item_count=53, clustering=perfect
    ).evaluate(_arm_output(), _goldset())
    assert metrics["false_merge_rate"].value == pytest.approx(0.0)
    assert metrics["false_split_rate"].value == pytest.approx(0.0)


def test_misaligned_clustering_is_rejected_not_silently_truncated() -> None:
    with pytest.raises(ValueError, match="misaligned"):
        MentionClustering(gold=("a", "b"), predicted=("a",))


def test_calibration_computes_when_confidences_and_sample_size_exist() -> None:
    perfect = [CalibrationSample(1.0, True) for _ in range(100)]
    assert expected_calibration_error(perfect) == pytest.approx(0.0)

    overconfident = [CalibrationSample(1.0, False) for _ in range(100)]
    assert expected_calibration_error(overconfident) == pytest.approx(1.0)

    metrics = CurationMetricProvider(gold_item_count=53, calibration=perfect).evaluate(
        _arm_output(), _goldset()
    )
    assert metrics["calibration.ece"].value == pytest.approx(0.0)


def test_calibration_names_its_two_disqualifiers_independently() -> None:
    note = (
        CurationMetricProvider(gold_item_count=53)
        .evaluate(_arm_output(), _goldset())["calibration.ece"]
        .note
    )
    assert "no per-candidate confidence" in note
    assert "53 observations" in note

    few = [CalibrationSample(0.5, True) for _ in range(5)]
    note_few = (
        CurationMetricProvider(gold_item_count=53, calibration=few)
        .evaluate(_arm_output(), _goldset())["calibration.ece"]
        .note
    )
    assert "no per-candidate confidence" not in note_few
    assert "5 observations" in note_few


def test_review_rate_is_not_the_abstention_rate() -> None:
    """An earlier draft would have published abstention under a second name.

    `review.rate` is reviewed-candidates over candidates-produced — a different
    quantity from `abstention_rate`, which kg_eval already reports separately.
    """
    output = _arm_output(n_candidates=4, abstained=3, attempted=8)
    metrics = CurationMetricProvider(gold_item_count=53, review_count=1).evaluate(
        output, _goldset()
    )
    assert metrics["review.rate"].value == pytest.approx(1 / 4)
    assert output.abstained / output.attempted == pytest.approx(3 / 8)
    assert metrics["review.rate"].value != pytest.approx(3 / 8)


def test_review_rate_null_says_it_is_not_abstention() -> None:
    note = (
        CurationMetricProvider(gold_item_count=53)
        .evaluate(_arm_output(), _goldset())["review.rate"]
        .note
    )
    assert "NOT the abstention rate" in note
    assert "Supply review_count" in note


def test_unfolded_doi_drops_the_edge_rather_than_mis_scoring_it(
    chain_root: Path, importer_output_dir: Path, monkeypatch
) -> None:
    """Pins the DOI failure mode precisely: fn only, never fp.

    An unresolvable DOI is dropped by `_resolve_cited_slugs` before a candidate
    is ever built, so the edge vanishes instead of being graded wrong. Worth
    pinning because the two failures look nothing alike in a report: a false
    positive is visible, a silent drop just lowers recall and reads as a miss
    the importer never made.
    """
    import agentic_kg.migration.evaluation.corpus as corpus_module
    from agentic_kg.migration.evaluation.adapter import (
        build_candidates,
        build_goldset,
        build_surface_index,
        filter_candidates,
    )
    from kg_eval.matching import match_relations

    papers = corpus_module.load_reconciled_papers(chain_root)
    gold = build_goldset(papers, gold_set_id="t")
    index = build_surface_index(papers)

    # Without folding, the DOI->slug map no longer contains the uppercase form.
    monkeypatch.setattr(corpus_module, "normalize_doi", lambda d: d.strip())
    monkeypatch.setattr(
        corpus_module,
        "_DOI_TO_SLUG",
        {d.strip(): s for s, d in corpus_module.PAPER_DOIS.items()},
    )
    arm = [
        p
        for p in corpus_module.load_importer_output(importer_output_dir)
        if p.slug in corpus_module.RECONCILED_SLUGS
    ]
    assert [e for p in arm for e in p.citations] == [], (
        "the edge should have been dropped at resolution time"
    )

    outcomes = filter_candidates(arm, index)
    candidates, _ = build_candidates("t", arm, outcomes)
    match = match_relations(candidates, gold)
    assert (match.tp, match.fp, match.fn) == (0, 0, 2)
