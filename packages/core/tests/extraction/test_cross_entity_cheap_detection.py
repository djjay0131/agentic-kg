"""E-7 Unit 3 — _cheap_collisions (exact name + alias overlap).

Covers AC-2 (exact + alias triggers, case-insensitive) and AC-12 (same-kind
duplicates are NOT cross-entity collisions).
"""

from agentic_kg.extraction.cross_entity_normalizer import _cheap_collisions
from agentic_kg.extraction.schemas import (
    ExtractedMethod,
    ExtractedModel,
    ExtractedResearchConcept,
)


def _concept(name: str, aliases=None) -> ExtractedResearchConcept:
    return ExtractedResearchConcept(
        name=name,
        aliases=aliases or [],
        quoted_text="grounding text for the concept here",
        confidence=0.9,
    )


def _model(name: str, aliases=None) -> ExtractedModel:
    return ExtractedModel(
        name=name,
        aliases=aliases or [],
        quoted_text="grounding text for the model here",
        confidence=0.9,
    )


def _method(name: str, aliases=None) -> ExtractedMethod:
    return ExtractedMethod(
        name=name,
        aliases=aliases or [],
        quoted_text="grounding text for the method here",
        confidence=0.9,
    )


# =============================================================================
# AC-2 — exact + alias triggers
# =============================================================================


class TestExactNameCollision:
    def test_concept_vs_method_exact_name(self):
        c = _concept("attention mechanism")
        m = _method("attention mechanism")
        pairs = _cheap_collisions([c], [], [m])
        assert len(pairs) == 1
        assert pairs[0].surface == "attention mechanism"
        assert pairs[0].trigger == "exact"
        assert pairs[0].extractions["concept"] is c
        assert pairs[0].extractions["method"] is m

    def test_case_insensitive(self):
        c = _concept("Attention Mechanism")
        m = _method("attention mechanism")
        pairs = _cheap_collisions([c], [], [m])
        assert len(pairs) == 1
        assert pairs[0].trigger == "exact"

    def test_concept_vs_model_exact_name(self):
        c = _concept("BERT")
        m = _model("BERT")
        pairs = _cheap_collisions([c], [m], [])
        assert len(pairs) == 1
        assert pairs[0].extractions["concept"] is c
        assert pairs[0].extractions["model"] is m


class TestAliasCollision:
    def test_alias_vs_canonical(self):
        c = _concept("attention", aliases=[])
        m = _method("self-attention", aliases=["attention"])
        pairs = _cheap_collisions([c], [], [m])
        assert len(pairs) == 1
        # The concept's CANONICAL is "attention" which matches the
        # method's ALIAS "attention" — trigger is "exact" because the
        # method's *canonical* was not what collided, but the concept's
        # canonical WAS. The contract counts ANY canonical hit as exact.
        assert pairs[0].trigger == "exact"
        assert pairs[0].surface == "attention"

    def test_alias_vs_alias_pure(self):
        """Both extractions match via aliases only — canonicals differ
        and aliases share a surface. Trigger must be 'alias'."""
        c = _concept("multi-head attention", aliases=["attention head"])
        m = _method("scaled attention", aliases=["attention head"])
        pairs = _cheap_collisions([c], [], [m])
        assert len(pairs) == 1
        assert pairs[0].surface == "attention head"
        assert pairs[0].trigger == "alias"

    def test_empty_alias_ignored(self):
        c = _concept("attention", aliases=["", "ignore-me"])
        m = _method("attention", aliases=["", "different"])
        pairs = _cheap_collisions([c], [], [m])
        # Both canonicals are "attention" — exact collision wins.
        assert len(pairs) == 1
        assert pairs[0].trigger == "exact"


# =============================================================================
# AC-10 — triple collision
# =============================================================================


class TestTripleCollision:
    def test_three_kinds_same_name(self):
        c = _concept("attention")
        m = _model("attention")
        meth = _method("attention")
        pairs = _cheap_collisions([c], [m], [meth])
        assert len(pairs) == 1
        assert set(pairs[0].extractions.keys()) == {"concept", "model", "method"}
        assert pairs[0].trigger == "exact"


# =============================================================================
# AC-12 — in-kind same-name is NOT a cross-entity collision
# =============================================================================


class TestSameKindIgnored:
    def test_two_concepts_same_name_no_pair(self):
        c1 = _concept("attention")
        c2 = _concept("attention")
        pairs = _cheap_collisions([c1, c2], [], [])
        assert pairs == []

    def test_two_methods_same_alias_no_pair(self):
        m1 = _method("XX", aliases=["common"])
        m2 = _method("YY", aliases=["common"])
        pairs = _cheap_collisions([], [], [m1, m2])
        assert pairs == []


# =============================================================================
# Negative / boundary
# =============================================================================


class TestNoCollisions:
    def test_disjoint_extractions(self):
        pairs = _cheap_collisions(
            [_concept("attention")],
            [_model("BERT")],
            [_method("fine-tuning")],
        )
        assert pairs == []

    def test_all_empty(self):
        assert _cheap_collisions([], [], []) == []

    def test_same_pair_via_both_name_and_alias_deduped(self):
        """Concept canonical 'X' + Method canonical 'X' + Method alias
        'X' would naturally surface twice in the index (once via the
        method's name, once via the method's alias). The signature
        dedupe keeps it as one pair."""
        c = _concept("X-thing")
        m = _method("X-thing", aliases=["X-thing"])  # explicit duplicate
        pairs = _cheap_collisions([c], [], [m])
        assert len(pairs) == 1
        assert pairs[0].trigger == "exact"
