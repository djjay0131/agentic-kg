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
``auto_when_confident``
    The name ADR-0013 uses for the same rule: the agent decides only when its
    confidence is at least the threshold, otherwise the gate falls back to a
    human. Kept as a separate mode so a policy reads the way the ADR does; it
    resolves identically to ``auto_if``.
``auto``
    Act without a person. This is deliberately *not* enabled for any gate in
    this PR, and the ADR records the evidence (gate agreement from decided
    cycles) required before the owner may enable it.

Promotion criteria
------------------
Every gate may carry ``promotion`` (``min_decisions`` and
``min_agreement_lower_bound``). The system only *reports* whether the recorded
evidence meets them (``agentic-kg autonomy status`` / the Agreement panel).
Promotion itself is a human edit of this versioned file in a PR. At runtime a
non-``human`` gate is refused when its criteria are absent or unmet, so a policy
edit alone can never grant autonomy without evidence.

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

AutonomyMode = Literal["human", "auto_if", "auto_when_confident", "auto"]

#: The config path inside the Job image / repo.
DEFAULT_POLICY_PATH = "config/research-autonomy.yaml"

#: Environment override for a different versioned file.
ENV_POLICY_PATH = "RESEARCH_AUTONOMY_POLICY"

_GATE_NAMES = tuple(gate.value for gate in CheckpointType)


class PromotionCriteria(BaseModel):
    """The evidence a gate must accumulate before autonomy is even considered.

    These are criteria only. Meeting them makes the system *recommend* a
    promotion (``agentic-kg autonomy status``); it never edits the policy.
    """

    min_decisions: int = Field(default=1, ge=0)
    min_agreement_lower_bound: float = Field(default=0.0, ge=0.0, le=1.0)


class GatePolicy(BaseModel):
    """The rule for one gate."""

    mode: AutonomyMode = "human"
    confidence_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    promotion: Optional[PromotionCriteria] = None

    @model_validator(mode="after")
    def _threshold_matches_mode(self) -> "GatePolicy":
        confidence_modes = ("auto_if", "auto_when_confident")
        if self.mode in confidence_modes and self.confidence_threshold is None:
            raise ValueError(f"{self.mode} requires a confidence_threshold")
        if self.mode not in confidence_modes and self.confidence_threshold is not None:
            raise ValueError(
                "confidence_threshold is only valid for auto_if/"
                "auto_when_confident"
            )
        return self

    @property
    def needs_confidence(self) -> bool:
        """Whether this mode consults the gate's confidence."""
        return self.mode in ("auto_if", "auto_when_confident")

    def resolve(self, confidence: Optional[float]) -> bool:
        """Whether the gate may proceed without a human.

        A missing confidence is treated as zero: an unattributable score never
        satisfies a threshold. This only answers the *mode* question; the cycle
        additionally requires the promotion criteria to be met before it acts.
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
    "PromotionCriteria",
    "load_policy",
    "parse_policy",
]
