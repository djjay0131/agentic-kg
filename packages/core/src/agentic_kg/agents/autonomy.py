"""Autonomy policy for the research-progression loop.

Every gate in the loop is a decision point where an agent wants to act:

* ``select_problem`` -- which ranked problem the workflow investigates,
* ``approve_proposal`` -- whether the continuation proposal is run, and
* ``review_evaluation`` -- whether a result may be written back.

The policy is a **versioned file**, not an environment variable, so what the
system was allowed to do can be recovered from the version recorded on each
cycle. This PR ships the file with every gate set to ``human``; a test pins
that default.

Modes
-----
``human``
    Always stop and wait for a person (the shipped default).
``auto_if``
    Act without a person only when the gate's confidence is at least the
    configured threshold; otherwise stop for a person.
``auto``
    Act without a person. This is deliberately *not* enabled for any gate in
    this PR, and the ADR records the evidence (adviser/human agreement from the
    P2 review queue) required before the owner may enable it.

A policy fails closed: an unknown gate or mode, a missing threshold, or a
threshold outside ``[0, 1]`` raises rather than silently becoming ``auto``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from agentic_kg.agents.schemas import CheckpointType

AutonomyMode = Literal["human", "auto_if", "auto"]

#: The config path inside the Job image / repo.
DEFAULT_POLICY_PATH = "config/research-autonomy.yaml"

#: Environment override for a different versioned file.
ENV_POLICY_PATH = "RESEARCH_AUTONOMY_POLICY"

_GATE_NAMES = tuple(gate.value for gate in CheckpointType)


class GatePolicy(BaseModel):
    """The rule for one gate."""

    mode: AutonomyMode = "human"
    confidence_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _threshold_matches_mode(self) -> "GatePolicy":
        if self.mode == "auto_if" and self.confidence_threshold is None:
            raise ValueError("auto_if requires a confidence_threshold")
        if self.mode != "auto_if" and self.confidence_threshold is not None:
            raise ValueError("confidence_threshold is only valid for auto_if")
        return self

    def resolve(self, confidence: Optional[float]) -> bool:
        """Whether the gate may proceed without a human.

        A missing confidence is treated as zero: an unattributable score never
        satisfies an ``auto_if`` threshold.
        """
        if self.mode == "auto":
            return True
        if self.mode == "human":
            return False
        return (confidence or 0.0) >= (self.confidence_threshold or 0.0)


class AutonomyPolicy(BaseModel):
    """A versioned set of per-gate rules."""

    version: str
    gates: dict[str, GatePolicy]

    @field_validator("gates")
    @classmethod
    def _all_gates_present(cls, value: dict[str, GatePolicy]) -> dict[str, GatePolicy]:
        missing = [name for name in _GATE_NAMES if name not in value]
        if missing:
            raise ValueError(f"policy is missing gates: {', '.join(missing)}")
        extra = [name for name in value if name not in _GATE_NAMES]
        if extra:
            raise ValueError(f"policy names unknown gates: {', '.join(extra)}")
        return value

    def gate(self, gate: str) -> GatePolicy:
        try:
            return self.gates[gate]
        except KeyError as exc:
            raise KeyError(f"unknown gate: {gate!r}") from exc

    def is_human(self, gate: str) -> bool:
        return self.gate(gate).mode == "human"

    def all_human(self) -> bool:
        return all(policy.mode == "human" for policy in self.gates.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "gates": {
                name: policy.model_dump(mode="json")
                for name, policy in self.gates.items()
            },
        }


def parse_policy(raw: dict[str, Any]) -> AutonomyPolicy:
    """Parse a policy mapping, raising on anything malformed."""
    return AutonomyPolicy.model_validate(raw)


def load_policy(path: Optional[str | Path] = None) -> AutonomyPolicy:
    """Load the autonomy policy from ``path`` or the configured default.

    ``RESEARCH_AUTONOMY_POLICY`` overrides the default path. A relative path is
    resolved against the process working directory first, then the repository
    root (the Job image copies ``config/`` to ``/app/config``).
    """
    chosen = path or os.getenv(ENV_POLICY_PATH) or DEFAULT_POLICY_PATH
    candidate = Path(chosen)
    if not candidate.is_absolute():
        if candidate.is_file():
            candidate = candidate.resolve()
        else:
            repo_root = Path(__file__).resolve().parents[5]
            candidate = repo_root / candidate
    if not candidate.is_file():
        raise FileNotFoundError(f"autonomy policy not found: {candidate}")
    data = yaml.safe_load(candidate.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"autonomy policy {candidate} is not a mapping")
    return parse_policy(data)


__all__ = [
    "AutonomyMode",
    "AutonomyPolicy",
    "DEFAULT_POLICY_PATH",
    "ENV_POLICY_PATH",
    "GatePolicy",
    "load_policy",
    "parse_policy",
]
