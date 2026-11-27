"""Gate agreement evidence for the research-progression loop (ADR-0013).

The Review queue already measures adviser/human agreement. This module measures
the same thing at the three research gates: the workflow states its own
recommendation *before* a human decides, the human's decision is recorded beside
it, and ``agreed`` says whether they matched.

Nothing here writes canonical graph content. A recommendation is workflow state
stored on the ``:Canon__ResearchCycle`` record, next to the gate decision it
belongs to (ADR-0004/0005).

The statistics are deliberately simple and hand-checkable:

* ``decisions`` counts only gates that have **both** an agent recommendation and
  a human decision. An automatic decision has no human decision, so it is not
  evidence of human agreement and never enters the denominator.
* ``agreement_rate`` is ``agreed / decisions`` (``None`` when the denominator is
  zero — an honest null, not 0%).
* ``lower_bound`` is the Wilson score interval's 95% lower bound. With small
  samples a 100% observed rate still has a low bound; that is the point.
* ``promotion_met`` only *reports*. Promotion is a human edit of
  ``config/research-autonomy.yaml`` in a PR, never an automatic change.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from agentic_kg.agents.autonomy import AutonomyPolicy, GatePolicy, PromotionCriteria
from agentic_kg.agents.schemas import CheckpointType
from agentic_kg.knowledge_graph.models.research_cycle import (
    AgentRecommendation,
    GateDecision,
    ResearchCycle,
)

#: 95% two-sided normal quantile.
Z_95 = 1.959963984540054

GATE_NAMES: tuple[str, ...] = tuple(gate.value for gate in CheckpointType)

#: The proposal gate rejects a low-confidence continuation; the evaluation gate
#: approves a ``promising`` verdict. Both are documented defaults, not calibrated
#: probabilities (see ADR-0011 / ADR-0013).
PROPOSAL_CONFIDENCE_FLOOR = 0.5


def _clamp(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or number < 0.0 or number > 1.0:
        return None
    return number


def wilson_lower_bound(successes: int, n: int, z: float = Z_95) -> Optional[float]:
    """Wilson score interval lower bound for a binomial proportion.

    Returns ``None`` when there are no observations: a rate with no denominator
    is not evidence. ``successes``/``n`` are clamped only by validation; an
    out-of-range pair raises rather than silently producing a number.
    """
    if n < 0:
        raise ValueError("n must be non-negative")
    if successes < 0 or successes > n:
        raise ValueError("successes must be between 0 and n")
    if n == 0:
        return None
    phat = successes / n
    denominator = 1.0 + (z * z) / n
    centre = phat + (z * z) / (2.0 * n)
    margin = z * math.sqrt((phat * (1.0 - phat) + (z * z) / (4.0 * n)) / n)
    return max(0.0, (centre - margin) / denominator)


def build_recommendation(
    gate: str, state: Mapping[str, Any], *, produced_at: str = ""
) -> Optional[AgentRecommendation]:
    """Derive the agent's own recommendation from the workflow's pre-gate state.

    The state fields are the agents' real outputs (``ranked_problems``,
    ``proposal``, ``evaluation_result``), so the recommendation exists before the
    interrupt is released and cannot depend on the human's decision.
    """
    if gate == CheckpointType.SELECT_PROBLEM.value:
        ranked = [item for item in state.get("ranked_problems") or [] if isinstance(item, dict)]
        if not ranked:
            return None
        top = ranked[0]
        top_score = _clamp(top.get("score"))
        second_score = _clamp(ranked[1].get("score")) if len(ranked) > 1 else None
        margin = (
            round(top_score - second_score, 6)
            if top_score is not None and second_score is not None
            else None
        )
        problem_id = str(top.get("problem_id") or "")
        rationale = (
            f"top-ranked problem {problem_id or 'unknown'}"
            f" (score {top_score if top_score is not None else 'unknown'}"
            f", margin {margin if margin is not None else 'unknown'})"
        )
        return AgentRecommendation(
            action="approve",
            rationale=rationale,
            confidence=top_score,
            details={
                "problem_id": problem_id,
                "score": top_score,
                "score_margin": margin,
                "ranked_count": len(ranked),
            },
            produced_at=produced_at,
        )

    if gate == CheckpointType.APPROVE_PROPOSAL.value:
        proposal = state.get("proposal")
        if not isinstance(proposal, dict) or not proposal:
            return None
        confidence = _clamp(proposal.get("confidence"))
        action = (
            "reject"
            if confidence is not None and confidence < PROPOSAL_CONFIDENCE_FLOOR
            else "approve"
        )
        rationale = str(
            proposal.get("rationale")
            or proposal.get("title")
            or "continuation proposal"
        )
        return AgentRecommendation(
            action=action,
            rationale=rationale,
            confidence=confidence,
            details={
                "problem_id": str(proposal.get("problem_id") or ""),
                "title": str(proposal.get("title") or ""),
                "confidence_floor": PROPOSAL_CONFIDENCE_FLOOR,
            },
            produced_at=produced_at,
        )

    if gate == CheckpointType.REVIEW_EVALUATION.value:
        evaluation = state.get("evaluation_result")
        if not isinstance(evaluation, dict) or not evaluation:
            return None
        verdict = str(evaluation.get("verdict") or "").lower()
        confidence = _clamp(evaluation.get("feasibility_score"))
        provenance = evaluation.get("provenance")
        abstained = (
            bool(provenance.get("abstained"))
            if isinstance(provenance, dict) and "abstained" in provenance
            else None
        )
        action = "approve" if verdict == "promising" else "reject"
        rationale = f"evaluation verdict {verdict or 'unknown'}"
        if abstained is True:
            rationale += "; provenance envelope abstained"
        limitations = evaluation.get("limitations") or []
        if limitations:
            rationale += f"; limitations: {str(limitations[0])[:160]}"
        return AgentRecommendation(
            action=action,
            rationale=rationale,
            confidence=confidence,
            details={
                "verdict": verdict,
                "execution_success": evaluation.get("execution_success"),
                "feasibility_score": confidence,
            },
            provenance_abstained=abstained,
            produced_at=produced_at,
        )

    return None


def normalize_human_action(human_decision: Optional[str]) -> Optional[str]:
    """Map a human action to the comparison space.

    ``edit`` means "approve with changes", so at the action level it agrees with
    an approving recommendation. The raw decision is still recorded, and an edit
    that changes the recommended problem is called out in the audit trail.
    """
    if human_decision in ("approve", "edit"):
        return "approve"
    if human_decision == "reject":
        return "reject"
    return None


def recommendation_agrees(
    recommendation: Optional[AgentRecommendation], human_decision: Optional[str]
) -> Optional[bool]:
    """Whether the human's action matched the agent's recommendation."""
    action = normalize_human_action(human_decision)
    if recommendation is None or action is None:
        return None
    return recommendation.action == action


def evaluation_provenance_allows_auto(state: Mapping[str, Any]) -> bool:
    """Whether the evaluation carries a grounded (non-abstaining) envelope.

    The write gate is the one gate that changes the canonical graph. An
    abstaining provenance envelope means the result is not attributable to
    canonical assertions, so it can never be auto-approved (ADR-0013).
    """
    evaluation = state.get("evaluation_result")
    if not isinstance(evaluation, dict):
        return False
    provenance = evaluation.get("provenance")
    if not isinstance(provenance, dict):
        return False
    return provenance.get("abstained") is False


def criteria_met(
    promotion: Optional[PromotionCriteria],
    decisions: int,
    lower_bound: Optional[float],
) -> bool:
    """Whether recorded evidence satisfies a gate's promotion criteria.

    Absent criteria fail closed. An explicit zero bar (``min_decisions: 0`` and
    ``min_agreement_lower_bound: 0``) is the only way to say "no evidence
    required"; a non-human mode still has to be written by a human in the
    versioned policy file.
    """
    if promotion is None:
        return False
    if decisions < promotion.min_decisions:
        return False
    if decisions == 0:
        return promotion.min_agreement_lower_bound <= 0.0
    if lower_bound is None:
        return False
    return lower_bound >= promotion.min_agreement_lower_bound


@dataclass(frozen=True)
class AgreementDisagreement:
    """One gate where the human did not do what the agent recommended."""

    cycle_id: str
    workflow_run_id: str
    gate: str
    agent_action: str
    human_decision: str
    decided_by: str
    decided_at: str
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "workflow_run_id": self.workflow_run_id,
            "gate": self.gate,
            "agent_action": self.agent_action,
            "human_decision": self.human_decision,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class GateAgreement:
    """Agreement statistics and promotion status for one gate."""

    gate: str
    mode: str
    decisions: int = 0
    agreed: int = 0
    agreement_rate: Optional[float] = None
    lower_bound: Optional[float] = None
    min_decisions: Optional[int] = None
    min_agreement_lower_bound: Optional[float] = None
    promotion_met: bool = False
    last_disagreements: tuple[AgreementDisagreement, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "mode": self.mode,
            "decisions": self.decisions,
            "agreed": self.agreed,
            "agreement_rate": self.agreement_rate,
            "lower_bound": self.lower_bound,
            "min_decisions": self.min_decisions,
            "min_agreement_lower_bound": self.min_agreement_lower_bound,
            "promotion_met": self.promotion_met,
            "last_disagreements": [item.as_dict() for item in self.last_disagreements],
        }


@dataclass(frozen=True)
class GateAgreementReport:
    """Per-gate agreement evidence for the Research Loop page / CLI."""

    since: Optional[str] = None
    gates: tuple[GateAgreement, ...] = field(default_factory=tuple)

    @property
    def total_decisions(self) -> int:
        return sum(gate.decisions for gate in self.gates)

    @property
    def total_agreed(self) -> int:
        return sum(gate.agreed for gate in self.gates)

    def gate(self, name: str) -> Optional[GateAgreement]:
        for item in self.gates:
            if item.gate == name:
                return item
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "since": self.since,
            "total_decisions": self.total_decisions,
            "total_agreed": self.total_agreed,
            "gates": [gate.as_dict() for gate in self.gates],
        }


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _decision_gate_policy(policy: Optional[AutonomyPolicy], gate: str) -> Optional[GatePolicy]:
    if policy is None:
        return None
    return policy.gates.get(gate)


def _comparable_decisions(
    cycles: Iterable[ResearchCycle],
    *,
    gate: Optional[str],
    since: Optional[str],
) -> dict[str, list[tuple[str, str, GateDecision, bool]]]:
    since_dt = _parse_iso(since)
    comparable: dict[str, list[tuple[str, str, GateDecision, bool]]] = {
        name: [] for name in GATE_NAMES
    }
    for cycle in cycles:
        for workflow in cycle.workflow_runs:
            for decision in workflow.gate_decisions:
                if gate is not None and decision.gate != gate:
                    continue
                if since_dt is not None:
                    decided = _parse_iso(decision.decided_at)
                    if decided is None or decided < since_dt:
                        continue

                human_decision = decision.human_decision
                if human_decision is None and decision.decision in (
                    "approve",
                    "reject",
                    "edit",
                ) and not decision.decided_by.startswith("policy:"):
                    human_decision = decision.decision  # type: ignore[assignment]

                recommendation = decision.agent_recommendation
                agreed = decision.agreed
                if agreed is None:
                    agreed = recommendation_agrees(recommendation, human_decision)
                if human_decision is None or agreed is None or recommendation is None:
                    # No human decision, or no recommendation to compare: not
                    # agreement evidence. Auto decisions deliberately land here.
                    continue
                comparable[decision.gate].append(
                    (
                        decision.cycle_id or cycle.cycle_id,
                        decision.workflow_run_id or workflow.workflow_run_id,
                        decision,
                        agreed,
                    )
                )
    return comparable


def build_agreement_report(
    cycles: Iterable[ResearchCycle],
    *,
    policy: Optional[AutonomyPolicy] = None,
    gate: Optional[str] = None,
    since: Optional[str] = None,
    last_n: int = 10,
) -> GateAgreementReport:
    """Aggregate recorded gate decisions into per-gate evidence.

    ``gate`` narrows the report to one gate; ``since`` filters by ``decided_at``.
    ``last_n`` caps the disagreement examples included per gate.
    """
    if gate is not None and gate not in GATE_NAMES:
        raise ValueError(f"unknown gate: {gate!r}")
    wanted = (gate,) if gate is not None else GATE_NAMES
    comparable = _comparable_decisions(cycles, gate=gate, since=since)

    results: list[GateAgreement] = []
    for name in wanted:
        rows = comparable.get(name, [])
        decisions = len(rows)
        agreed = sum(1 for _, _, _, matched in rows if matched)
        rate = (agreed / decisions) if decisions else None
        lower = wilson_lower_bound(agreed, decisions)
        gate_policy = _decision_gate_policy(policy, name)
        promotion = gate_policy.promotion if gate_policy is not None else None
        mode = gate_policy.mode if gate_policy is not None else "unknown"

        disagreements = [
            AgreementDisagreement(
                cycle_id=cycle_id,
                workflow_run_id=workflow_run_id,
                gate=name,
                agent_action=decision.agent_recommendation.action,
                human_decision=decision.human_decision or decision.decision,
                decided_by=decision.decided_by,
                decided_at=decision.decided_at,
                reason=decision.disagreement_reason or decision.note,
            )
            for cycle_id, workflow_run_id, decision, matched in rows
            if not matched
        ]
        disagreements.sort(key=lambda item: item.decided_at, reverse=True)
        results.append(
            GateAgreement(
                gate=name,
                mode=mode,
                decisions=decisions,
                agreed=agreed,
                agreement_rate=rate,
                lower_bound=lower,
                min_decisions=promotion.min_decisions if promotion else None,
                min_agreement_lower_bound=(
                    promotion.min_agreement_lower_bound if promotion else None
                ),
                promotion_met=criteria_met(promotion, decisions, lower),
                last_disagreements=tuple(disagreements[:last_n]),
            )
        )
    return GateAgreementReport(since=since, gates=tuple(results))


def promotion_by_gate(report: GateAgreementReport) -> dict[str, bool]:
    """The runtime view: whether each gate's criteria are met."""
    return {gate.gate: gate.promotion_met for gate in report.gates}


__all__ = [
    "AgreementDisagreement",
    "GATE_NAMES",
    "GateAgreement",
    "GateAgreementReport",
    "PROPOSAL_CONFIDENCE_FLOOR",
    "Z_95",
    "build_agreement_report",
    "build_recommendation",
    "criteria_met",
    "evaluation_provenance_allows_auto",
    "normalize_human_action",
    "promotion_by_gate",
    "recommendation_agrees",
    "wilson_lower_bound",
]
