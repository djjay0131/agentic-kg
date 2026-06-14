"""Tests for the DescriptionWithSelfCheck schema (E-6, Unit 1).

Pure Pydantic validation — no LLM required.
"""

import pytest
from agentic_kg.knowledge_graph.description_generation import (
    DescriptionWithSelfCheck,
)
from pydantic import ValidationError


class TestDescriptionWithSelfCheckFields:
    def test_minimal_passing(self):
        m = DescriptionWithSelfCheck(
            description="A " + "x" * 18,  # 20 chars
            is_factually_grounded=True,
            is_concise=True,
            is_specific=True,
            is_not_tautological=True,
        )
        assert m.passes_self_validation is True
        assert m.rejection_reason is None

    def test_description_below_min_length_raises(self):
        with pytest.raises(ValidationError):
            DescriptionWithSelfCheck(
                description="too short",
                is_factually_grounded=True,
                is_concise=True,
                is_specific=True,
                is_not_tautological=True,
            )

    def test_description_above_max_length_raises(self):
        with pytest.raises(ValidationError):
            DescriptionWithSelfCheck(
                description="x" * 401,
                is_factually_grounded=True,
                is_concise=True,
                is_specific=True,
                is_not_tautological=True,
            )

    def test_description_at_min_length_accepted(self):
        m = DescriptionWithSelfCheck(
            description="x" * 20,
            is_factually_grounded=True,
            is_concise=True,
            is_specific=True,
            is_not_tautological=True,
        )
        assert len(m.description) == 20

    def test_description_at_max_length_accepted(self):
        m = DescriptionWithSelfCheck(
            description="x" * 400,
            is_factually_grounded=True,
            is_concise=True,
            is_specific=True,
            is_not_tautological=True,
        )
        assert len(m.description) == 400


class TestPassesSelfValidation:
    @pytest.mark.parametrize(
        "false_gate",
        [
            "is_factually_grounded",
            "is_concise",
            "is_specific",
            "is_not_tautological",
        ],
    )
    def test_any_single_false_gate_fails_validation(self, false_gate):
        """Every gate must be True; flipping any single one fails."""
        gates = {
            "is_factually_grounded": True,
            "is_concise": True,
            "is_specific": True,
            "is_not_tautological": True,
        }
        gates[false_gate] = False
        m = DescriptionWithSelfCheck(
            description="A reasonable description sentence here.",
            **gates,
            rejection_reason=f"{false_gate} was False",
        )
        assert m.passes_self_validation is False

    def test_all_true_passes(self):
        m = DescriptionWithSelfCheck(
            description="A reasonable description sentence here.",
            is_factually_grounded=True,
            is_concise=True,
            is_specific=True,
            is_not_tautological=True,
        )
        assert m.passes_self_validation is True

    def test_all_false_fails(self):
        m = DescriptionWithSelfCheck(
            description="A reasonable description sentence here.",
            is_factually_grounded=False,
            is_concise=False,
            is_specific=False,
            is_not_tautological=False,
            rejection_reason="all gates failed",
        )
        assert m.passes_self_validation is False

    def test_rejection_reason_optional(self):
        # rejection_reason can be None even when gates fail (the LLM didn't fill it).
        m = DescriptionWithSelfCheck(
            description="A reasonable description sentence here.",
            is_factually_grounded=False,
            is_concise=True,
            is_specific=True,
            is_not_tautological=True,
        )
        assert m.rejection_reason is None
        assert m.passes_self_validation is False
