"""Bounded-adviser adjudication of the candidates the deterministic policy deferred.

The Phase-5 report measured the `new` arm as an **honest null**: the KGCS
curation path ran end to end over the corpus and committed the eight structured
``Paper`` identities, but no graded research entity, because the LLM extractor's
``Method`` / ``Model`` / ``ResearchConcept`` candidates carry
``extraction_confidence=0.8`` — below the unmoved ``auto_min_extraction=0.95`` —
and route ``LLM_ASSESS``. Its own closing sentence names the missing piece:
*"Closing the deferred share needs the bounded adviser / review stage, not a
lower threshold."* This module is that stage, for the adopter's entity
candidates.

What it is, and what it deliberately is not
-------------------------------------------
The stage runs **after** deterministic routing and consumes its decisions. For
every validated candidate the deterministic policy routed ``LLM_ASSESS`` it
consults one bounded adviser — a
:class:`~agentic_kg.migration.curation.adjudication.CandidateAdmissionAdviser`,
which is a ``kgcs.advisers.StructuredAdviser`` and therefore returns only an
``AdviserAssessment`` and holds no write surface (KGCS §9 law 16) — and folds
the assessment through a deterministic gate. Only a gate that admits the
candidate mints a resolving decision for it.

**Upstream gap, stated rather than worked around (adoption rule 7).** The task
brief proposed routing these candidates "through the KGCS ``CurationOrchestrator``".
That class adjudicates an **entity-resolution pair** (a ``MatchResult`` plus a
``CurationProfile``) and returns an ``ErDecision`` about linking; it is not a
candidate-admission gate, and KGCS v2.0.0 ships no bounded-adviser → promotion
router. The two KGCS paths that do turn advice into a plan are
``kgcs.recuration.router.EvolutionRouter`` (assertion recommendations only) and
``kgcs.review.operations.ReviewRouter`` (a **human** ``ReviewAction``, with
``ConceptEvolutionPlanner.plan_promotion`` for a candidate). Neither maps an
adviser assessment to a candidate admission. So the gate below is adopter-side
and declared as such: it decides *whether* to admit, and the plan operation is
then built by KGCS's own ``CurationPlanner``, exactly as the deterministic path
builds it. The adopter contributes a decision, never an operation, a plan, or a
write — which is the line rule 7 draws. Filed as an upstream gap on the PR.

Why ``CurationPlanner`` and not ``plan_promotion``
--------------------------------------------------
``ConceptEvolutionPlanner.plan_promotion`` (the review path's promotion) builds a
*separate* plan per candidate, each stamped with its own snapshot precondition.
Executing those plans one after another cannot work: the first advances the
graph epoch, and the second's precondition then fails ``STALE``. Executing them
as one batch would mean this module assembling a combined ``CurationPlan`` and
its per-identity guards by hand — strictly more adopter-side plan construction,
not less. Re-using ``CurationPlanner`` — the same class ``CurationEngine`` uses —
plans the deterministic candidates and the admitted ones together, in one plan,
with the planner's own guards and deterministic ids. The trade is that the
admitted candidate's operation does not carry the ``trigger_id`` provenance
``plan_promotion`` would stamp; the advice and the gate's reason live on
:class:`AdmissionDecision`, carried back on the run result, instead.

Law 1 (baseline survives every failure)
---------------------------------------
``StructuredAdviser.assess`` turns every port error, timeout and malformed
payload into an abstain, and this module's gate holds on an abstain. With the
adviser removed, failing, or returning garbage, no candidate is promoted and the
deterministic plan is byte-identical to a run with the stage off. The tests
assert that by comparing plan JSON, not by asserting the adviser was not called.

Law 13 (advice cannot flip a reject)
------------------------------------
Eligibility is read off the deterministic ``ResolutionDecision``, not the
candidate's content: only candidates that validated **and** routed
``LLM_ASSESS`` are ever shown to the adviser. A candidate the validator rejected
has ``resolution is None`` and is unreachable here, so however loudly an adviser
recommends admission it cannot reach it. A candidate the policy already resolved
``AUTO`` is not re-decided either — the adviser stage never widens the
deterministic surface, it only fills the gap the policy explicitly left.

Law 16 (advisers have no write surface)
---------------------------------------
An ``Adviser`` returns an ``AdviserAssessment`` — a frozen bag of
recommendation/evidence/confidence — and holds no ``GraphMutationStore``. The
stage returns ``ResolvedCandidate``s and an ``AdjudicationResult``; no object it
produces is, or holds, a write surface. ``test_adjudication.py`` asserts the
structural half and ``test_no_application_write_surface.py`` asserts the
syntactic half over this module.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from agentic_kg.migration.curation._contracts import (
    AdjudicationRoute,
    Adviser,
    AdviserAssessment,
    AdviserQuestion,
    Candidate,
    EngineResult,
    EntityCandidate,
    IdFactory,
    ResolutionDecision,
    ResolvedCandidate,
    StructuredAdviser,
    score_vector,
)

#: The graph an admission's minted identity belongs to. Bound through the
#: candidate rather than restated, so an admission cannot mint into a graph the
#: candidate was validated against — the validator already rejected a
#: cross-graph candidate, and this keeps the two from drifting.


class AdmissionRecommendation(StrEnum):
    """What the bounded admission adviser may recommend (spec §7.4 shape).

    ``HOLD`` is the conservative fallback and the abstain value: an adviser that
    cannot tell never admits. ``ADMIT`` is advice only — the deterministic gate
    below decides whether it is sufficient, and the planner decides the
    operation.
    """

    ADMIT = "admit"
    HOLD = "hold"


def _values(enum: type[StrEnum]) -> frozenset[str]:
    """The value set of a recommendation enum (its permitted recommendations)."""
    return frozenset(member.value for member in enum)


class CandidateAdmissionAdviser(StructuredAdviser):
    """Bounded adviser for one extracted candidate's admission question.

    A thin, data-only subclass of KGCS's ``StructuredAdviser``: it fixes a
    recommendation vocabulary, a conservative fallback, a template id/version
    and an instruction, and inherits the shared render → complete → parse →
    abstain pipeline unchanged. It returns an ``AdviserAssessment`` and only an
    ``AdviserAssessment`` — a recommendation value, cited evidence ids,
    contradictions and a confidence/abstain — and holds only an injected
    ``CompletionPort`` (KGCS §9 law 16). This is the one *domain* adviser the
    adopter declares; the machinery, the provenance model and the no-write law
    are all KGCS's.
    """

    ADVISER_TYPE = "candidate_admission"
    ADVISER_VERSION = "candidate_admission/1"
    TEMPLATE_ID = "candidate_admission"
    PROMPT_VERSION = "1"
    INSTRUCTION = (
        "You are a bounded candidate-admission adviser. Using only the cited "
        "evidence, recommend whether the extracted research entity should be "
        "admitted as a canonical concept (admit) or held back (hold). The "
        "deterministic policy deferred this candidate because its extraction "
        "confidence is below the auto-apply threshold; your job is to say "
        "whether the evidence supports admitting it anyway. Cite the evidence "
        "ids you relied on and list any contradictions. You never write: your "
        "output is advice for a deterministic gate."
    )
    RECOMMENDATIONS: ClassVar[frozenset[str]] = _values(AdmissionRecommendation)
    INSUFFICIENT = AdmissionRecommendation.HOLD.value


@dataclass(frozen=True)
class AdmissionPolicy:
    """The deterministic gate that folds advice into an admission decision.

    Thresholds are *data*, declared here in one place, in the spirit of
    ``ConfidencePolicy``: tightening or loosening automation is a config change,
    never a code change. The gate is conservative by construction — every path
    that is not an explicit, evidence-citing, sufficiently-confident ``ADMIT``
    holds the candidate and leaves the deterministic baseline untouched.
    """

    #: The lowest adviser confidence that admits. The synthetic oracle below
    #: scores above this; the number exists so a recorded fixture with a low
    #: confidence is *held*, which is what the tests exercise.
    min_confidence: float = 0.5
    #: An admission must cite at least one of the evidence ids it was given.
    #: A recommendation with no citation is advice about nothing.
    require_cited_evidence: bool = True


#: The policy used when the caller does not supply one. Conservative: a bare
#: ``admit`` with no confidence or no citation is held.
DEFAULT_ADMISSION_POLICY = AdmissionPolicy()


@dataclass(frozen=True)
class AdmissionDecision:
    """One candidate's admission verdict and the advice behind it.

    ``assessment`` is ``None`` only when the candidate was never eligible (it
    was not an entity candidate that routed ``LLM_ASSESS``); every decision that
    *did* consult an adviser carries the assessment, abstained or not, so the
    adjudication is auditable without re-running the model.
    """

    candidate_id: str
    admitted: bool
    reason: str
    assessment: AdviserAssessment | None = None

    @property
    def recommendation(self) -> str | None:
        return self.assessment.recommendation if self.assessment is not None else None

    @property
    def confidence(self) -> float | None:
        return self.assessment.confidence if self.assessment is not None else None

    @property
    def cited_evidence(self) -> tuple[str, ...]:
        return self.assessment.evidence_ids if self.assessment is not None else ()


@dataclass(frozen=True)
class AdjudicationResult:
    """What the adviser stage decided, and which candidates it promoted.

    ``promoted`` are the ``ResolvedCandidate``s a caller should plan **in
    addition to** the deterministic ones; each carries an ``AUTO``
    ``ResolutionDecision`` the gate produced. ``decisions`` has one entry per
    eligible candidate consulted (held and admitted alike). An empty
    ``promoted`` with a non-empty ``decisions`` is the honest "consulted, held
    everything" outcome — the stage ran and changed nothing.
    """

    promoted: tuple[ResolvedCandidate, ...] = ()
    decisions: tuple[AdmissionDecision, ...] = ()
    eligible: int = 0
    admitted: int = 0
    held: int = 0

    @property
    def consulted(self) -> bool:
        """True iff at least one candidate was shown to the adviser."""
        return self.eligible > 0

    def promoted_candidate_ids(self) -> tuple[str, ...]:
        return tuple(item.candidate.candidate_id for item in self.promoted)


def admission_question(candidate: EntityCandidate) -> AdviserQuestion:
    """The deterministic, reproducible admission question for one candidate.

    A pure function of the candidate: the same candidate always yields the same
    question, so the rendered prompt (and therefore a ``RecordedCompletionClient``
    fixture key) is reproducible without hidden state. Evidence ids are taken in
    the candidate's own order — the order is part of the request key, so a set
    here would make replay keys drift.
    """
    evidence_ids = tuple(ref.evidence_id for ref in candidate.evidence_refs)
    context = [
        f"entity_type: {candidate.entity_type}",
        f"semantic_key: {candidate.semantic_key}",
    ]
    if candidate.display_name:
        context.append(f"display_name: {candidate.display_name}")
    passage = candidate.representations.get("source_passage")
    text = getattr(passage, "text", None)
    if isinstance(text, str) and text:
        context.append(f"source_passage: {text}")
    return AdviserQuestion(
        kind="entity_admission",
        subject=candidate.semantic_key,
        evidence_ids=evidence_ids,
        context=tuple(context),
        trace_id=candidate.trace_id,
    )


def _gate(
    policy: AdmissionPolicy, candidate_id: str, assessment: AdviserAssessment
) -> AdmissionDecision:
    """Fold one assessment through the deterministic gate (never raises)."""
    if assessment.abstained:
        return AdmissionDecision(
            candidate_id=candidate_id,
            admitted=False,
            reason=f"adviser abstained: {assessment.rationale}",
            assessment=assessment,
        )
    if assessment.recommendation != AdmissionRecommendation.ADMIT.value:
        return AdmissionDecision(
            candidate_id=candidate_id,
            admitted=False,
            reason=(
                f"adviser recommended {assessment.recommendation!r}, not "
                f"{AdmissionRecommendation.ADMIT.value!r}"
            ),
            assessment=assessment,
        )
    if policy.require_cited_evidence and not assessment.evidence_ids:
        return AdmissionDecision(
            candidate_id=candidate_id,
            admitted=False,
            reason="adviser admitted with no cited evidence",
            assessment=assessment,
        )
    if assessment.confidence is None or assessment.confidence < policy.min_confidence:
        return AdmissionDecision(
            candidate_id=candidate_id,
            admitted=False,
            reason=(
                f"adviser confidence {assessment.confidence!r} is below the "
                f"gate's min_confidence={policy.min_confidence}"
            ),
            assessment=assessment,
        )
    return AdmissionDecision(
        candidate_id=candidate_id,
        admitted=True,
        reason=(
            f"admitted: recommendation={assessment.recommendation!r}, "
            f"confidence={assessment.confidence}, "
            f"cited={len(assessment.evidence_ids)} evidence id(s)"
        ),
        assessment=assessment,
    )


def eligible_llm_assess_entities(
    candidates: Sequence[Candidate], engine_result: EngineResult
) -> tuple[EntityCandidate, ...]:
    """The entity candidates the deterministic policy deferred to ``LLM_ASSESS``.

    The single definition of eligibility, used by the adjudication run and by
    the fixture recorder, so a recording can never miss a question the run will
    ask (which would surface as a ``CompletionMiss``) or record one it never
    asks. Eligibility is read off the deterministic decision — never the
    candidate's content — so a rejected candidate has no resolution and is
    absent here (law 13).
    """
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    eligible: list[EntityCandidate] = []
    for outcome in engine_result.outcomes:
        resolution = outcome.resolution
        if resolution is None or resolution.route is not AdjudicationRoute.LLM_ASSESS:
            continue
        candidate = by_id[outcome.candidate_id]
        if isinstance(candidate, EntityCandidate):
            eligible.append(candidate)
    return tuple(eligible)


def adjudicate_llm_assess_candidates(
    candidates: Sequence[Candidate],
    engine_result: EngineResult,
    *,
    adviser: Adviser,
    policy: AdmissionPolicy = DEFAULT_ADMISSION_POLICY,
    id_factory: IdFactory,
    snapshot_version: str,
) -> AdjudicationResult:
    """Consult the bounded adviser over the deferred entity candidates.

    Args:
        candidates: The same sequence the deterministic engine curated.
        engine_result: The engine's own decisions. Eligibility is read from
            here — a candidate with no ``ResolutionDecision`` was rejected by
            validation and is never shown to an adviser (law 13).
        adviser: The bounded adviser. Injected, never constructed here, so a
            test can supply a failing, timeouting or recorded client.
        policy: The deterministic gate. Defaults to ``DEFAULT_ADMISSION_POLICY``.
        id_factory: Mints the identity each admitted candidate resolves to.
            The same factory the deterministic path uses, so a promoted
            identity replays byte-identically.
        snapshot_version: The graph snapshot the promoted decisions assert.
            Health hazard if stale — the executor checks it.

    Returns:
        An :class:`AdjudicationResult`. Nothing here touches a graph; a caller
        that has a store plans and executes the promoted candidates through the
        ordinary ``PlanExecutor`` path.
    """
    promoted: list[ResolvedCandidate] = []
    decisions: list[AdmissionDecision] = []

    for candidate in eligible_llm_assess_entities(candidates, engine_result):
        # A relation/attribute candidate whose endpoints are unresolved aliases
        # needs entity resolution, not an admission decision, and an artifact
        # has no v1 operation at all; both are absent from eligibility.
        assessment = adviser.assess(admission_question(candidate))
        decision = _gate(policy, candidate.candidate_id, assessment)
        decisions.append(decision)
        if not decision.admitted:
            continue

        promoted.append(
            ResolvedCandidate(
                candidate=candidate,
                resolution=ResolutionDecision(
                    candidate_id=candidate.candidate_id,
                    resolved_identity=id_factory.identity_id(
                        candidate.graph_id, candidate.candidate_id
                    ),
                    create_new_identity=True,
                    route=AdjudicationRoute.AUTO,
                    score_vector=score_vector(candidate.scores),
                    matcher_version=None,
                    snapshot_version=snapshot_version,
                    trace_id=candidate.trace_id,
                ),
            )
        )

    admitted = sum(1 for d in decisions if d.admitted)
    return AdjudicationResult(
        promoted=tuple(promoted),
        decisions=tuple(decisions),
        eligible=len(decisions),
        admitted=admitted,
        held=len(decisions) - admitted,
    )


__all__ = [
    "DEFAULT_ADMISSION_POLICY",
    "AdmissionDecision",
    "AdmissionPolicy",
    "AdmissionRecommendation",
    "AdjudicationResult",
    "CandidateAdmissionAdviser",
    "adjudicate_llm_assess_candidates",
    "admission_question",
    "eligible_llm_assess_entities",
]
