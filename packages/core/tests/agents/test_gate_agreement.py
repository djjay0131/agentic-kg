"""Gate-agreement evidence: maths, recommendations, and promotion status."""

from __future__ import annotations

import pytest
from agentic_kg.agents.autonomy import PromotionCriteria, parse_policy
from agentic_kg.agents.gate_agreement import (
    build_agreement_report,
    build_recommendation,
    criteria_met,
    evaluation_provenance_allows_auto,
    recommendation_agrees,
    wilson_lower_bound,
)
from agentic_kg.knowledge_graph.models.research_cycle import (
    AgentRecommendation,
    CycleWorkflow,
    GateDecision,
    ResearchCycle,
)


def _cycle(*decisions: GateDecision) -> ResearchCycle:
    return ResearchCycle(
        cycle_id="cycle-1",
        namespace="canon",
        started_at="2026-10-26T03:00:00Z",
        status="awaiting_gate",
        policy_version="1",
        workflow_runs=[
            CycleWorkflow(
                workflow_run_id="wf-1",
                problem_id="p0",
                state="awaiting_gate",
                pending_gate="select_problem",
                gate_decisions=list(decisions),
            )
        ],
    )


def _decision(
    *,
    gate: str = "select_problem",
    action: str = "approve",
    human: str = "approve",
    note: str = "",
    decided_at: str = "2026-10-26T04:00:00Z",
    recommendation: AgentRecommendation | None = None,
) -> GateDecision:
    rec = recommendation or AgentRecommendation(action=action)
    return GateDecision(
        gate=gate,  # type: ignore[arg-type]
        decision=human,  # type: ignore[arg-type]
        decided_by="alice",
        decided_at=decided_at,
        note=note,
        cycle_id="cycle-1",
        workflow_run_id="wf-1",
        policy_version="1",
        agent_recommendation=rec,
        human_decision=human,  # type: ignore[arg-type]
        agreed=recommendation_agrees(rec, human),
        disagreement_reason=note if recommendation_agrees(rec, human) is False else "",
    )


def test_wilson_lower_bound_is_an_honest_lower_bound():
    # 9/10 observed still has a lower bound well below 0.9.
    assert wilson_lower_bound(9, 10) == pytest.approx(0.5958, abs=1e-4)
    # 0/1 and 1/1 never claim certainty at this sample size.
    assert wilson_lower_bound(0, 1) == 0.0
    assert wilson_lower_bound(1, 1) == pytest.approx(0.2065, abs=1e-4)
    # No observations is an honest null, not 0%.
    assert wilson_lower_bound(0, 0) is None


@pytest.mark.parametrize(
    "successes,n",
    [(-1, 2), (3, 2)],
)
def test_wilson_lower_bound_rejects_impossible_counts(successes, n):
    with pytest.raises(ValueError):
        wilson_lower_bound(successes, n)


def test_select_problem_recommendation_has_top_id_and_margin():
    recommendation = build_recommendation(
        "select_problem",
        {
            "ranked_problems": [
                {"problem_id": "p0", "score": 0.91},
                {"problem_id": "p1", "score": 0.74},
            ]
        },
        produced_at="2026-10-26T03:00:00Z",
    )
    assert recommendation is not None
    assert recommendation.action == "approve"
    assert recommendation.details["problem_id"] == "p0"
    assert recommendation.details["score_margin"] == pytest.approx(0.17)
    assert recommendation.produced_at == "2026-10-26T03:00:00Z"


def test_approve_proposal_recommendation_uses_confidence_and_rationale():
    approve = build_recommendation(
        "approve_proposal",
        {"proposal": {"problem_id": "p0", "confidence": 0.8, "rationale": "sound"}},
    )
    assert approve is not None
    assert approve.action == "approve"
    assert approve.confidence == 0.8
    assert approve.rationale == "sound"

    reject = build_recommendation(
        "approve_proposal",
        {"proposal": {"problem_id": "p0", "confidence": 0.2}},
    )
    assert reject is not None
    assert reject.action == "reject"


def test_review_evaluation_recommendation_records_abstention():
    grounded = build_recommendation(
        "review_evaluation",
        {
            "evaluation_result": {
                "verdict": "promising",
                "feasibility_score": 0.7,
                "provenance": {"abstained": False},
            }
        },
    )
    assert grounded is not None
    assert grounded.action == "approve"
    assert grounded.provenance_abstained is False

    abstained = build_recommendation(
        "review_evaluation",
        {
            "evaluation_result": {
                "verdict": "promising",
                "feasibility_score": 0.7,
                "provenance": {"abstained": True},
            }
        },
    )
    assert abstained is not None
    assert abstained.provenance_abstained is True
    assert "abstained" in abstained.rationale
    assert evaluation_provenance_allows_auto(
        {"evaluation_result": {"provenance": {"abstained": True}}}
    ) is False
    assert evaluation_provenance_allows_auto(
        {"evaluation_result": {"provenance": {"abstained": False}}}
    ) is True
    assert evaluation_provenance_allows_auto({"evaluation_result": {}}) is False


def test_review_evaluation_recommendation_keeps_limitation_text_out_of_the_line():
    """The full limitation text belongs under LIMITATIONS, not the header.

    It used to be appended to the one-line rationale and cut at 160 chars, so
    a long infrastructure error ended mid-sentence in the gate header.
    """
    limitation = (
        "Execution error: RetryError: Timeout of 120.0s exceeded, last "
        "exception: HTTPSConnectionPool(host='private.googleapis.com', "
        "port=443): Max retries exceeded with url: /upload/storage/v1/b/"
        "vt-gcp-00042-agentic-kg-experiments"
    )
    recommendation = build_recommendation(
        "review_evaluation",
        {
            "evaluation_result": {
                "verdict": "inconclusive",
                "feasibility_score": 0.3,
                "limitations": [limitation],
            }
        },
    )

    assert recommendation is not None
    assert recommendation.rationale == "evaluation verdict inconclusive"
    assert "Max retries" not in recommendation.rationale


def test_edit_counts_as_agreement_with_an_approval_recommendation():
    rec = AgentRecommendation(action="approve")
    assert recommendation_agrees(rec, "edit") is True
    assert recommendation_agrees(rec, "reject") is False
    assert recommendation_agrees(None, "approve") is None


def test_report_counts_only_comparable_human_decisions():
    agree = _decision(action="approve", human="approve")
    disagree = _decision(action="approve", human="reject", note="wrong")
    auto = GateDecision(
        gate="select_problem",
        decision="auto_approve",
        decided_by="policy:auto",
        decided_at="2026-10-26T05:00:00Z",
        agent_recommendation=AgentRecommendation(action="approve"),
    )
    policy = parse_policy(
        {
            "version": "1",
            "gates": {
                "select_problem": {
                    "mode": "human",
                    "promotion": {
                        "min_decisions": 2,
                        "min_agreement_lower_bound": 0.09,
                    },
                },
                "approve_proposal": {"mode": "human"},
                "review_evaluation": {"mode": "human"},
            },
        }
    )

    report = build_agreement_report(
        [_cycle(agree, disagree, auto)], policy=policy
    )
    stats = report.gate("select_problem")
    assert stats is not None
    assert stats.decisions == 2  # the auto decision has no human to agree with
    assert stats.agreed == 1
    assert stats.agreement_rate == 0.5
    assert stats.lower_bound == pytest.approx(wilson_lower_bound(1, 2))
    assert stats.promotion_met is True
    assert len(stats.last_disagreements) == 1
    assert stats.last_disagreements[0].reason == "wrong"


def test_report_since_filter_and_unknown_gate():
    older = _decision(decided_at="2026-10-01T00:00:00Z")
    newer = _decision(decided_at="2026-10-26T00:00:00Z")
    report = build_agreement_report(
        [_cycle(older, newer)], since="2026-10-20T00:00:00Z"
    )
    stats = report.gate("select_problem")
    assert stats is not None
    assert stats.decisions == 1

    with pytest.raises(ValueError, match="unknown gate"):
        build_agreement_report([_cycle()], gate="not_a_gate")


def test_promotion_criteria_fail_closed_and_explicit_zero_is_met():
    strict = PromotionCriteria(min_decisions=5, min_agreement_lower_bound=0.9)
    assert criteria_met(strict, 4, 1.0) is False
    assert criteria_met(strict, 5, 0.5) is False
    assert criteria_met(strict, 5, 0.95) is True
    assert criteria_met(None, 100, 1.0) is False
    zero = PromotionCriteria(min_decisions=0, min_agreement_lower_bound=0.0)
    assert criteria_met(zero, 0, None) is True
