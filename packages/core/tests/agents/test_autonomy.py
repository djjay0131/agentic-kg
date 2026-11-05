"""The autonomy policy: shipped default is human, and the parser fails closed."""

from __future__ import annotations

import pytest
from agentic_kg.agents.autonomy import (
    DEFAULT_POLICY_PATH,
    GatePolicy,
    load_policy,
    parse_policy,
)
from agentic_kg.agents.schemas import CheckpointType


def _raw(**gates):
    base = {
        "select_problem": {"mode": "human"},
        "approve_proposal": {"mode": "human"},
        "review_evaluation": {"mode": "human"},
    }
    base.update(gates)
    return {"version": "1", "gates": base}


def test_shipped_policy_is_all_human():
    policy = load_policy(DEFAULT_POLICY_PATH)
    assert policy.version == "1"
    assert policy.all_human()
    for gate in CheckpointType:
        assert policy.gate(gate.value).mode == "human"


def test_every_gate_must_be_present():
    raw = _raw()
    del raw["gates"]["review_evaluation"]
    with pytest.raises(ValueError, match="missing gates"):
        parse_policy(raw)


def test_unknown_gate_is_rejected():
    with pytest.raises(ValueError, match="unknown gates"):
        parse_policy(_raw(write_everything={"mode": "human"}))


def test_auto_if_requires_a_threshold():
    with pytest.raises(ValueError, match="confidence_threshold"):
        parse_policy(_raw(select_problem={"mode": "auto_if"}))


def test_threshold_is_only_valid_for_auto_if():
    with pytest.raises(ValueError, match="only valid for auto_if"):
        parse_policy(
            _raw(select_problem={"mode": "human", "confidence_threshold": 0.9})
        )


def test_threshold_is_bounded():
    with pytest.raises(ValueError):
        parse_policy(
            _raw(select_problem={"mode": "auto_if", "confidence_threshold": 1.5})
        )


def test_resolve_semantics():
    assert GatePolicy(mode="human").resolve(1.0) is False
    assert GatePolicy(mode="auto").resolve(None) is True
    auto_if = GatePolicy(mode="auto_if", confidence_threshold=0.8)
    assert auto_if.resolve(0.8) is True
    assert auto_if.resolve(0.79) is False
    # An absent confidence never satisfies a threshold.
    assert auto_if.resolve(None) is False
