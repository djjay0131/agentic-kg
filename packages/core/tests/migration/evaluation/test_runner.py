"""The runner: which metrics are live, which are honest nulls, and why.

The mutation tests here are the ones that matter most. A metric that never moves
is not a measurement, and an honest null that would have stayed null no matter
what the arm did is not honesty — it is an omission with a comment on it. So
every live metric is shown moving under a deliberately-wrong candidate set, and
every null is shown becoming a number once the thing it says is missing is
supplied.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agentic_kg.migration.evaluation.arms import (
    GOLD_ARM,
    LEGACY_ARM,
    NEW_ARM,
    ArmUnavailable,
    build_new_arm,
)
from agentic_kg.migration.evaluation.corpus import ArmEntity, ArmPaper
from agentic_kg.migration.evaluation.metrics import NamedResourceDisposition
from agentic_kg.migration.evaluation.runner import (
    render_kg_eval_json,
    render_report,
    run_evaluation,
)
from kg_eval.ablation import AblationVerdict
from kg_eval.bootstrap import BootstrapConfig


def _typed(arm_report, entity_type: str):
    return next(t for t in arm_report.typed if t.entity_type == entity_type)


# --------------------------------------------------------------------------
# The control arm — the runner's own self-check
# --------------------------------------------------------------------------


def test_gold_control_arm_scores_perfectly(report) -> None:
    """If the control is not perfect, every other number here is suspect.

    The control re-emits the gold set as candidates. Anything below 1.0 means
    the adapter builds candidate keys that do not line up with the gold keys it
    also builds, i.e. the runner is measuring itself.
    """
    control = report.arm(GOLD_ARM)
    assert control.available
    for entity_type in ("Topic", "ResearchConcept", "Model", "Method"):
        typed = _typed(control, entity_type)
        assert typed.recall.value == pytest.approx(1.0), entity_type
        assert typed.precision.value == pytest.approx(1.0), entity_type
        assert typed.metrics.entity.fp == 0 and typed.metrics.entity.fn == 0
    assert control.overall.relation.recall.value == pytest.approx(1.0)
    assert control.overall.relation.precision.value == pytest.approx(1.0)
    assert control.overall.evidence.span_coverage_rate.value == pytest.approx(1.0)
    assert control.overall.evidence.spans_checked == 49
    assert control.overall.hallucination_count == 0


# --------------------------------------------------------------------------
# Scope
# --------------------------------------------------------------------------


def test_report_states_the_real_scoreable_surface(report) -> None:
    assert report.scoreable_entities == 53
    assert report.scoreable_relations == 2
    assert report.relations_gold_both_ends == 1
    assert len(report.papers) == 2


def test_graded_and_corpus_denominators_are_kept_apart(report) -> None:
    """Metrics describe 2 papers; the corpus is 8. Mixing them is the trap.

    A failure rate over 8 printed beside a recall over 2 invites a reader to
    compare populations that have nothing to do with each other.
    """
    legacy = report.arm(LEGACY_ARM)
    assert legacy.coverage.graded_attempted == 2
    assert legacy.coverage.graded_failed == 0
    assert legacy.coverage.corpus_attempted == 8
    assert legacy.coverage.corpus_failed == 4
    assert legacy.coverage.corpus_failure_rate == pytest.approx(0.5)
    # The graded arm's own failure rate is over the graded scope, and is a real
    # measured 0.0: both reconciled papers extracted successfully.
    assert legacy.overall.failure_rate.value == pytest.approx(0.0)


# --------------------------------------------------------------------------
# Live metrics, shown moving
# --------------------------------------------------------------------------


def test_legacy_entity_recall_is_live_and_per_type(report) -> None:
    """Real numbers over the 53 gold entities, broken out by type."""
    legacy = report.arm(LEGACY_ARM)
    assert _typed(legacy, "Topic").recall.value == pytest.approx(0.0)
    assert _typed(legacy, "ResearchConcept").recall.value == pytest.approx(1 / 14)
    assert _typed(legacy, "Model").recall.value == pytest.approx(8 / 12)
    assert _typed(legacy, "Method").recall.value == pytest.approx(4 / 23)
    assert [t.gold_count for t in legacy.typed] == [4, 14, 12, 23]


def test_topic_recall_zero_is_measured_but_precision_is_null(report) -> None:
    """The two zeroes here mean different things and must render differently.

    Recall 0.0 is measured and true: the BELONGS_TO bug means no Topic is ever
    persisted, so all 4 gold topics are missed. Precision is *insufficient*, not
    0.0 — there are no Topic candidates, so there is no denominator, and a 0.0
    would assert that every topic the importer emitted was wrong.
    """
    typed = _typed(report.arm(LEGACY_ARM), "Topic")
    assert typed.recall.value == pytest.approx(0.0) and typed.recall.sufficient
    assert typed.precision.value is None
    assert "no entity candidates produced" in typed.precision.note


def test_citation_metrics_are_live(report) -> None:
    """1 of 2 gold edges found, and the one found is correct."""
    rel = report.arm(LEGACY_ARM).overall.relation
    assert rel.recall.value == pytest.approx(0.5)
    assert rel.precision.value == pytest.approx(1.0)
    assert (rel.tp, rel.fp, rel.fn) == (1, 0, 1)


def test_mutating_the_arm_moves_every_live_metric(
    chain_root: Path, importer_output_dir: Path, report
) -> None:
    """§9.0(v): a criterion must be able to fail for the reason it names.

    A deliberately-wrong 'new' arm — right shape, wrong content — must move
    recall, precision, the hallucination count and the evidence metrics. If the
    numbers do not move, they are not reading the candidates.
    """
    wrong = (
        ArmPaper(
            slug="cskg",
            status="extracted",
            entities=tuple(
                ArmEntity(slug="cskg", bucket="concepts", name=f"fabricated entity {i}")
                for i in range(20)
            ),
        ),
    )
    mutated = run_evaluation(
        chain_root=chain_root,
        importer_output_dir=importer_output_dir,
        new_arm_papers=wrong,
    )
    new = mutated.arm(NEW_ARM)
    assert new.available

    concept = _typed(new, "ResearchConcept")
    assert concept.recall.value == pytest.approx(0.0)
    assert concept.metrics.entity.fp == 20
    # ...and it differs from the real legacy arm, i.e. the metric reads input.
    legacy_concept = _typed(report.arm(LEGACY_ARM), "ResearchConcept")
    assert concept.metrics.entity.fp != legacy_concept.metrics.entity.fp
    assert new.overall.hallucination_count == 20
    assert new.overall.unsupported_assertion_count == 20


def test_perfect_and_empty_arms_are_distinguishable(
    chain_root: Path, importer_output_dir: Path, papers
) -> None:
    """The two extremes must not collapse onto the same reported value."""
    empty = run_evaluation(
        chain_root=chain_root,
        importer_output_dir=importer_output_dir,
        new_arm_papers=(ArmPaper(slug="cskg", status="extracted"),),
    )
    new = empty.arm(NEW_ARM)
    # Empty: recall is a measured 0.0 (gold exists and none of it was found),
    # precision is insufficient (nothing was produced to be wrong about).
    assert _typed(new, "Method").recall.value == pytest.approx(0.0)
    assert _typed(new, "Method").precision.value is None
    control = empty.arm(GOLD_ARM)
    assert _typed(control, "Method").recall.value == pytest.approx(1.0)


def test_evidence_metrics_separate_two_different_absences(report) -> None:
    """Resolvability is null; span coverage is a measured 0.0. Both are right.

    The legacy arm cites no evidence at all, so there is nothing to resolve and
    resolvability has no denominator. Span coverage does have one: gold demanded
    a quote for each of the 13 entities the arm did find, and supplied none.
    """
    ev = report.arm(LEGACY_ARM).overall.evidence
    assert ev.total_refs == 0
    assert ev.resolvable_rate.value is None
    assert "no evidence references to resolve" in ev.resolvable_rate.note
    assert ev.spans_checked == 13
    assert ev.span_coverage_rate.value == pytest.approx(0.0)


def test_provenance_completeness_is_live(report) -> None:
    """A real 0.0 for legacy: the importer retains quotes only for Problems."""
    legacy = report.arm(LEGACY_ARM).provider_metrics["curation.provenance.completeness"]
    assert legacy.value == pytest.approx(0.0) and legacy.sufficient
    control = report.arm(GOLD_ARM).provider_metrics["curation.provenance.completeness"]
    assert control.value is not None and control.value > 0.8


# --------------------------------------------------------------------------
# Honest nulls, shown becoming numbers when the gap is filled
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "metric",
    [
        "curation.false_merge_rate",
        "curation.false_split_rate",
        "curation.calibration.ece",
        "curation.review.rate",
    ],
)
def test_unmeasurable_metrics_are_null_with_a_reason_not_zero(report, metric: str) -> None:
    """Null on this corpus, each naming an input that would lift it.

    "Supply X and this computes" is the claim test_diagnostics.py then cashes by
    supplying X. A note without such a handle would be a permanent excuse.
    """
    value = report.arm(LEGACY_ARM).provider_metrics[metric]
    assert value.value is None
    assert value.sufficient is False
    assert value.note and len(value.note) > 40
    assert "Supply" in value.note or "below the" in value.note


# The liftable-null estimators (false merge/split, calibration ECE, review rate)
# and the rendering-layer honest-null obligation are exercised in
# test_diagnostics.py, which supplies the inputs each null names and asserts a
# real number comes back.


def test_cost_and_latency_are_not_measured_for_legacy(report) -> None:
    """None, never zeros — no reader may conclude the legacy run was free."""
    assert report.arm(LEGACY_ARM).overall.cost is None


def test_relation_ci_is_insufficient_on_two_items(report) -> None:
    """n=2 is below the bootstrap floor of 8; the point estimate stands alone."""
    ablation = next(
        a
        for a in report.ablations
        if a.metric == "relation.recall"
        and {a.baseline_arm, a.enhanced_arm} == {LEGACY_ARM, GOLD_ARM}
    )
    assert ablation.verdict is AblationVerdict.INSUFFICIENT_EVIDENCE
    assert "too few gold items" in ablation.rationale
    assert ablation.diff_ci is None


# --------------------------------------------------------------------------
# The named-resource gate
# --------------------------------------------------------------------------


def test_pending_decision_blocks_precision_only_where_it_bites(report) -> None:
    """Per-type. ResearchConcept is gated; Method and Model are NOT.

    The arm's only named-resource emission is `SCICERO`, filed under `concepts`.
    Method emits none, so Method precision is 0.286 under all four options — a
    decided number. Nulling it would withhold a settled value *and* publish a
    reason ("yields four different precisions") that is false for that type.
    """
    assert report.disposition is NamedResourceDisposition.PENDING
    legacy = report.arm(LEGACY_ARM)

    concept = _typed(legacy, "ResearchConcept")
    assert concept.named_resource_emissions == 1
    assert concept.metrics.entity.fp > 0
    assert concept.precision.value is None
    assert "named-resource decision" in concept.precision.note
    assert "docs/ground-truth/named-resources.md" in concept.precision.note
    assert concept.precision_is_gated

    # Method HAS false positives, so the old fp==0 escape does not explain this:
    # it is ungated because the decision cannot move it.
    method = _typed(legacy, "Method")
    assert method.named_resource_emissions == 0
    assert method.metrics.entity.fp == 10
    assert method.precision.value == pytest.approx(2 / 7)
    assert not method.precision_is_gated

    model = _typed(legacy, "Model")
    assert model.named_resource_emissions == 0
    assert model.precision.value == pytest.approx(1.0)


def test_ungated_precision_is_identical_under_every_disposition(
    chain_root: Path, importer_output_dir: Path
) -> None:
    """The justification for leaving a type ungated, asserted directly.

    Method precision must be the same number under all four options, or the
    claim the gate relies on is wrong and it should be gating Method after all.
    """
    values = {}
    for disposition in NamedResourceDisposition:
        result = run_evaluation(
            chain_root=chain_root,
            importer_output_dir=importer_output_dir,
            disposition=disposition,
        )
        values[disposition] = _typed(result.arm(LEGACY_ARM), "Method").precision.value
    assert all(v == pytest.approx(2 / 7) for v in values.values()), values
    assert len(values) == 5  # PENDING plus all four options


def test_a_named_resource_emission_of_a_type_does_gate_it(surface_index) -> None:
    """The gate is not simply off for Methods — give it cause and it fires.

    Guards the per-type scoping against over-correction: an arm that DOES emit a
    named resource as a Method must have Method precision gated.
    """
    from agentic_kg.migration.evaluation.arms import build_legacy_arm
    from agentic_kg.migration.evaluation.metrics import (
        count_named_resource_emissions,
        named_resource_emissions_by_bucket,
        named_resource_note,
    )

    arm = build_legacy_arm(
        (
            ArmPaper(
                "cskg",
                "extracted",
                (
                    ArmEntity("cskg", "methods", "CS-KG"),
                    ArmEntity("cskg", "methods", "not labelled anywhere"),
                ),
            ),
        ),
        surface_index,
        graded_slugs=frozenset({"cskg"}),
    )
    by_bucket = named_resource_emissions_by_bucket(arm.outcomes)
    assert by_bucket == {"methods": 1}
    assert count_named_resource_emissions(arm.outcomes, "methods") == 1
    assert count_named_resource_emissions(arm.outcomes, "concepts") == 0
    assert named_resource_note(NamedResourceDisposition.PENDING, 1) is not None
    assert named_resource_note(NamedResourceDisposition.PENDING, 0) is None


def test_the_gate_lifts_when_the_decision_closes(
    chain_root: Path, importer_output_dir: Path
) -> None:
    """Choosing option B must produce a real number — the gate is not a deletion.

    This is the mutation test for the gate itself: if precision stayed null under
    every disposition, the gate would be an excuse rather than a blocker.
    """
    settled = run_evaluation(
        chain_root=chain_root,
        importer_output_dir=importer_output_dir,
        disposition=NamedResourceDisposition.EXCLUDED_AS_FALSE_POSITIVE,
    )
    typed = _typed(settled.arm(LEGACY_ARM), "ResearchConcept")
    assert typed.precision.value is not None
    assert 0.0 < typed.precision.value < 1.0


def test_the_disposition_changes_the_denominator(
    chain_root: Path, importer_output_dir: Path
) -> None:
    """Options B and D must disagree, or the gate is guarding nothing."""
    b = run_evaluation(
        chain_root=chain_root,
        importer_output_dir=importer_output_dir,
        disposition=NamedResourceDisposition.EXCLUDED_AS_FALSE_POSITIVE,
    )
    d = run_evaluation(
        chain_root=chain_root,
        importer_output_dir=importer_output_dir,
        disposition=NamedResourceDisposition.HOLDING_PATTERN,
    )
    b_arm, d_arm = b.arm(LEGACY_ARM), d.arm(LEGACY_ARM)
    assert b_arm.excluded_candidates != d_arm.excluded_candidates
    assert _typed(b_arm, "ResearchConcept").metrics.entity.fp != (
        _typed(d_arm, "ResearchConcept").metrics.entity.fp
    )


@pytest.mark.parametrize(
    "disposition",
    [
        NamedResourceDisposition.SCORED_AS_CONCEPT,
        NamedResourceDisposition.RESOURCE_ENTITY_TYPE,
    ],
)
def test_options_needing_new_gold_stay_null_and_say_so(
    chain_root: Path, importer_output_dir: Path, disposition
) -> None:
    """A and C cannot be graded against today's files, and must not pretend to."""
    result = run_evaluation(
        chain_root=chain_root,
        importer_output_dir=importer_output_dir,
        disposition=disposition,
    )
    typed = _typed(result.arm(LEGACY_ARM), "ResearchConcept")
    assert typed.precision.value is None
    assert "gold" in typed.precision.note.lower()
    # ...and a type the decision cannot touch still reports a number.
    assert _typed(result.arm(LEGACY_ARM), "Method").precision.value is not None


def test_the_gate_never_hides_a_decision_independent_value(report) -> None:
    """An arm with no false positives scores 1.0 under all four options.

    Gating it would make the control arm unreadable for no gain.
    """
    typed = _typed(report.arm(GOLD_ARM), "ResearchConcept")
    assert typed.metrics.entity.fp == 0
    assert typed.precision.value == pytest.approx(1.0)


# --------------------------------------------------------------------------
# Arms
# --------------------------------------------------------------------------


def test_the_new_arm_is_unavailable_not_empty(report) -> None:
    """The distinction is the whole point: 'not built' != 'found nothing'."""
    new = report.arm(NEW_ARM)
    assert not new.available
    assert "no producer yet" in new.unavailable_reason
    assert new.typed == ()
    assert new.overall is None
    # And it is absent from the kg_eval result rather than present with zeros.
    assert {a.arm.arm_id for a in report.kg_eval_result.arms} == {LEGACY_ARM, GOLD_ARM}


def test_unavailable_arm_yields_insufficient_evidence_ablations(report) -> None:
    involving_new = [
        a for a in report.ablations if NEW_ARM in (a.baseline_arm, a.enhanced_arm)
    ]
    assert involving_new
    assert all(a.verdict is AblationVerdict.INSUFFICIENT_EVIDENCE for a in involving_new)
    assert all(a.diff_ci is None for a in involving_new)
    assert all("did not run" in a.rationale for a in involving_new)


def test_all_three_pairings_are_present(report) -> None:
    pairings = {(a.metric, a.baseline_arm, a.enhanced_arm) for a in report.ablations}
    for metric in ("entity.recall", "relation.recall"):
        assert (metric, LEGACY_ARM, NEW_ARM) in pairings
        assert (metric, LEGACY_ARM, GOLD_ARM) in pairings
        assert (metric, NEW_ARM, GOLD_ARM) in pairings


def test_new_arm_becomes_available_the_moment_output_exists(surface_index) -> None:
    """Wiring in the new pipeline is passing data, not editing the arm module."""
    assert isinstance(
        build_new_arm(None, surface_index, graded_slugs=frozenset({"cskg"})), ArmUnavailable
    )
    built = build_new_arm(
        (ArmPaper("cskg", "extracted", (ArmEntity("cskg", "concepts", "reification"),)),),
        surface_index,
        graded_slugs=frozenset({"cskg"}),
    )
    assert not isinstance(built, ArmUnavailable)
    assert len(built.output.candidates) == 1


def test_failed_inputs_are_a_count_not_a_flag(chain_root: Path, importer_output_dir: Path) -> None:
    """``ArmOutput.failed`` counts failed *inputs*, bounded by ``attempted``.

    Stub/metadata-only papers belong there; an arm with no producer does not.
    """
    result = run_evaluation(
        chain_root=chain_root,
        importer_output_dir=importer_output_dir,
        new_arm_papers=(
            ArmPaper("cskg", "extracted", (ArmEntity("cskg", "concepts", "reification"),)),
            ArmPaper("cskg2", "stub"),
        ),
    )
    new = result.arm(NEW_ARM)
    assert new.available
    assert new.coverage.graded_attempted == 2
    assert new.coverage.graded_failed == 1
    assert new.overall.failure_rate.value == pytest.approx(0.5)


# --------------------------------------------------------------------------
# Determinism and rendering
# --------------------------------------------------------------------------


def test_two_runs_are_byte_identical(chain_root: Path, importer_output_dir: Path) -> None:
    """No model call, no network, no clock: the only RNG is the seeded bootstrap."""
    a = run_evaluation(chain_root=chain_root, importer_output_dir=importer_output_dir)
    b = run_evaluation(chain_root=chain_root, importer_output_dir=importer_output_dir)
    assert render_report(a) == render_report(b)
    assert render_kg_eval_json(a) == render_kg_eval_json(b)
    assert a.gold.content_hash == b.gold.content_hash


def test_a_different_seed_changes_only_intervals(
    chain_root: Path, importer_output_dir: Path, report
) -> None:
    """Point estimates must not depend on the bootstrap seed."""
    other = run_evaluation(
        chain_root=chain_root,
        importer_output_dir=importer_output_dir,
        boot=BootstrapConfig(seed=999, n_resamples=200, min_items=8),
    )
    assert _typed(other.arm(LEGACY_ARM), "Model").recall.value == pytest.approx(
        _typed(report.arm(LEGACY_ARM), "Model").recall.value
    )


def test_rendered_report_never_prints_a_fake_zero(report) -> None:
    """Substring checks on the report as a whole.

    The stronger assertion — that no rendering path coalesces ``None`` into
    ``0.0`` at all — is in test_diagnostics.py, because a substring check cannot
    catch a fabricated zero that happens to look like a real one.
    """
    text = render_report(report)
    assert "insufficient (" in text
    assert "53" in text and "8 attempted, 4 failed" in text
    assert "Not run." in text
    # An unmeasured cost must never render as 0.
    assert "not measured" in text


def test_strict_corpus_check_fires_on_a_changed_corpus(tmp_path: Path, chain_root: Path) -> None:
    """A newly reconciled paper must force a re-measure, not slide in silently."""
    import shutil

    staged = tmp_path / "chain"
    shutil.copytree(chain_root, staged)
    (staged / "reconciled" / "paper_cskg2.gold.yml").unlink()
    with pytest.raises(AssertionError, match="reconciled gold set changed"):
        run_evaluation(
            chain_root=staged,
            importer_output_dir=chain_root.parents[4] / "docs/ground-truth/importer-output",
        )
