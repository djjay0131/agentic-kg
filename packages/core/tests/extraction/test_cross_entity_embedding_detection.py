"""E-7 Unit 4 — _embedding_collisions.

Covers AC-3 (embedding-trigger detection + cost guard) and AC-13
(embedder failure degrades the fuzzy layer only).
"""

import logging
from unittest.mock import MagicMock

import pytest
from agentic_kg.extraction.cross_entity_normalizer import (
    _cosine,
    _embed_with_cache,
    _embedding_collisions,
)
from agentic_kg.extraction.schemas import (
    ExtractedMethod,
    ExtractedModel,
    ExtractedResearchConcept,
)


def _concept(name: str) -> ExtractedResearchConcept:
    return ExtractedResearchConcept(
        name=name,
        quoted_text="grounding text for the concept here",
        confidence=0.9,
    )


def _model(name: str) -> ExtractedModel:
    return ExtractedModel(
        name=name,
        quoted_text="grounding text for the model here",
        confidence=0.9,
    )


def _method(name: str) -> ExtractedMethod:
    return ExtractedMethod(
        name=name,
        quoted_text="grounding text for the method here",
        confidence=0.9,
    )


# =============================================================================
# Helpers
# =============================================================================


class TestCosine:
    def test_identical_vectors_return_one(self):
        v = [0.5, 0.5, 0.5]
        assert _cosine(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors_return_zero(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert _cosine(a, b) == pytest.approx(0.0)

    def test_opposite_vectors_return_negative_one(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert _cosine(a, b) == pytest.approx(-1.0)


class TestEmbedWithCache:
    def test_caches_repeat_calls(self):
        embedder = MagicMock()
        embedder.generate_embedding.return_value = [0.1] * 4
        cache: dict = {}

        v1 = _embed_with_cache("BERT", cache, embedder)
        v2 = _embed_with_cache("BERT", cache, embedder)

        assert v1 == [0.1] * 4
        assert v2 is v1  # cached object reused
        # Embedder was called once despite two requests.
        embedder.generate_embedding.assert_called_once()

    def test_case_insensitive_cache_key(self):
        embedder = MagicMock()
        embedder.generate_embedding.return_value = [0.2] * 4
        cache: dict = {}

        _embed_with_cache("BERT", cache, embedder)
        _embed_with_cache("bert", cache, embedder)
        # Second call hits the cache via the case-insensitive key.
        embedder.generate_embedding.assert_called_once()

    def test_embedder_exception_returns_none_and_warns(self, caplog):
        embedder = MagicMock()
        embedder.generate_embedding.side_effect = RuntimeError("openai down")
        cache: dict = {}

        with caplog.at_level(logging.WARNING):
            v = _embed_with_cache("name", cache, embedder)

        assert v is None
        assert any(
            "Embedding failed" in r.message for r in caplog.records
        )
        # Cache stays empty so future retries are possible.
        assert cache == {}


# =============================================================================
# AC-3 — happy path + cost guard
# =============================================================================


class TestEmbeddingCollisionsHappyPath:
    def test_above_threshold_emits_pair(self):
        embedder = MagicMock()
        embedder.generate_embedding.side_effect = [
            [1.0, 0.0, 0.0],  # concept "self-attention"
            [0.95, 0.31, 0.0],  # method "scaled dot-product attention"
        ]
        c = _concept("self-attention")
        m = _method("scaled dot-product attention")
        pairs = _embedding_collisions(
            [c], [], [m],
            already_paired_ids=set(),
            embedder=embedder,
            threshold=0.85,
        )
        assert len(pairs) == 1
        assert pairs[0].trigger == "embedding"
        assert pairs[0].extractions["concept"] is c
        assert pairs[0].extractions["method"] is m

    def test_below_threshold_skipped(self):
        embedder = MagicMock()
        embedder.generate_embedding.side_effect = [
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
        ]
        c = _concept("attention")
        m = _method("unrelated")
        pairs = _embedding_collisions(
            [c], [], [m],
            already_paired_ids=set(),
            embedder=embedder,
            threshold=0.85,
        )
        assert pairs == []

    def test_already_paired_extractions_not_embedded(self):
        """AC-3 cost guard: extractions covered by a cheap-trigger pair
        are excluded from the embedding scan entirely (no embedder
        calls for them)."""
        embedder = MagicMock()
        c = _concept("attention")
        m = _method("attention")
        # Both are already_paired (their ids are in the set).
        pairs = _embedding_collisions(
            [c], [], [m],
            already_paired_ids={id(c), id(m)},
            embedder=embedder,
            threshold=0.85,
        )
        assert pairs == []
        embedder.generate_embedding.assert_not_called()


class TestEmbeddingCollisionsScansAllAxes:
    def test_concept_vs_model_axis(self):
        embedder = MagicMock()
        embedder.generate_embedding.side_effect = [[1.0], [1.0]]
        c = _concept("X-thing")
        m = _model("Y-thing")
        pairs = _embedding_collisions(
            [c], [m], [],
            already_paired_ids=set(),
            embedder=embedder,
            threshold=0.85,
        )
        assert len(pairs) == 1
        assert "concept" in pairs[0].extractions
        assert "model" in pairs[0].extractions

    def test_model_vs_method_axis(self):
        embedder = MagicMock()
        embedder.generate_embedding.side_effect = [[1.0], [1.0]]
        m = _model("X-thing")
        meth = _method("Y-thing")
        pairs = _embedding_collisions(
            [], [m], [meth],
            already_paired_ids=set(),
            embedder=embedder,
            threshold=0.85,
        )
        assert len(pairs) == 1
        assert "model" in pairs[0].extractions
        assert "method" in pairs[0].extractions


# =============================================================================
# AC-13 — embedder failure degrades the fuzzy layer; does not propagate
# =============================================================================


class TestEmbedderFailure:
    def test_embedder_raises_returns_empty_no_exception(self):
        embedder = MagicMock()
        embedder.generate_embedding.side_effect = RuntimeError("S2 down")
        c = _concept("attention")
        m = _method("attention method")

        pairs = _embedding_collisions(
            [c], [], [m],
            already_paired_ids=set(),
            embedder=embedder,
            threshold=0.85,
        )
        assert pairs == []  # AC-13: no pairs emitted

    def test_partial_embedder_failure_continues(self):
        """If embedding one extraction fails, the scan should still
        attempt to handle the others. Here the first call (concept)
        succeeds but the second (method) raises; the pair is silently
        dropped."""
        embedder = MagicMock()
        embedder.generate_embedding.side_effect = [
            [1.0, 0.0],
            RuntimeError("transient"),
        ]
        c = _concept("attention")
        m = _method("attention thing")
        pairs = _embedding_collisions(
            [c], [], [m],
            already_paired_ids=set(),
            embedder=embedder,
            threshold=0.85,
        )
        assert pairs == []
