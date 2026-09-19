"""The metrics this corpus cannot get from ``kg_eval`` alone — every one honest.

``kg_eval`` owns extraction P/R/F1, evidence validity, hallucination and
unsupported-assertion counts, abstention/failure rates and cost/latency. Two
things it cannot own are added here.

**The named-resource gate.** ``docs/ground-truth/named-resources.md`` is an open
decision (owner: Victoria; status: open since 2026-08-03), and
``reconciled/paper_cskg.gold.yml`` says in the file: *"DECISION PENDING — do not
diff against this paper until it is settled."* Until it closes, entity precision
has no defined denominator, because each of the four options on the table
produces a different one from the same arm output. Reporting a number anyway
would be reporting our labelling indecision as the importer's behaviour. So the
gate turns entity precision into ``MetricValue.insufficient`` with a note that
names the decision — and it is a *gate*, not a deletion: choose a disposition and
the number appears.

**The curation metrics ADR-0009 asks for that this gold set cannot support.**
False merge rate, false split rate and calibration are real, wanted metrics. They
are also unmeasurable against these fixtures, and the failure mode to avoid is
not "we forgot them" but "we printed 0.0 for them". :class:`CurationMetricProvider`
emits each one explicitly as an insufficient ``MetricValue`` carrying the reason,
so the report shows the whole ADR-0009 metric list with the gaps visible rather
than a shorter list that looks complete.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from kg_eval.arms import ArmOutput
from kg_eval.goldset import GoldSet
from kg_eval.metrics import MetricValue

from agentic_kg.migration.evaluation.adapter import FilterOutcome
from agentic_kg.migration.evaluation.corpus import NAMED_RESOURCE_BUCKET

#: Bin-population floor below which a calibration curve is noise. Ten bins over
#: 53 gold items would average five items a bin even if every item carried a
#: confidence, which none do.
CALIBRATION_MIN_ITEMS = 100


class NamedResourceDisposition(StrEnum):
    """The four options in ``docs/ground-truth/named-resources.md``, plus PENDING.

    PENDING is the default and the current truth: the decision is open. The
    other four exist so that when it closes, the runner changes by one argument
    rather than by an edit to the grading logic — and so that the cost of each
    option can be shown before it is chosen.
    """

    #: No decision yet. Entity precision is not defined; the gate returns null.
    PENDING = "PENDING"
    #: Option A — score named resources as ResearchConcepts.
    SCORED_AS_CONCEPT = "SCORED_AS_CONCEPT"
    #: Option B — exclude from gold, emissions are false positives.
    EXCLUDED_AS_FALSE_POSITIVE = "EXCLUDED_AS_FALSE_POSITIVE"
    #: Option C — a first-class ``Resource`` entity type.
    RESOURCE_ENTITY_TYPE = "RESOURCE_ENTITY_TYPE"
    #: Option D — keep the ``acceptable_extras`` holding pattern indefinitely.
    HOLDING_PATTERN = "HOLDING_PATTERN"


#: Dispositions under which named-resource emissions stay in the precision
#: denominator (as false positives). Only option B.
_DENOMINATOR_INCLUDES_NAMED_RESOURCES = frozenset(
    {NamedResourceDisposition.EXCLUDED_AS_FALSE_POSITIVE}
)


def named_resource_note(disposition: NamedResourceDisposition, affected: int) -> str | None:
    """Why entity precision is unmeasurable under ``disposition``, or ``None``.

    ``None`` means precision is well-defined and should be reported as measured.

    ``affected`` is the number of *this entity type's* emissions that the open
    decision covers, and passing zero returns ``None`` for every disposition.
    That scoping is the whole correctness argument. The four options all act on
    named-resource emissions and on gold entries for them; a type with neither is
    untouched by the decision and its precision is already settled. Nulling it
    anyway would withhold a number that is not in doubt, and — worse — publish a
    reason ("the same arm output yields four different precisions") that is
    simply false for that type. An honest null has to be honest about *why*, and
    a reason that does not apply is not a smaller error than a fabricated zero.
    """
    if affected == 0:
        return None
    if disposition is NamedResourceDisposition.PENDING:
        return (
            "precision for this entity type has no defined denominator while the "
            "named-resource decision is open (docs/ground-truth/named-resources.md, "
            f"owner Victoria, raised 2026-08-03): {affected} of this arm's emissions of "
            "this type fall under it. The four options do not merely re-bucket the same "
            "candidates: A and C both ADD gold entries (as ResearchConcepts, or under a "
            "new expected_resources key), which moves recall's denominator as well as "
            "precision's, while B keeps the emissions in as false positives and D drops "
            "them. The same arm output therefore yields four different precisions for "
            "this type, so any number reported now measures the labelling choice rather "
            "than the importer. Note the inventory is gold-side: it records the named "
            "resources the reviewers found, and does not bound how many the arm emits. "
            "reconciled/paper_cskg.gold.yml: 'DECISION PENDING — do not diff against this "
            "paper until it is settled.'"
        )
    if disposition is NamedResourceDisposition.SCORED_AS_CONCEPT:
        return (
            "option A scores named resources as ResearchConcepts, which makes them recall "
            "obligations — but no reconciled file lists any under expected_concepts. The gold "
            "would have to be relabelled first; grading against today's files would count "
            f"every one of this type's {affected} affected emissions as a false positive, "
            "which is option B, not option A."
        )
    if disposition is NamedResourceDisposition.RESOURCE_ENTITY_TYPE:
        return (
            "option C needs a Resource entity type and an expected_resources gold key. "
            "SCHEMA.md defines neither, so there is nothing to grade this type's "
            f"{affected} affected emissions against."
        )
    return None


def disposition_excludes_named_resources(disposition: NamedResourceDisposition) -> bool:
    """Should named-resource emissions be dropped from the precision denominator?

    True for every disposition except B. Under PENDING and D they are dropped
    because they carry no obligation either way; under A and C they would be
    dropped because the gold that ought to receive them does not exist yet, and
    the accompanying note says so. Only B deliberately keeps them in, as the
    false positives that option defines them to be.
    """
    return disposition not in _DENOMINATOR_INCLUDES_NAMED_RESOURCES


def _is_named_resource(outcome: FilterOutcome) -> bool:
    return (
        outcome.excused_by is not None
        and outcome.excused_by.source_bucket == NAMED_RESOURCE_BUCKET
    )


def count_named_resource_emissions(
    outcomes: Sequence[FilterOutcome], bucket: str | None = None
) -> int:
    """How many of an arm's emissions the open decision covers.

    ``bucket`` narrows the count to one category, which is what the per-type gate
    needs: on this corpus the arm's only named-resource emission is ``SCICERO``,
    filed under ``concepts``. Counting corpus-wide would gate Topic, Model and
    Method precision on a decision that cannot move any of them.
    """
    return sum(
        1
        for o in outcomes
        if _is_named_resource(o) and (bucket is None or o.entity.bucket == bucket)
    )


def named_resource_emissions_by_bucket(
    outcomes: Sequence[FilterOutcome],
) -> dict[str, int]:
    """Per-bucket counts of emissions the open decision covers."""
    counts: dict[str, int] = {}
    for outcome in outcomes:
        if _is_named_resource(outcome):
            counts[outcome.entity.bucket] = counts.get(outcome.entity.bucket, 0) + 1
    return counts


@dataclass(frozen=True)
class MentionClustering:
    """Mention-level cluster labels, the input false merge/split actually need.

    ``gold[i]`` and ``predicted[i]`` are the cluster ids assigned to mention
    ``i`` by the answer key and by the arm. Both rates are *pairwise*: over every
    pair of mentions, a false merge is a pair the arm put together that gold
    keeps apart, and a false split is a pair gold puts together that the arm
    keeps apart.

    This type exists so the two metrics are genuinely liftable. Reporting them
    as permanently-insufficient with a note would be a refusal dressed as
    honesty; the null must name a missing input that something can actually
    supply. The reconciled fixtures do not supply it — they record entity-level
    canonicals and aliases, never which mention belongs to which entity — so on
    today's corpus the metrics stay null. Hand this object over and they compute.
    """

    gold: tuple[str, ...]
    predicted: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.gold) != len(self.predicted):
            raise ValueError(
                f"clustering is misaligned: {len(self.gold)} gold labels vs "
                f"{len(self.predicted)} predicted; index i must be the same mention "
                "on both sides"
            )

    @property
    def n(self) -> int:
        return len(self.gold)

    def pair_counts(self) -> tuple[int, int, int, int]:
        """``(both_together, pred_only, gold_only, neither)`` over all pairs."""
        both = pred_only = gold_only = neither = 0
        for i in range(self.n):
            for j in range(i + 1, self.n):
                same_gold = self.gold[i] == self.gold[j]
                same_pred = self.predicted[i] == self.predicted[j]
                if same_gold and same_pred:
                    both += 1
                elif same_pred:
                    pred_only += 1
                elif same_gold:
                    gold_only += 1
                else:
                    neither += 1
        return both, pred_only, gold_only, neither


@dataclass(frozen=True)
class CalibrationSample:
    """One ``(confidence, was_correct)`` observation for a reliability curve."""

    confidence: float
    correct: bool


def expected_calibration_error(
    samples: Sequence[CalibrationSample], *, bins: int = 10
) -> float:
    """Binned ECE: the confidence-weighted gap between stated and actual accuracy.

    Equal-width bins over [0, 1]; empty bins contribute nothing. Deterministic —
    no sampling, no RNG — so it composes with the rest of this runner's
    reproducibility guarantee.
    """
    buckets: list[list[CalibrationSample]] = [[] for _ in range(bins)]
    for sample in samples:
        index = min(int(sample.confidence * bins), bins - 1)
        buckets[index].append(sample)
    total = len(samples)
    error = 0.0
    for bucket in buckets:
        if not bucket:
            continue
        accuracy = sum(1 for s in bucket if s.correct) / len(bucket)
        confidence = sum(s.confidence for s in bucket) / len(bucket)
        error += (len(bucket) / total) * abs(accuracy - confidence)
    return error


class CurationMetricProvider:
    """ADR-0009 curation metrics: computed when the data exists, null when it does not.

    This is a ``kg_eval`` ``MetricProvider`` (structural: a ``name`` plus
    ``evaluate``), registered through ``MetricProviderRegistry`` exactly as the
    KGCS providers will be.

    Every null here names an input that something can actually supply, and the
    estimator behind it is real — pass the input and a number comes out. That
    property is the difference between an honest null and a permanent excuse: a
    metric whose note can only ever change wording is not "not yet measured", it
    is not implemented, and saying so in the shape of a null misrepresents it.

    ``provenance.completeness``
        The fraction of graded candidates carrying at least one resolvable
        supporting ``Evidence``. Always measurable; for the recorded legacy arm
        it is a real 0.0, because the importer keeps ``quoted_text`` only for
        Problems. A finding about the pipeline, not an artefact of this runner.

    ``false_merge_rate`` / ``false_split_rate``
        Pairwise, over a :class:`MentionClustering`. Null on this corpus because
        the reconciled fixtures carry no mention-level cluster labels; labelling
        them is ground-truth work, not a code change.

    ``calibration.ece``
        :func:`expected_calibration_error` over per-candidate confidences. Null
        on this corpus for two independent reasons, either alone disqualifying:
        the arm emits no per-candidate confidence, and 53 gold items is below any
        sane bin-population floor.

    ``review.rate``
        Reviewed candidates over candidates produced — deliberately *not*
        abstention, which ``kg_eval`` already reports separately. An earlier
        draft of this provider would have returned ``abstained / attempted`` had
        a queue been enabled, i.e. silently published the abstention rate under a
        second name. It now takes an explicit ``review_count``.
    """

    name = "curation"

    def __init__(
        self,
        *,
        gold_item_count: int,
        calibration: Sequence[CalibrationSample] | None = None,
        clustering: MentionClustering | None = None,
        review_count: int | None = None,
        calibration_bins: int = 10,
    ) -> None:
        self._gold_item_count = gold_item_count
        self._calibration = tuple(calibration) if calibration is not None else None
        self._clustering = clustering
        self._review_count = review_count
        self._calibration_bins = calibration_bins

    def evaluate(self, output: ArmOutput, gold: GoldSet) -> Mapping[str, MetricValue]:
        return {
            "provenance.completeness": self._provenance(output),
            "false_merge_rate": self._false_merge(),
            "false_split_rate": self._false_split(),
            "calibration.ece": self._calibration_error(),
            "review.rate": self._review(output),
        }

    # -- provenance --------------------------------------------------------

    def _provenance(self, output: ArmOutput) -> MetricValue:
        total = len(output.candidates)
        if total == 0:
            return MetricValue.insufficient("no candidates produced")
        store = output.evidence
        supported = sum(
            1
            for c in output.candidates
            if any(ref.evidence_id in store for ref in c.evidence_refs)
        )
        return MetricValue.measured(supported / total)

    # -- clustering --------------------------------------------------------

    _NO_CLUSTERS = (
        "{label} is defined over mention clusters; the reconciled gold records "
        "entity-level canonicals and aliases only and carries no mention-level cluster "
        "labels, so there is nothing to count merges or splits against. Supply a "
        "MentionClustering and this computes."
    )

    def _false_merge(self) -> MetricValue:
        clustering = self._clustering
        if clustering is None:
            return MetricValue.insufficient(self._NO_CLUSTERS.format(label="false merge rate"))
        _, pred_only, _, _ = clustering.pair_counts()
        predicted_pairs = self._predicted_pairs(clustering)
        if predicted_pairs == 0:
            return MetricValue.insufficient(
                "false merge rate: the arm merged no pair of mentions, so there is no "
                "denominator"
            )
        return MetricValue.measured(pred_only / predicted_pairs)

    def _false_split(self) -> MetricValue:
        clustering = self._clustering
        if clustering is None:
            return MetricValue.insufficient(self._NO_CLUSTERS.format(label="false split rate"))
        both, _, gold_only, _ = clustering.pair_counts()
        gold_pairs = both + gold_only
        if gold_pairs == 0:
            return MetricValue.insufficient(
                "false split rate: gold merges no pair of mentions, so there is no "
                "denominator"
            )
        return MetricValue.measured(gold_only / gold_pairs)

    @staticmethod
    def _predicted_pairs(clustering: MentionClustering) -> int:
        both, pred_only, _, _ = clustering.pair_counts()
        return both + pred_only

    # -- calibration -------------------------------------------------------

    def _calibration_error(self) -> MetricValue:
        reasons: list[str] = []
        if not self._calibration:
            reasons.append(
                "the arm emits no per-candidate confidence (the legacy importer retains "
                "name+aliases only for concepts/models/methods)"
            )
        sample_size = len(self._calibration) if self._calibration else self._gold_item_count
        if sample_size < CALIBRATION_MIN_ITEMS:
            reasons.append(
                f"{sample_size} observations is below the {CALIBRATION_MIN_ITEMS}-item "
                "floor for a bin-populated reliability curve"
            )
        if reasons:
            return MetricValue.insufficient("calibration error: " + "; ".join(reasons))
        assert self._calibration is not None
        return MetricValue.measured(
            expected_calibration_error(self._calibration, bins=self._calibration_bins)
        )

    # -- review ------------------------------------------------------------

    def _review(self, output: ArmOutput) -> MetricValue:
        if self._review_count is None:
            return MetricValue.insufficient(
                "no review queue exists on this arm, so there is no numerator; a 0.0 would "
                "assert that nothing needed review rather than that nothing could be "
                "routed. Supply review_count and this computes. Note this is NOT the "
                "abstention rate, which kg_eval already reports separately"
            )
        produced = len(output.candidates)
        if produced == 0:
            return MetricValue.insufficient(
                "no candidates produced, so no review denominator"
            )
        return MetricValue.measured(self._review_count / produced)


__all__ = [
    "CALIBRATION_MIN_ITEMS",
    "CalibrationSample",
    "CurationMetricProvider",
    "MentionClustering",
    "expected_calibration_error",
    "NamedResourceDisposition",
    "count_named_resource_emissions",
    "named_resource_emissions_by_bucket",
    "disposition_excludes_named_resources",
    "named_resource_note",
]
