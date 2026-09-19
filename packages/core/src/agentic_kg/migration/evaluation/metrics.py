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
    """
    if disposition is NamedResourceDisposition.PENDING:
        return (
            "entity precision has no defined denominator while the named-resource decision "
            "is open (docs/ground-truth/named-resources.md, owner Victoria, raised "
            "2026-08-03). The four options do not merely re-bucket the same candidates: A "
            "and C both ADD gold entries (as ResearchConcepts, or under a new "
            "expected_resources key), which moves recall's denominator as well as "
            "precision's, while B keeps the emissions in as false positives and D drops "
            "them. The same arm output therefore yields four different precisions, so any "
            "number reported now measures the labelling choice rather than the importer. "
            f"({affected} of this arm's emissions match the inventory the reviewers "
            "recorded; that inventory is gold-side and does not bound how many named "
            "resources the arm actually emits.) "
            "reconciled/paper_cskg.gold.yml: 'DECISION PENDING — do not diff against this "
            "paper until it is settled.'"
        )
    if disposition is NamedResourceDisposition.SCORED_AS_CONCEPT:
        return (
            "option A scores named resources as ResearchConcepts, which makes them recall "
            "obligations — but no reconciled file lists any under expected_concepts. The gold "
            "would have to be relabelled first; grading against today's files would count "
            "every one as a false positive, which is option B, not option A."
        )
    if disposition is NamedResourceDisposition.RESOURCE_ENTITY_TYPE:
        return (
            "option C needs a Resource entity type and an expected_resources gold key. "
            "SCHEMA.md defines neither, so there is nothing to grade Resource emissions "
            "against."
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


def count_named_resource_emissions(outcomes: Sequence[FilterOutcome]) -> int:
    """How many of an arm's emissions the open decision actually covers."""
    return sum(
        1
        for o in outcomes
        if o.excused_by is not None and o.excused_by.source_bucket == NAMED_RESOURCE_BUCKET
    )


class CurationMetricProvider:
    """ADR-0009 curation metrics, reported as honest nulls with their reasons.

    This is a ``kg_eval`` ``MetricProvider`` (structural: a ``name`` plus
    ``evaluate``), registered through ``MetricProviderRegistry`` exactly as the
    KGCS providers will be. It computes what these fixtures support and refuses
    to compute what they do not:

    ``provenance.completeness``
        The fraction of graded candidates carrying at least one resolvable
        supporting ``Evidence``. Measurable, and for the recorded legacy arm it
        is a real 0.0: the importer keeps ``quoted_text`` only for Problems, so
        no scored entity carries provenance at all. That is a finding about the
        legacy pipeline, not an artefact of this runner, and it is the reason a
        zero is correct here where it would be wrong elsewhere.

    ``false_merge_rate`` / ``false_split_rate``
        Insufficient. Both are defined over *mention clusters* — how often two
        mentions of different entities were merged, or two mentions of one entity
        split. The reconciled fixtures record entity-level canonicals and
        aliases; they carry no mention-level cluster labels, so there is nothing
        to count merges or splits against. Labelling them would be new
        ground-truth work, not a code change.

    ``calibration.ece``
        Insufficient, for two independent reasons — either alone is
        disqualifying. The arm emits no per-candidate confidence (the legacy
        importer's own caveat), and with 53 gold items the sample is below any
        sane bin-population floor.

    ``review.rate``
        Insufficient. The legacy pipeline has no review queue, so there is no
        numerator; a 0.0 would read as "nothing needed review", which is a much
        stronger claim than "nothing could be routed anywhere".
    """

    name = "curation"

    def __init__(
        self,
        *,
        gold_item_count: int,
        has_confidence: bool = False,
        has_review_queue: bool = False,
        has_cluster_labels: bool = False,
    ) -> None:
        self._gold_item_count = gold_item_count
        self._has_confidence = has_confidence
        self._has_review_queue = has_review_queue
        self._has_cluster_labels = has_cluster_labels

    def evaluate(self, output: ArmOutput, gold: GoldSet) -> Mapping[str, MetricValue]:
        return {
            "provenance.completeness": self._provenance(output),
            "false_merge_rate": self._cluster_metric("false merge rate"),
            "false_split_rate": self._cluster_metric("false split rate"),
            "calibration.ece": self._calibration(),
            "review.rate": self._review(output),
        }

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

    def _cluster_metric(self, label: str) -> MetricValue:
        if self._has_cluster_labels:
            return MetricValue.insufficient(
                f"{label}: cluster labels were declared but no comparator is wired up yet"
            )
        return MetricValue.insufficient(
            f"{label} is defined over mention clusters; the reconciled gold records "
            "entity-level canonicals and aliases only and carries no mention-level "
            "cluster labels, so there is nothing to count merges or splits against"
        )

    def _calibration(self) -> MetricValue:
        reasons: list[str] = []
        if not self._has_confidence:
            reasons.append(
                "the arm emits no per-candidate confidence (the legacy importer retains "
                "name+aliases only for concepts/models/methods)"
            )
        if self._gold_item_count < CALIBRATION_MIN_ITEMS:
            reasons.append(
                f"{self._gold_item_count} gold items is below the {CALIBRATION_MIN_ITEMS}-item "
                "floor for a bin-populated reliability curve"
            )
        if not reasons:
            return MetricValue.insufficient(
                "calibration is computable in principle but no estimator is wired up yet"
            )
        return MetricValue.insufficient("calibration error: " + "; ".join(reasons))

    def _review(self, output: ArmOutput) -> MetricValue:
        if not self._has_review_queue:
            return MetricValue.insufficient(
                "no review queue exists on this arm, so there is no numerator; a 0.0 would "
                "assert that nothing needed review rather than that nothing could be routed"
            )
        attempted = output.attempted
        if not attempted:
            return MetricValue.insufficient("no attempted-input count known")
        return MetricValue.measured(output.abstained / attempted)


__all__ = [
    "CALIBRATION_MIN_ITEMS",
    "CurationMetricProvider",
    "NamedResourceDisposition",
    "count_named_resource_emissions",
    "disposition_excludes_named_resources",
    "named_resource_note",
]
