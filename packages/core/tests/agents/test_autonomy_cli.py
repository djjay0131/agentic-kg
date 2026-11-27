"""The read-only ``agentic-kg autonomy status`` command (ADR-0013)."""

from __future__ import annotations

import json

import pytest
from agentic_kg import cli
from agentic_kg.agents.autonomy import DEFAULT_POLICY_PATH
from agentic_kg.knowledge_graph.models.research_cycle import (
    AgentRecommendation,
    CycleWorkflow,
    GateDecision,
    ResearchCycle,
)


def _cycle() -> ResearchCycle:
    return ResearchCycle(
        cycle_id="cycle-1",
        namespace="canon",
        started_at="2026-10-26T03:00:00Z",
        status="awaiting_gate",
        workflow_runs=[
            CycleWorkflow(
                workflow_run_id="wf-1",
                problem_id="p0",
                state="awaiting_gate",
                pending_gate="select_problem",
                gate_decisions=[
                    GateDecision(
                        gate="select_problem",
                        decision="approve",
                        decided_by="alice",
                        decided_at="2026-10-26T04:00:00Z",
                        cycle_id="cycle-1",
                        workflow_run_id="wf-1",
                        agent_recommendation=AgentRecommendation(action="approve"),
                        human_decision="approve",
                        agreed=True,
                    )
                ],
            )
        ],
    )


class FakeCycleRepository:
    def __init__(self, *args, **kwargs):
        pass

    def list(self, limit=1000):
        return [_cycle()]


def test_autonomy_status_parser_accepts_a_subcommand():
    args = cli.build_parser().parse_args(["autonomy", "status", "--json"])
    assert args.command == "autonomy"
    assert args.autonomy_command == "status"
    assert args.json_output is True


def test_autonomy_status_prints_json_evidence(monkeypatch, capsys):
    from agentic_kg.knowledge_graph import repository as repository_module
    from agentic_kg.knowledge_graph import research_cycles as cycles_module

    monkeypatch.setattr(
        repository_module, "get_repository", lambda: object()
    )
    monkeypatch.setattr(
        cycles_module, "ResearchCycleRepository", FakeCycleRepository
    )
    args = cli.build_parser().parse_args(
        [
            "autonomy",
            "status",
            "--json",
            "--policy",
            DEFAULT_POLICY_PATH,
        ]
    )
    cli.run_autonomy(args)
    payload = json.loads(capsys.readouterr().out)
    gates = {gate["gate"]: gate for gate in payload["gates"]}
    assert gates["select_problem"]["decisions"] == 1
    assert gates["select_problem"]["agreed"] == 1
    assert gates["select_problem"]["mode"] == "human"
    assert gates["select_problem"]["promotion_met"] is False


def test_autonomy_status_rejects_an_unknown_gate(monkeypatch, capsys):
    args = cli.build_parser().parse_args(
        ["autonomy", "status", "--gate", "nope"]
    )
    with pytest.raises(SystemExit) as exc:
        cli.run_autonomy(args)
    assert exc.value.code == 2
    assert "unknown gate" in capsys.readouterr().err
