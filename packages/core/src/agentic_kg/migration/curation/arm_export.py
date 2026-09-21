"""The `new` arm, built from what the curation run actually COMMITTED.

The evaluation runner already defines the shape this path must speak. Its
``arms`` module's ``build_new_arm`` takes ``Sequence[ArmPaper] | None`` and
returns ``ArmUnavailable`` when passed ``None``; its own docstring says "wiring
the new pipeline in is passing ``arm_papers``, not editing this module". So this
module produces exactly that and computes no metric of its own.

Note what this module deliberately does **not** do: import that subpackage. The
runner imports ``kg_eval`` unguarded, which is correct for a tool whose purpose
is ``kg_eval`` and fatal on any module reachable from a no-extras install — see
``tests/migration/test_evaluation_suite_runs.py``, which asserts the blast
radius stays zero. The hand-off is therefore by *shape*: plain dataclasses that
the runner's own types are constructed from at the call site, which is also what
the ingestion export next door does. Nothing here is imported by the runner
either, and the runner is not edited.

Committed, not extracted, not planned
-------------------------------------
The arm is built from :attr:`CurationRunResult.committed_candidate_ids` — the
candidates whose operations the executor reported ``COMMITTED``. Three weaker
joins were available and each would report a different pipeline:

* *extracted* would grade KGIS's output and call it KGCS's, which is the
  shadow-ingestion arm wearing a new label;
* *planned* would grade a batch that may have come back ``STALE`` and never
  reached the graph;
* *validated* would grade candidates the policy explicitly deferred.

The honest null, and the measured zero, are different
-----------------------------------------------------
:func:`curated_arm` returns ``arm_papers=None`` when the run produced no graded
entity **because entities were deferred** — the curation policy declined to
auto-apply them and no adviser or review stage adjudicated them. That is "not
measured". Handing ``build_new_arm`` an empty arm instead would grade to a
measured recall of 0.0, which asserts that the new pipeline found nothing; the
fact is that it found them and did not commit them. Opposite conclusions.

The distinction is discriminated, not assumed. Three ways a graded entity can
fail to reach the graph each make the result unmeasured — it was deferred, it
was rejected at validation, or it was planned and the apply did not commit
(``STALE``, ``ERROR``, ``UNSUPPORTED_OPERATION``). The third is easy to miss
and was: a replayed plan comes back ``STALE`` with nothing deferred and nothing
rejected, so a discriminator that looked only at the first two called it a
measured zero and would have reported recall 0.0 for a batch the graph merely
refused to apply twice.

When *none* of the three happened and nothing was committed, the pipeline
genuinely produced nothing for those papers, and that **is** a measured zero:
``arm_papers`` comes back as real records with empty entity tuples and the arm
is graded. A module that always returned ``None`` on an empty result would be an
honest-null policy that had quietly become "never report a bad number".

A run that saw **no candidates at all** is the fourth case, and it is not a
measured zero either. Review of this module found the original version handing
``run_curation([])`` to the runner as eight real records with ``reason=None``,
graded 0.0 — "the new pipeline found nothing" asserted about a run in which
nothing was ever submitted. Ingestion failing, an empty batch and an extractor
returning nothing all land there. :func:`curated_arm` now refuses it by its own
rule, and the measured-zero test is driven by a run that really did see
candidates, because a discriminator only ever exercised on the empty set
discriminates nothing.

A measurable arm can still be badly incomplete
----------------------------------------------
The null above is a step function at exactly zero committed: one graded entity
committed alongside fifty deferred makes the arm measurable, and a reader taking
``reason is None`` as "this is a clean measurement" would draw very nearly the
wrong conclusion the null exists to prevent. So a measurable arm whose run left
graded entities behind carries a :attr:`CuratedArm.caveat` — the arm is graded,
and the sentence travels with it. ``reason`` still means "there is no number";
``caveat`` means "there is a number, and here is what it is missing".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from agentic_kg.migration.curation._contracts import Candidate
from agentic_kg.migration.curation.pipeline import CurationRunResult
from agentic_kg.migration.ingestion.arm_export import (
    ENTITY_TYPE_TO_BUCKET,
    ShadowArmPaper,
    to_arm_papers,
)

#: The entity types the gold files score. Reused from the ingestion export
#: rather than restated: a second list here could disagree with the one that
#: actually buckets the entities, and the disagreement would show up as a
#: silently shrinking denominator rather than as an error.
GRADED_ENTITY_TYPES: frozenset[str] = frozenset(ENTITY_TYPE_TO_BUCKET)


@dataclass(frozen=True)
class CuratedArm:
    """The curated `new` arm, or the reason there is no number to report.

    ``arm_papers`` is ``None`` exactly when ``reason`` is set. Pass
    ``arm_papers`` straight to the evaluation runner's ``build_new_arm``; when
    it is ``None``, pass ``None`` and use ``reason`` as the
    ``ArmUnavailable.reason``.

    ``caveat`` is the other half and is set only when ``reason`` is not: the arm
    *is* gradeable and is nonetheless incomplete, because the run left graded
    entities deferred, rejected or uncommitted. Report it next to the number.

    The four counts are carried because the reason and the caveat are only
    credible with them:
    "deferred, not missing" is a claim about numbers, and a reader has to be
    able to check it. ``graded_uncommitted`` counts graded candidates that
    reached the plan and did not reach the graph.
    """

    arm_papers: tuple[ShadowArmPaper, ...] | None
    reason: str | None
    caveat: str | None
    graded_committed: int
    graded_deferred: int
    graded_rejected: int
    graded_uncommitted: int

    @property
    def available(self) -> bool:
        return self.arm_papers is not None


def _graded(entity_type: str | None) -> bool:
    return entity_type is not None and entity_type in GRADED_ENTITY_TYPES


def curated_arm(
    result: CurationRunResult,
    candidates: Sequence[Candidate],
    *,
    doi_to_slug: dict[str, str],
) -> CuratedArm:
    """Build the `new` arm from ``result``, or explain why it cannot be built.

    Args:
        result: The run whose committed candidates become the arm.
        candidates: The same sequence that was curated. Needed because a
            ``CurationRunResult`` carries decisions keyed by candidate id, not
            the candidates themselves, and the arm records are built from
            candidate content (aliases, source passage, scores).
        doi_to_slug: Normalised DOI -> paper slug, the join the ingestion
            export already uses. Every slug in it gets a record, including
            papers that contributed nothing — dropping one would shrink the
            recall denominator, and recall over a shrinking denominator rises
            for free.
    """
    committed_ids = set(result.committed_candidate_ids)
    committed = [c for c in candidates if c.candidate_id in committed_ids]

    graded_committed = sum(
        1 for c in committed if _graded(getattr(c, "entity_type", None))
    )
    graded_deferred = sum(1 for d in result.deferred if _graded(d.entity_type))
    graded_rejected = sum(1 for d in result.rejected if _graded(d.entity_type))

    planned_ids = set(result.planned_candidate_ids) - committed_ids
    graded_uncommitted = sum(
        1
        for c in candidates
        if c.candidate_id in planned_ids and _graded(getattr(c, "entity_type", None))
    )

    counts = dict(
        graded_committed=graded_committed,
        graded_deferred=graded_deferred,
        graded_rejected=graded_rejected,
        graded_uncommitted=graded_uncommitted,
    )

    if result.candidates_seen == 0:
        # Nothing was submitted, so nothing was measured. Grading this as a zero
        # would assert that the pipeline found nothing about eight papers it
        # never saw a candidate for.
        return CuratedArm(
            arm_papers=None,
            reason=(
                "the curation run saw no candidates at all, so nothing about "
                "these papers was measured. An empty batch, a failed ingestion "
                "and an extractor that returned nothing all arrive here, and "
                "none of them is evidence that the new pipeline found nothing."
            ),
            caveat=None,
            **counts,
        )

    unmeasured = graded_deferred or graded_rejected or graded_uncommitted
    if graded_committed == 0 and unmeasured:
        return CuratedArm(
            arm_papers=None,
            reason=(
                "the KGCS curation path ran and committed no graded entity: "
                + _shortfall(result, graded_deferred, graded_rejected, graded_uncommitted)
                + ". Reported as unavailable rather than as an empty arm: an "
                "empty arm grades to a measured recall of 0.0, which asserts the "
                "new pipeline found nothing, when the fact is that it found them "
                "and they did not reach the graph. Closing the deferred share "
                "needs the bounded adviser / review stage, not a lower threshold."
            ),
            caveat=None,
            **counts,
        )

    caveat = None
    if unmeasured:
        total = graded_committed + graded_deferred + graded_rejected + graded_uncommitted
        caveat = (
            f"this arm is gradeable but incomplete: {graded_committed} of {total} "
            f"graded-type candidates reached the graph. "
            + _shortfall(result, graded_deferred, graded_rejected, graded_uncommitted)
            + ". Recall computed over this arm is bounded above by that fraction "
            "and is not a measurement of what the pipeline can extract."
        )

    return CuratedArm(
        arm_papers=to_arm_papers(committed, doi_to_slug=doi_to_slug),
        reason=None,
        caveat=caveat,
        **counts,
    )


def _shortfall(
    result: CurationRunResult, deferred: int, rejected: int, uncommitted: int
) -> str:
    """The sentence naming where the missing graded candidates went."""
    outcome = (
        "no plan was executed"
        if result.execution is None
        else f"the apply came back {result.execution.outcome.value}"
    )
    return (
        f"{deferred} were deferred to adjudication that has not been run, "
        f"{rejected} were rejected at validation, and {uncommitted} were "
        f"planned but not committed ({outcome})"
    )


__all__ = ["GRADED_ENTITY_TYPES", "CuratedArm", "curated_arm"]
