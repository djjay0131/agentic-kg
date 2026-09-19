"""The deterministic evaluation runner: legacy vs new vs gold.

Assembles the corpus, the gold set, the arms and the metric providers into one
reproducible :class:`EvaluationReport`, and renders it. Nothing here calls a
model, opens a socket, or reads a clock: the legacy arm is a committed recording
and the only randomness in ``kg_eval`` is a caller-seeded bootstrap. Two runs of
this module over the same commit produce byte-identical output.

What the report is careful to keep separate:

* **Per-type entity metrics.** ``kg_eval`` reports one aggregate entity PRF per
  gold set. Topic, ResearchConcept, Model and Method have very different sample
  sizes (4, 14, 12, 23) and the legacy importer fails them in different ways, so
  an aggregate would hide the one result the corpus actually supports. The
  runner grades one gold-set slice per type.
* **Graded scope vs corpus scope.** The metrics describe two papers; the corpus
  is eight. Both numbers appear, labelled, and neither is used as the other's
  denominator.
* **Measured zero vs honest null.** Every metric that cannot be computed carries
  its reason. ``kg_eval`` enforces this for its own metrics; the named-resource
  gate and :class:`CurationMetricProvider` extend it to ours.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from kg_eval.ablation import AblationResult, AblationVerdict, compare_arms
from kg_eval.bootstrap import BootstrapConfig
from kg_eval.goldset import GoldSet
from kg_eval.matching import match_entities
from kg_eval.metrics import ExtractionMetrics, MetricValue, evaluate_extraction
from kg_eval.providers import MetricProviderRegistry
from kg_eval.report import EvaluationResult, evaluate_arms, render_json, render_markdown

from agentic_kg.migration.evaluation.adapter import (
    CrossTypeMatch,
    build_goldset,
    build_surface_index,
    cross_type_matches,
)
from agentic_kg.migration.evaluation.arms import (
    GOLD_ARM,
    LEGACY_ARM,
    NEW_ARM,
    ArmResult,
    ArmUnavailable,
    BuiltArm,
    build_gold_arm,
    build_legacy_arm,
    build_new_arm,
    restrict_to_entity_type,
)
from agentic_kg.migration.evaluation.corpus import (
    BUCKET_TO_ENTITY_TYPE,
    ArmPaper,
    CorpusCoverage,
    ReconciledPaper,
    check_reconciled_slugs,
    load_importer_output,
    load_reconciled_papers,
)
from agentic_kg.migration.evaluation.metrics import (
    CurationMetricProvider,
    NamedResourceDisposition,
    count_named_resource_emissions,
    named_resource_emissions_by_bucket,
    named_resource_note,
)

#: Fixed seed. ``BootstrapConfig`` has no default seed by design — there is no
#: "fall back to the system RNG" path, because that is how a CI silently becomes
#: irreproducible. Pinning it here makes every interval in the report a function
#: of the committed fixtures alone.
DEFAULT_BOOTSTRAP = BootstrapConfig(seed=20260901, n_resamples=1000, level=0.95, min_items=8)

#: The ablations the report answers, in order.
ABLATION_METRICS = ("entity.recall", "relation.recall")


@dataclass(frozen=True)
class TypedMetrics:
    """One entity type's scorecard, with the named-resource gate applied."""

    entity_type: str
    bucket: str
    gold_count: int
    metrics: ExtractionMetrics
    precision: MetricValue
    recall: MetricValue
    named_resource_emissions: int = 0
    cross_type_hits: tuple[CrossTypeMatch, ...] = ()

    @property
    def precision_is_gated(self) -> bool:
        return self.metrics.entity.precision.value is not None and self.precision.value is None

    @property
    def type_insensitive_recall(self) -> MetricValue:
        """Recall counting a gold item found under the wrong type as found.

        Published *beside* the strict recall, never instead of it. Strict recall
        is the score — a mis-typed entity is a real error. But the gap between
        the two is the diagnosis, and without it a reader cannot tell blindness
        from mis-filing. ResearchConcept recall of 0.071 invites "the extractor
        cannot see research concepts"; the 0.214 beside it says it saw three of
        them and filed two as Methods. Different defect, much cheaper fix.
        """
        if self.gold_count == 0:
            return MetricValue.insufficient("no gold items of this type")
        found = self.metrics.entity.tp + len(self.cross_type_hits)
        return MetricValue.measured(found / self.gold_count)

    @property
    def type_confusion_count(self) -> int:
        """Gold items of this type that the arm reached under another type."""
        return len(self.cross_type_hits)


@dataclass(frozen=True)
class ArmReport:
    """Everything the report says about one arm."""

    arm_id: str
    unavailable_reason: str | None = None
    coverage: CorpusCoverage | None = None
    typed: tuple[TypedMetrics, ...] = ()
    overall: ExtractionMetrics | None = None
    provider_metrics: Mapping[str, MetricValue] = field(default_factory=dict)
    excluded_candidates: int = 0
    named_resource_emissions: int = 0

    @property
    def available(self) -> bool:
        return self.unavailable_reason is None


@dataclass(frozen=True)
class EvaluationReport:
    """The full legacy-vs-new-vs-gold result, plus the corpus facts behind it."""

    gold: GoldSet
    disposition: NamedResourceDisposition
    papers: tuple[ReconciledPaper, ...]
    arms: tuple[ArmReport, ...]
    ablations: tuple[AblationResult, ...]
    kg_eval_result: EvaluationResult
    scoreable_entities: int
    scoreable_relations: int
    relations_gold_both_ends: int

    def arm(self, arm_id: str) -> ArmReport:
        for a in self.arms:
            if a.arm_id == arm_id:
                return a
        raise KeyError(arm_id)


# --------------------------------------------------------------------------


def _gate_precision(
    metrics: ExtractionMetrics,
    disposition: NamedResourceDisposition,
    affected: int,
) -> MetricValue:
    """Apply the named-resource gate to an entity precision value.

    The gate only ever *removes* a number; it never invents one. Two cases pass
    through untouched:

    * ``kg_eval`` already found precision unmeasurable (no candidates of this
      type). That verdict and its reason stand — swapping one honest null for a
      different one would hide why the metric is missing.
    * The arm produced **no false positives at all**. Every open option changes
      precision only by moving emissions into or out of the denominator's wrong
      column; an arm with nothing in that column scores 1.0 under all four, so
      the value is decision-independent and reporting it is safe. This is what
      keeps the ``gold`` control arm's self-check readable: a control that came
      back "insufficient" on precision could not tell anyone the adapter's keys
      line up.

    ``affected`` is counted **per entity type**, and that is what makes the gate
    truthful rather than merely cautious. A corpus-wide count gates every type on
    a decision that may not touch most of them: on this corpus the arm's only
    named-resource emission is ``SCICERO``, under ``concepts``. Method precision
    is 0.286 under all four options, and nulling it published a reason — "the
    same arm output yields four different precisions" — that was false for that
    type. A null carrying a reason that does not apply is its own kind of
    fabrication; :func:`named_resource_note` returns ``None`` when ``affected``
    is zero.
    """
    if metrics.entity.precision.value is None:
        return metrics.entity.precision
    if metrics.entity.fp == 0:
        return metrics.entity.precision
    note = named_resource_note(disposition, affected)
    if note is None:
        return metrics.entity.precision
    return MetricValue.insufficient(note)


def _typed_metrics(
    arm: BuiltArm,
    papers: Sequence[ReconciledPaper],
    disposition: NamedResourceDisposition,
    affected_by_bucket: Mapping[str, int],
    cross_type: Sequence[CrossTypeMatch],
    boot: BootstrapConfig | None,
) -> tuple[TypedMetrics, ...]:
    out: list[TypedMetrics] = []
    for bucket, entity_type in BUCKET_TO_ENTITY_TYPE.items():
        slice_gold = build_goldset(
            papers,
            gold_set_id=f"ground-truth-chain:{bucket}",
            buckets=(bucket,),
            include_relations=False,
        )
        # Both sides must be narrowed together: a single-type gold slice graded
        # against every candidate turns the other three types into this type's
        # false positives.
        metrics = evaluate_extraction(
            restrict_to_entity_type(arm.output, entity_type), slice_gold, boot=boot
        )
        affected = affected_by_bucket.get(bucket, 0)
        # Deduplicated by gold key: two mis-typed emissions naming the same gold
        # entity are one entity found, not two.
        hits: dict[tuple[str, str], CrossTypeMatch] = {}
        for match in cross_type:
            if match.gold_bucket == bucket:
                hits.setdefault(match.gold_key, match)
        # A gold item already matched strictly is not "confused about" anything;
        # counting it again would push type-insensitive recall above the truth.
        matched_strictly = _strictly_matched(arm, slice_gold, entity_type)
        confused = tuple(m for k, m in sorted(hits.items()) if k not in matched_strictly)
        out.append(
            TypedMetrics(
                entity_type=entity_type,
                bucket=bucket,
                gold_count=len(slice_gold.entities),
                metrics=metrics,
                precision=_gate_precision(metrics, disposition, affected),
                recall=metrics.entity.recall,
                named_resource_emissions=affected,
                cross_type_hits=confused,
            )
        )
    return tuple(out)


def _strictly_matched(
    arm: BuiltArm, slice_gold: GoldSet, entity_type: str
) -> set[tuple[str, str]]:
    """Gold keys of this type the arm already matched under the correct type."""
    narrowed = restrict_to_entity_type(arm.output, entity_type)
    match = match_entities(narrowed.candidates, slice_gold)
    matched: set[tuple[str, str]] = set()
    for index, hit in enumerate(match.gold_hits):
        if hit:
            key = slice_gold.entities[index].semantic_key
            slug, _, canonical = key.partition("/")
            matched.add((slug, canonical))
    return matched


def _arm_report(
    arm: ArmResult,
    papers: Sequence[ReconciledPaper],
    gold: GoldSet,
    disposition: NamedResourceDisposition,
    boot: BootstrapConfig | None,
) -> ArmReport:
    if isinstance(arm, ArmUnavailable):
        return ArmReport(arm_id=arm.arm_id, unavailable_reason=arm.reason)

    affected = count_named_resource_emissions(arm.outcomes)
    by_bucket = named_resource_emissions_by_bucket(arm.outcomes)
    registry = MetricProviderRegistry()
    registry.register(
        CurationMetricProvider(
            gold_item_count=len(gold.entities),
            # All three are None on this corpus: no arm emits per-candidate
            # confidence, no mention-level cluster labels exist in the reconciled
            # gold, and no arm has a review queue. Each is a real estimator input,
            # so each null is liftable by supplying data rather than by editing a
            # note.
            calibration=None,
            clustering=None,
            review_count=None,
        )
    )
    # `index` is optional on BuiltArm only so the dataclass stays constructible in
    # isolation; every builder supplies it, and a diagnostic silently reporting
    # "no confusion" because it was absent would be worse than not running.
    assert arm.index is not None, f"arm {arm.arm_id!r} was built without a SurfaceIndex"
    cross_type = cross_type_matches(arm.arm_papers, arm.index)
    return ArmReport(
        arm_id=arm.arm_id,
        coverage=arm.coverage,
        typed=_typed_metrics(arm, papers, disposition, by_bucket, cross_type, boot),
        overall=evaluate_extraction(arm.output, gold, boot=boot),
        provider_metrics=registry.evaluate_all(arm.output, gold),
        excluded_candidates=arm.excluded_count,
        named_resource_emissions=affected,
    )


def _ablations(
    arms: Mapping[str, ArmResult],
    gold: GoldSet,
    boot: BootstrapConfig,
) -> tuple[AblationResult, ...]:
    """Every pairing the brief asks for, with unavailable arms reported honestly.

    ``compare_arms`` needs two ``ArmOutput``s. When one side is unavailable there
    is nothing to hand it, so the INSUFFICIENT_EVIDENCE verdict is constructed
    directly — the same verdict ``compare_arms`` would reach, reached without
    fabricating an empty arm to feed it.
    """
    pairs = ((LEGACY_ARM, NEW_ARM), (LEGACY_ARM, GOLD_ARM), (NEW_ARM, GOLD_ARM))
    results: list[AblationResult] = []
    for metric in ABLATION_METRICS:
        for baseline_id, enhanced_id in pairs:
            baseline, enhanced = arms.get(baseline_id), arms.get(enhanced_id)
            missing = [
                arm_id
                for arm_id, arm in ((baseline_id, baseline), (enhanced_id, enhanced))
                if not isinstance(arm, BuiltArm)
            ]
            if missing:
                results.append(
                    AblationResult(
                        metric=metric,
                        baseline_arm=baseline_id,
                        enhanced_arm=enhanced_id,
                        baseline=MetricValue.insufficient("arm not run"),
                        enhanced=MetricValue.insufficient("arm not run"),
                        diff_ci=None,
                        verdict=AblationVerdict.INSUFFICIENT_EVIDENCE,
                        rationale=(
                            f"{', '.join(missing)} produced no output to grade; a comparison "
                            "against an arm that did not run is not evidence in either "
                            "direction"
                        ),
                    )
                )
                continue
            assert isinstance(baseline, BuiltArm) and isinstance(enhanced, BuiltArm)
            results.append(
                compare_arms(
                    baseline.output, enhanced.output, gold, metric=metric, boot=boot
                )
            )
    return tuple(results)


def run_evaluation(
    *,
    chain_root: Path,
    importer_output_dir: Path,
    new_arm_papers: Sequence[ArmPaper] | None = None,
    disposition: NamedResourceDisposition = NamedResourceDisposition.PENDING,
    boot: BootstrapConfig = DEFAULT_BOOTSTRAP,
    strict_corpus: bool = True,
) -> EvaluationReport:
    """Run the full comparison over the committed fixtures.

    ``strict_corpus`` asserts the reconciled set is still exactly the two papers
    every count in this report was measured against; turn it off only when
    deliberately evaluating a changed corpus.
    """
    papers = load_reconciled_papers(chain_root)
    if strict_corpus:
        check_reconciled_slugs(papers)
    graded_slugs = frozenset(p.slug for p in papers)

    index = build_surface_index(papers)
    gold = build_goldset(
        papers,
        gold_set_id="ground-truth-chain:reconciled",
        description=(
            "Reconciled answer keys for the papers that have them. "
            "human/ and claude/ are independent reviews and are never scored against."
        ),
    )

    legacy = build_legacy_arm(
        load_importer_output(importer_output_dir),
        index,
        graded_slugs=graded_slugs,
        disposition=disposition,
    )
    new = build_new_arm(
        new_arm_papers, index, graded_slugs=graded_slugs, disposition=disposition
    )
    control = build_gold_arm(papers, index)

    arms: dict[str, ArmResult] = {LEGACY_ARM: legacy, NEW_ARM: new, GOLD_ARM: control}
    ablations = _ablations(arms, gold, boot)

    runnable = tuple(a.output for a in arms.values() if isinstance(a, BuiltArm))
    kg_eval_result = evaluate_arms(runnable, gold, boot=boot, ablations=ablations)

    return EvaluationReport(
        gold=gold,
        disposition=disposition,
        papers=papers,
        arms=tuple(
            _arm_report(arms[a], papers, gold, disposition, boot)
            for a in (LEGACY_ARM, NEW_ARM, GOLD_ARM)
        ),
        ablations=ablations,
        kg_eval_result=kg_eval_result,
        scoreable_entities=len(gold.entities),
        scoreable_relations=len(gold.relations),
        relations_gold_both_ends=sum(
            1 for p in papers for e in p.citations if e.cited_has_reconciled_gold
        ),
    )


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _fmt(value: MetricValue) -> str:
    if value.value is None:
        return f"insufficient ({value.note})"
    text = f"{value.value:.3f}"
    if value.ci is not None:
        text += f" [{value.ci.lower:.3f}, {value.ci.upper:.3f}]"
    return text


def _fmt_ratio(value: float | None, note: str) -> str:
    """Render a bare ``float | None`` ratio without inventing a zero.

    The honest-null rule is a property of the *rendered report*, not only of the
    model behind it. ``f"{(x or 0.0):.3f}"`` reads as a formatting convenience
    and prints ``0.000`` for an unmeasured value — indistinguishable from a real
    zero, which is exactly the confusion ``MetricValue`` exists to prevent. Every
    optional number in this renderer goes through here or through :func:`_fmt`;
    no `or 0` coalescing is permitted in a rendering path.
    """
    return f"insufficient ({note})" if value is None else f"{value:.3f}"


def render_report(report: EvaluationReport) -> str:
    """A Markdown summary of what this corpus can and cannot say today.

    Deliberately leads with the scope, not the scores. A reader who sees
    "entity recall 0.34" without first seeing "over 53 gold items on 2 of 8
    papers, with precision gated on an open decision" will over-read it, and the
    corpus is small enough that over-reading it is the likeliest failure mode.
    """
    lines: list[str] = [
        "# Ground-truth evaluation — legacy vs new vs gold",
        "",
        f"Gold set: `{report.gold.gold_set_id}` (hash `{report.gold.content_hash[:16]}…`)",
        "",
        "## Scoreable surface",
        "",
        f"- Reconciled papers: **{len(report.papers)}** "
        f"({', '.join(p.slug for p in report.papers)})",
        f"- Scored gold entities: **{report.scoreable_entities}**",
        f"- Gold citation edges: **{report.scoreable_relations}** "
        f"({report.relations_gold_both_ends} with reconciled gold on both ends)",
        f"- Named-resource disposition: **{report.disposition.value}**",
        "",
        "## Arms",
        "",
    ]
    for arm in report.arms:
        lines += [f"### {arm.arm_id}", ""]
        if not arm.available:
            lines += [f"**Not run.** {arm.unavailable_reason}", ""]
            continue
        if arm.coverage is not None:
            cov = arm.coverage
            lines += [
                f"- Graded scope: {cov.graded_attempted} paper(s), "
                f"{cov.graded_failed} failed",
                f"- Corpus scope (not a metric denominator): {cov.corpus_attempted} "
                f"attempted, {cov.corpus_failed} failed "
                f"({_fmt_ratio(cov.corpus_failure_rate, 'no attempted inputs')})",
                f"- Candidates excluded as acceptable_extras: {arm.excluded_candidates} "
                f"(of which named-resource: {arm.named_resource_emissions})",
                "",
            ]
        lines += [
            "| Entity type | Gold | Precision | Recall (strict) | Recall (type-insensitive) "
            "| Mis-typed | TP | FP | FN |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for typed in arm.typed:
            prf = typed.metrics.entity
            lines.append(
                f"| {typed.entity_type} | {typed.gold_count} | {_fmt(typed.precision)} "
                f"| {_fmt(typed.recall)} | {_fmt(typed.type_insensitive_recall)} "
                f"| {typed.type_confusion_count} | {prf.tp} | {prf.fp} | {prf.fn} |"
            )
        lines.append("")
        confused = [t for t in arm.typed if t.cross_type_hits]
        if confused:
            lines += [
                "**Type confusion** — gold entities this arm located in the text and filed "
                "under the wrong type. These score as misses (strict recall is the score); "
                "they are listed because a low recall caused by mis-typing calls for "
                "different work than one caused by not finding the entity at all.",
                "",
            ]
            for typed in confused:
                for match in typed.cross_type_hits:
                    lines.append(
                        f"- `{match.slug}` {typed.entity_type} "
                        f"**{match.gold_canonical}** was emitted as "
                        f"{BUCKET_TO_ENTITY_TYPE[match.emitted_bucket]} "
                        f'("{match.emitted_surface}")'
                    )
            lines.append("")
        if arm.overall is not None:
            rel = arm.overall.relation
            lines += [
                f"- Citation (CITES) precision: {_fmt(rel.precision)}",
                f"- Citation (CITES) recall: {_fmt(rel.recall)}",
                f"- Evidence resolvable: {_fmt(arm.overall.evidence.resolvable_rate)}",
                f"- Evidence span coverage: {_fmt(arm.overall.evidence.span_coverage_rate)}"
                f" ({arm.overall.evidence.spans_covered}/"
                f"{arm.overall.evidence.spans_checked} spans)",
                f"- Abstention rate: {_fmt(arm.overall.abstention_rate)}",
                f"- Failure rate (graded scope): {_fmt(arm.overall.failure_rate)}",
                "- Cost/latency/tokens: "
                + (
                    "not measured"
                    if arm.overall.cost is None
                    else f"{arm.overall.cost.latency_seconds}s / "
                    f"{arm.overall.cost.cost_usd} usd / {arm.overall.cost.tokens} tokens"
                ),
                "",
            ]
        for name, value in sorted(arm.provider_metrics.items()):
            lines.append(f"- {name}: {_fmt(value)}")
        lines.append("")

    lines += ["## Ablations", ""]
    for ab in report.ablations:
        lines.append(
            f"- `{ab.metric}` {ab.enhanced_arm} vs {ab.baseline_arm}: "
            f"**{ab.verdict.value}** — {ab.rationale}"
        )
    lines.append("")
    return "\n".join(lines)


def render_kg_eval_json(report: EvaluationReport) -> str:
    """The archival ``kg_eval`` JSON, byte-stable for the same fixtures."""
    return render_json(report.kg_eval_result)


def render_kg_eval_markdown(report: EvaluationReport) -> str:
    """``kg_eval``'s own Markdown rendering, for the aggregate view."""
    return render_markdown(report.kg_eval_result)


__all__ = [
    "ABLATION_METRICS",
    "DEFAULT_BOOTSTRAP",
    "ArmReport",
    "EvaluationReport",
    "TypedMetrics",
    "render_kg_eval_json",
    "render_kg_eval_markdown",
    "render_report",
    "run_evaluation",
]
