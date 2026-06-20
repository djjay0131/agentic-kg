"""E-7 Unit 5 — detect_ambiguous_pairs composer + _build_paper_excerpt.

Tests the cheap-then-fuzzy ordering, AC-3 cost guard at the composer
level, and the excerpt-builder's truncation contract.
"""

from unittest.mock import MagicMock

from agentic_kg.extraction.cross_entity_normalizer import (
    MAX_EXCERPT_CHARS,
    _build_paper_excerpt,
    detect_ambiguous_pairs,
)
from agentic_kg.extraction.pipeline import PaperExtractionResult
from agentic_kg.extraction.schemas import (
    ExtractedMethod,
    ExtractedModel,
    ExtractedResearchConcept,
)


def _concept(name: str, quoted_text: str = "grounding text for concept here"):
    return ExtractedResearchConcept(
        name=name, quoted_text=quoted_text, confidence=0.9,
    )


def _model(name: str, quoted_text: str = "grounding text for model here"):
    return ExtractedModel(
        name=name, quoted_text=quoted_text, confidence=0.9,
    )


def _method(name: str, quoted_text: str = "grounding text for method here"):
    return ExtractedMethod(
        name=name, quoted_text=quoted_text, confidence=0.9,
    )


# =============================================================================
# detect_ambiguous_pairs — composer ordering + cost guard
# =============================================================================


class TestDetectAmbiguousPairsOrdering:
    def test_cheap_pair_skips_embedding_call(self):
        """A cheap-trigger pair must mark its extractions as 'already
        paired' so the embedding step never embeds them."""
        embedder = MagicMock()
        c = _concept("attention")
        m = _method("attention")  # exact-name collision
        pairs = detect_ambiguous_pairs(
            [c], [], [m], embedder=embedder,
        )
        assert len(pairs) == 1
        assert pairs[0].trigger == "exact"
        embedder.generate_embedding.assert_not_called()

    def test_no_cheap_collision_runs_embedding(self):
        """When there's no cheap pair, the embedding scan IS called."""
        embedder = MagicMock()
        embedder.generate_embedding.side_effect = [[1.0], [1.0]]
        c = _concept("X-thing")
        m = _method("Y-thing")
        pairs = detect_ambiguous_pairs(
            [c], [], [m], embedder=embedder, similarity_threshold=0.85,
        )
        assert len(pairs) == 1
        assert pairs[0].trigger == "embedding"

    def test_mixed_cheap_and_embedding_pairs_combine(self):
        """Two independent ambiguous pairs in the same paper: one
        cheap, one fuzzy. Both come back."""
        embedder = MagicMock()
        embedder.generate_embedding.side_effect = [[1.0], [1.0]]
        c1 = _concept("attention")
        m1 = _method("attention")            # cheap collision
        c2 = _concept("ProcessA-thing")
        m2 = _model("ProcessB-thing")        # embedding collision

        pairs = detect_ambiguous_pairs(
            [c1, c2], [m2], [m1], embedder=embedder,
        )
        triggers = {p.trigger for p in pairs}
        assert triggers == {"exact", "embedding"}
        assert len(pairs) == 2

    def test_empty_input_no_pairs_no_embedder_call(self):
        embedder = MagicMock()
        pairs = detect_ambiguous_pairs([], [], [], embedder=embedder)
        assert pairs == []
        embedder.generate_embedding.assert_not_called()


# =============================================================================
# _build_paper_excerpt — concatenation + truncation
# =============================================================================


class TestBuildPaperExcerpt:
    def test_concatenates_all_kinds(self):
        result = PaperExtractionResult(
            concepts=[_concept("X-thing", quoted_text="from-concept text here")],
            models=[_model("Y-thing", quoted_text="from-model text here")],
            methods=[_method("Z-thing", quoted_text="from-method text here")],
        )
        excerpt = _build_paper_excerpt(result, max_chars=4000)
        assert "from-concept text here" in excerpt
        assert "from-model text here" in excerpt
        assert "from-method text here" in excerpt

    def test_truncates_at_max(self):
        long_quote = "x" * 100  # 100-char snippet
        many = [_concept(f"item-{i}", quoted_text=long_quote) for i in range(50)]
        result = PaperExtractionResult(
            concepts=many, models=[], methods=[],
        )
        excerpt = _build_paper_excerpt(result, max_chars=200)
        assert len(excerpt) == 200

    def test_empty_extraction_result_empty_excerpt(self):
        result = PaperExtractionResult(concepts=[], models=[], methods=[])
        excerpt = _build_paper_excerpt(result, max_chars=4000)
        assert excerpt == ""

    def test_uses_max_excerpt_chars_default_constant(self):
        # Sanity check that the default constant matches the spec.
        assert MAX_EXCERPT_CHARS == 4000

    def test_tolerates_old_result_without_attrs(self):
        """getattr(..., default) defends against pinned/old results that
        don't carry models/methods fields."""
        bare = MagicMock(spec=["concepts"])
        bare.concepts = [_concept("XX", quoted_text="some quote here for ok")]
        # No .models / .methods attrs.
        excerpt = _build_paper_excerpt(bare, max_chars=4000)
        assert "some quote here for ok" in excerpt
