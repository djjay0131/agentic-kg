"""The three arms of the comparison, including the one that cannot run yet.

The comparison this runner exists to produce is *legacy vs new vs gold*. Only one
of those three arms has a producer today, and how the other two are represented
is the difference between an honest report and a flattering one.

``legacy``
    The recorded output of the existing importer, committed at
    ``docs/ground-truth/importer-output/``. A recorded fixture, not a live run:
    replaying it needs no model call, no network and no database, which is what
    makes the whole runner deterministic and CI-safe.

``gold``
    A synthetic perfect arm — the gold set re-emitted as candidates. It scores
    1.0 on everything by construction, which is exactly its job: it is the
    runner's own control. If ``gold`` ever scores below 1.0, the adapter has a
    key-construction bug and every other number in the report is suspect. An
    evaluation harness with no self-check is a number generator.

``new``
    The KGIS/KGCS path. **It has no admissible producer yet.** It is
    represented as :class:`ArmUnavailable`, not as an ``ArmOutput`` with no
    candidates — the distinction is the honest-null policy applied one level up
    from ``MetricValue``. An empty ``ArmOutput`` grades to a measured recall of
    0.0, which reads as "the new pipeline found nothing"; the truth is "the new
    pipeline produced output the harness declines to grade". Those are opposite
    facts and a reader acting on the first would draw exactly the wrong
    conclusion.

    The reason changed, and the change matters. When this module was written
    the shadow path did not exist. It does now: one run emits 252 candidates,
    29 of which are gradeable surfaces on the two reconciled papers
    (``migration/ingestion/arm_export.py``). Every one of them comes from the
    replay client in ``migration/ingestion/replay.py``, whose responses are
    **derived from the legacy importer's own committed output**. Grading them
    as ``new`` would compare the legacy arm against a copy of itself and report
    a flattering, meaningless score. The missing input is a *model recording*,
    not a pipeline.

A note on ``ArmOutput.failed``, because it is easy to misread: it is an ``int``
count of *inputs the arm failed on*, bounded by ``attempted`` — not a flag
meaning "this arm could not run". It is the right home for the four of eight
importer-output files that are stubs or metadata-only, and the wrong home for
an arm with no producer. Both usages appear below, kept distinct.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from kg_contracts.candidates import Candidate, EntityCandidate
from kg_contracts.evidence import Evidence
from kg_eval.arms import ArmConfig, ArmOutput, CostLatency
from kg_eval.goldset import GoldSet

from agentic_kg.migration.evaluation.adapter import (
    FilterOutcome,
    SurfaceIndex,
    build_candidates,
    filter_candidates,
)
from agentic_kg.migration.evaluation.corpus import (
    ArmEntity,
    ArmPaper,
    CorpusCoverage,
    ReconciledPaper,
)
from agentic_kg.migration.evaluation.metrics import (
    NamedResourceDisposition,
    disposition_excludes_named_resources,
)

LEGACY_ARM = "legacy"
NEW_ARM = "new"
GOLD_ARM = "gold"

#: Why the ``new`` arm is unavailable, stated as the reason that is *currently*
#: true rather than the one that was true when this module was written.
#:
#: Kept byte-identical to ``migration.ingestion.arm_export.UNAVAILABLE_REASON``,
#: which is where the producing side says the same thing. It is restated here
#: rather than imported because ``evaluation`` must stay importable without the
#: whole ``kgis`` ingestion stack; the two are asserted to agree by
#: ``tests/migration/ingestion/test_new_arm_availability.py``.
UNAVAILABLE_REASON = (
    "the KGIS shadow path runs, but only against a replay client whose responses "
    "are derived from the legacy importer's own committed output — not from a "
    "model. Grading it as the `new` arm would compare the legacy arm against a "
    "copy of itself and report a meaningless 1.0. A real provider recording "
    "(kgis RecordingCompletionClient, see migration/ingestion/replay.py) is the "
    "one remaining step; until it exists this arm is unavailable, not empty."
)


@dataclass(frozen=True)
class ArmUnavailable:
    """An arm that could not be run at all, and why.

    Distinct from an ``ArmOutput`` that produced nothing. Every ablation
    involving an unavailable arm is INSUFFICIENT_EVIDENCE; none of its metrics
    is reported as a number.
    """

    arm_id: str
    reason: str


@dataclass(frozen=True)
class BuiltArm:
    """A runnable arm: its ``ArmOutput`` plus the audit trail behind it.

    ``arm_papers`` and ``index`` are carried so diagnostics that need the raw,
    pre-grading emissions can run without re-deriving them. The cross-type
    diagnostic is the case in point: it has to look at surfaces the strict
    grader already discarded, which by then are gone from ``output.candidates``.
    """

    output: ArmOutput
    outcomes: tuple[FilterOutcome, ...]
    coverage: CorpusCoverage
    arm_papers: tuple[ArmPaper, ...] = ()
    index: SurfaceIndex | None = None

    @property
    def arm_id(self) -> str:
        return self.output.arm.arm_id

    @property
    def excluded_count(self) -> int:
        return sum(1 for o in self.outcomes if not o.kept)


ArmResult = BuiltArm | ArmUnavailable


def build_legacy_arm(
    arm_papers: Sequence[ArmPaper],
    index: SurfaceIndex,
    *,
    graded_slugs: frozenset[str],
    disposition: NamedResourceDisposition = NamedResourceDisposition.PENDING,
    arm_id: str = LEGACY_ARM,
) -> BuiltArm:
    """Build the legacy arm, graded over the papers that have reconciled gold.

    ``graded_slugs`` restricts grading to the two reconciled papers, and that
    restriction is not a convenience. Grading all eight against a two-paper gold
    set would make every entity from the other six a false positive — the
    importer would be penalised for extracting papers nobody has labelled.

    The corpus-wide picture is not discarded, it is reported separately as
    :class:`CorpusCoverage`: eight attempted, four failed (``status`` other than
    ``extracted``). Keeping the two denominators apart is what lets a reader see
    both "the metrics describe two papers" and "half the corpus never produced
    an extraction to measure" without mistaking either for the other.
    """
    graded = tuple(p for p in arm_papers if p.slug in graded_slugs)
    outcomes = filter_candidates(
        graded,
        index,
        exclude_extras=True,
        exclude_named_resources=disposition_excludes_named_resources(disposition),
    )
    candidates, evidence = build_candidates(arm_id, graded, outcomes)

    coverage = CorpusCoverage(
        corpus_attempted=len(arm_papers),
        corpus_failed=sum(1 for p in arm_papers if not p.succeeded),
        graded_attempted=len(graded),
        graded_failed=sum(1 for p in graded if not p.succeeded),
    )

    return BuiltArm(
        output=ArmOutput(
            arm=ArmConfig(
                arm_id=arm_id,
                description=(
                    "Recorded output of the existing agentic-kg importer "
                    "(docs/ground-truth/importer-output/), graded over the papers with "
                    "reconciled gold."
                ),
                config={
                    "source": "docs/ground-truth/importer-output",
                    "named_resource_disposition": disposition.value,
                    "graded_slugs": sorted(graded_slugs),
                },
            ),
            candidates=candidates,
            evidence=evidence,
            attempted=coverage.graded_attempted,
            abstained=0,
            failed=coverage.graded_failed,
            # Not measured: the importer-output fixtures record entities and
            # metadata, never token counts, spend or wall time. `None` rather
            # than zeros, so no reader can conclude the legacy run was free.
            cost=None,
        ),
        outcomes=outcomes,
        coverage=coverage,
        arm_papers=tuple(graded),
        index=index,
    )


def build_gold_arm(
    papers: Sequence[ReconciledPaper],
    index: SurfaceIndex,
    *,
    arm_id: str = GOLD_ARM,
) -> BuiltArm:
    """Build the perfect-arm control: the gold set re-emitted as candidates.

    Scoring anything other than 1.0 precision and 1.0 recall on this arm means
    the adapter builds candidate keys that do not line up with the gold keys it
    also builds, and the runner is measuring itself rather than any pipeline.
    That is the assertion the control exists to make available.
    """
    arm_papers = tuple(
        ArmPaper(
            slug=paper.slug,
            status="extracted",
            entities=tuple(
                ArmEntity(
                    slug=e.slug,
                    bucket=e.bucket,
                    name=e.canonical,
                    aliases=e.aliases,
                    quoted_text=e.quoted_text,
                    confidence=e.confidence,
                )
                for e in paper.entities
            ),
            citations=paper.citations,
        )
        for paper in papers
    )
    outcomes = filter_candidates(arm_papers, index)
    candidates, evidence = build_candidates(arm_id, arm_papers, outcomes)
    coverage = CorpusCoverage(
        corpus_attempted=len(papers),
        corpus_failed=0,
        graded_attempted=len(papers),
        graded_failed=0,
    )
    return BuiltArm(
        output=ArmOutput(
            arm=ArmConfig(
                arm_id=arm_id,
                description="Control arm: the reconciled gold re-emitted as candidates.",
                config={"source": "reconciled gold", "control": True},
            ),
            candidates=candidates,
            evidence=evidence,
            attempted=len(papers),
            abstained=0,
            failed=0,
            cost=CostLatency(latency_seconds=0.0, cost_usd=0.0, tokens=0),
        ),
        outcomes=outcomes,
        coverage=coverage,
        arm_papers=arm_papers,
        index=index,
    )


def build_new_arm(
    arm_papers: Sequence[ArmPaper] | None,
    index: SurfaceIndex,
    *,
    graded_slugs: frozenset[str],
    disposition: NamedResourceDisposition = NamedResourceDisposition.PENDING,
    arm_id: str = NEW_ARM,
) -> ArmResult:
    """Build the KGIS/KGCS arm, or report that it has no producer.

    Accepts recorded output the moment one exists, so the arm structure is real
    today rather than a placeholder to be rewritten later: wiring the new
    pipeline in is passing ``arm_papers``, not editing this module.
    """
    if arm_papers is None:
        return ArmUnavailable(
            arm_id=arm_id,
            reason=UNAVAILABLE_REASON,
        )
    outcomes = filter_candidates(
        tuple(p for p in arm_papers if p.slug in graded_slugs),
        index,
        exclude_extras=True,
        exclude_named_resources=disposition_excludes_named_resources(disposition),
    )
    graded = tuple(p for p in arm_papers if p.slug in graded_slugs)
    candidates, evidence = build_candidates(arm_id, graded, outcomes)
    coverage = CorpusCoverage(
        corpus_attempted=len(arm_papers),
        corpus_failed=sum(1 for p in arm_papers if not p.succeeded),
        graded_attempted=len(graded),
        graded_failed=sum(1 for p in graded if not p.succeeded),
    )
    return BuiltArm(
        output=ArmOutput(
            arm=ArmConfig(
                arm_id=arm_id,
                description="KGIS/KGCS extraction path.",
                config={"named_resource_disposition": disposition.value},
            ),
            candidates=candidates,
            evidence=evidence,
            attempted=coverage.graded_attempted,
            abstained=0,
            failed=coverage.graded_failed,
            cost=None,
        ),
        outcomes=outcomes,
        coverage=coverage,
        arm_papers=tuple(graded),
        index=index,
    )


def candidates_of(arm: ArmResult) -> tuple[Candidate, ...]:
    return arm.output.candidates if isinstance(arm, BuiltArm) else ()


def evidence_of(arm: ArmResult) -> dict[str, Evidence]:
    return dict(arm.output.evidence) if isinstance(arm, BuiltArm) else {}


def goldset_covers(arm: ArmResult, gold: GoldSet) -> bool:
    """Whether this arm can be graded against ``gold`` at all."""
    return isinstance(arm, BuiltArm) and bool(gold.entities or gold.relations)


def restrict_to_entity_type(output: ArmOutput, entity_type: str) -> ArmOutput:
    """Narrow an arm's output to one entity type, for a per-type scorecard.

    ``kg_eval`` grades whatever candidates it is given against whatever gold it
    is given, and ``match_entities`` looks at *all* ``EntityCandidate``s. Handing
    it a single-type gold slice without also narrowing the candidates makes every
    candidate of the other three types a false positive of this one: Topic
    precision would be computed over 53 candidates against 4 gold items, which is
    not a number about topics at all.

    Relation candidates are dropped too — a per-entity-type scorecard has no
    relation slice, and leaving them in would report their FP count under each of
    the four types in turn.
    """
    kept = tuple(
        c
        for c in output.candidates
        if isinstance(c, EntityCandidate) and c.entity_type == entity_type
    )
    keep_ids = {ref.evidence_id for c in kept for ref in c.evidence_refs}
    return output.model_copy(
        update={
            "candidates": kept,
            "evidence": {k: v for k, v in output.evidence.items() if k in keep_ids},
        }
    )


__all__ = [
    "GOLD_ARM",
    "UNAVAILABLE_REASON",
    "LEGACY_ARM",
    "NEW_ARM",
    "ArmResult",
    "ArmUnavailable",
    "BuiltArm",
    "build_gold_arm",
    "build_legacy_arm",
    "build_new_arm",
    "candidates_of",
    "evidence_of",
    "goldset_covers",
    "restrict_to_entity_type",
]
