"""E-7 Unit 1 — DisambiguationDecision + audit dataclasses.

Covers AC-1 (schema bounds + self-validation property).
"""

import pytest
from agentic_kg.extraction.cross_entity_normalizer import (
    MAX_EXCERPT_CHARS,
    MIN_DISAMBIGUATION_CONFIDENCE,
    SIMILARITY_THRESHOLD,
    AmbiguousPair,
    DisambiguationDecision,
    NormalizationAuditEntry,
    NormalizationResult,
)
from pydantic import ValidationError


class TestConstants:
    def test_similarity_threshold(self):
        assert SIMILARITY_THRESHOLD == 0.85

    def test_min_confidence(self):
        assert MIN_DISAMBIGUATION_CONFIDENCE == 0.7

    def test_max_excerpt_chars(self):
        assert MAX_EXCERPT_CHARS == 4000


class TestDisambiguationDecisionShape:
    def test_minimal_passing(self):
        d = DisambiguationDecision(
            picked_kind="concept",
            confidence=0.9,
            is_grounded_in_paper_context=True,
            is_specific_to_one_kind=True,
        )
        assert d.picked_kind == "concept"
        assert d.passes_self_validation is True
        assert d.rejection_reason is None

    def test_rejects_invalid_picked_kind(self):
        # AC-1 + AC-20: Literal["concept","model","method"] is the
        # injection-resistant guard on the response shape.
        with pytest.raises(ValidationError):
            DisambiguationDecision(
                picked_kind="topic",  # not in the literal
                confidence=0.9,
                is_grounded_in_paper_context=True,
                is_specific_to_one_kind=True,
            )

    def test_confidence_above_one_rejected(self):
        with pytest.raises(ValidationError):
            DisambiguationDecision(
                picked_kind="model",
                confidence=1.5,
                is_grounded_in_paper_context=True,
                is_specific_to_one_kind=True,
            )

    def test_confidence_below_zero_rejected(self):
        with pytest.raises(ValidationError):
            DisambiguationDecision(
                picked_kind="model",
                confidence=-0.1,
                is_grounded_in_paper_context=True,
                is_specific_to_one_kind=True,
            )


class TestPassesSelfValidation:
    @pytest.mark.parametrize(
        "false_gate", ["is_grounded_in_paper_context", "is_specific_to_one_kind"],
    )
    def test_either_gate_false_fails_validation(self, false_gate):
        gates = {
            "is_grounded_in_paper_context": True,
            "is_specific_to_one_kind": True,
        }
        gates[false_gate] = False
        d = DisambiguationDecision(
            picked_kind="method",
            confidence=0.9,
            rejection_reason=f"{false_gate} False",
            **gates,
        )
        assert d.passes_self_validation is False

    def test_both_false_fails(self):
        d = DisambiguationDecision(
            picked_kind="method",
            confidence=0.9,
            is_grounded_in_paper_context=False,
            is_specific_to_one_kind=False,
            rejection_reason="both gates failed",
        )
        assert d.passes_self_validation is False


class TestAmbiguousPair:
    def test_constructable(self):
        pair = AmbiguousPair(
            surface="attention",
            extractions={"concept": object(), "method": object()},
            trigger="exact",
        )
        assert pair.surface == "attention"
        assert set(pair.extractions) == {"concept", "method"}
        assert pair.trigger == "exact"


class TestNormalizationAuditEntry:
    def test_accepted_pair(self):
        entry = NormalizationAuditEntry(
            surface="attention",
            trigger="exact",
            picked="concept",
            dropped_kinds=["method"],
        )
        assert entry.picked == "concept"
        assert entry.dropped_kinds == ["method"]
        assert entry.rejection_reason is None

    def test_rejected_pair_keeps_dropped_kinds_empty(self):
        entry = NormalizationAuditEntry(
            surface="attention",
            trigger="exact",
            picked=None,
            dropped_kinds=[],
            rejection_reason="insufficient context",
        )
        assert entry.picked is None
        assert entry.dropped_kinds == []
        assert entry.rejection_reason == "insufficient context"


class TestNormalizationResult:
    def test_is_clean_when_no_pairs(self):
        r = NormalizationResult()
        assert r.is_clean is True
        assert r.pairs_detected == 0

    def test_is_clean_false_when_pair_detected(self):
        r = NormalizationResult(pairs_detected=1)
        assert r.is_clean is False
