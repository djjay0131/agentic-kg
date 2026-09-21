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

    The four counts are carried because the reason is only credible with them:
    "deferred, not missing" is a claim about numbers, and a reader has to be
    able to check it. ``graded_uncommitted`` counts graded candidates that
    reached the plan and did not reach the graph.
    """

    arm_papers: tuple[ShadowArmPaper, ...] | None
    reason: str | None
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

    unmeasured = graded_deferred or graded_rejected or graded_uncommitted
    if graded_committed == 0 and unmeasured:
        outcome = (
            "no plan was executed"
            if result.execution is None
            else f"the apply came back {result.execution.outcome.value}"
        )
        return CuratedArm(
            arm_papers=None,
            reason=(
                "the KGCS curation path ran and committed no graded entity: of "
                f"the graded-type candidates it saw, {graded_deferred} were "
                f"deferred to adjudication that has not been run, "
                f"{graded_rejected} were rejected at validation, and "
                f"{graded_uncommitted} were planned but not committed "
                f"({outcome}). Reported as unavailable rather than as an empty "
                "arm: an empty arm grades to a measured recall of 0.0, which "
                "asserts the new pipeline found nothing, when the fact is that "
                "it found them and they did not reach the graph. Closing the "
                "deferred share needs the bounded adviser / review stage, not a "
                "lower threshold."
            ),
            **counts,
        )

    return CuratedArm(
        arm_papers=to_arm_papers(committed, doi_to_slug=doi_to_slug),
        reason=None,
        **counts,
    )


__all__ = ["GRADED_ENTITY_TYPES", "CuratedArm", "curated_arm"]
