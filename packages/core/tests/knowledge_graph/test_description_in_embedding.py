"""AC-9: the LLM-generated description must be incorporated into the
embedding text (so future dedup compares 'name: description' rather than
just 'name').

We mock the embedding generator and the LLM helper, then assert the
generator was called with text that contains the generated description.
This is a unit-level test — no Neo4j needed, because we patch the sync
``create_or_merge_X`` to intercept the embedding-generation hop.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

GENERATED = "A specific multi-sentence description of the entity."


@pytest.fixture
def patched_helper(monkeypatch):
    """Patch the description-generation helper to return GENERATED."""
    from agentic_kg.knowledge_graph import description_generation as dg
    monkeypatch.setattr(
        dg, "generate_description_with_self_check",
        AsyncMock(return_value=GENERATED),
    )


@pytest.fixture
def repo_with_stubbed_sync():
    """Build a bare Neo4jRepository with the sync method replaced by
    a capture spy. Verifies what flows from the async sibling into the
    sync method (and therefore into the embedding text)."""
    from agentic_kg.knowledge_graph.repository import Neo4jRepository

    repo = Neo4jRepository.__new__(Neo4jRepository)
    return repo


class TestDescriptionFlowsIntoEmbedding:
    @pytest.mark.asyncio
    async def test_method_description_flows_to_sync(
        self, repo_with_stubbed_sync, patched_helper,
    ):
        """The async sibling must hand the LLM-generated description
        down to the sync method, where embedding text is composed."""
        captured = {}

        def _spy_sync(**kwargs):
            captured.update(kwargs)
            return (MagicMock(id="m-1"), True)

        repo_with_stubbed_sync.create_or_merge_method = _spy_sync

        await repo_with_stubbed_sync.acreate_or_merge_method(
            name="contrastive learning",
            description=None,
            generate_description=True,
            llm_client=MagicMock(),
        )

        # The sync method received the LLM-generated description, which
        # is the same string that downstream embedding text uses.
        assert captured["description"] == GENERATED

    @pytest.mark.asyncio
    async def test_model_description_flows_to_sync(
        self, repo_with_stubbed_sync, patched_helper,
    ):
        captured = {}

        def _spy_sync(**kwargs):
            captured.update(kwargs)
            return (MagicMock(id="m-1"), True)

        repo_with_stubbed_sync.create_or_merge_model = _spy_sync

        await repo_with_stubbed_sync.acreate_or_merge_model(
            name="BERT",
            description=None,
            generate_description=True,
            llm_client=MagicMock(),
        )
        assert captured["description"] == GENERATED

    @pytest.mark.asyncio
    async def test_concept_description_flows_to_sync(
        self, repo_with_stubbed_sync, patched_helper,
    ):
        captured = {}

        def _spy_sync(**kwargs):
            captured.update(kwargs)
            return (MagicMock(id="c-1"), True)

        repo_with_stubbed_sync.create_or_merge_research_concept = _spy_sync

        await repo_with_stubbed_sync.acreate_or_merge_research_concept(
            name="attention mechanism",
            description=None,
            generate_description=True,
            llm_client=MagicMock(),
        )
        assert captured["description"] == GENERATED


class TestEmbeddingTextContainsDescription:
    """AC-9 (concrete): the embedding helper's text input contains both
    the name and the generated description.

    Tests the existing ``generate_method_embedding`` shape — when
    description is non-None, it appears in the embedded text.
    """

    @patch("agentic_kg.knowledge_graph.embeddings.EmbeddingService")
    def test_method_embedding_text_includes_description(self, service_cls):
        from agentic_kg.knowledge_graph.embeddings import (
            generate_method_embedding,
        )

        instance = service_cls.return_value
        instance.generate_embedding.return_value = [0.0] * 1536

        generate_method_embedding(name="fine-tuning", description=GENERATED)

        text_arg = instance.generate_embedding.call_args.args[0]
        assert "fine-tuning" in text_arg
        assert GENERATED in text_arg

    @patch("agentic_kg.knowledge_graph.embeddings.EmbeddingService")
    def test_model_embedding_text_includes_description(self, service_cls):
        from agentic_kg.knowledge_graph.embeddings import (
            generate_model_embedding,
        )
        instance = service_cls.return_value
        instance.generate_embedding.return_value = [0.0] * 1536

        generate_model_embedding(name="BERT", description=GENERATED)

        text_arg = instance.generate_embedding.call_args.args[0]
        assert "BERT" in text_arg
        assert GENERATED in text_arg

    @patch("agentic_kg.knowledge_graph.embeddings.EmbeddingService")
    def test_concept_embedding_text_includes_description(self, service_cls):
        from agentic_kg.knowledge_graph.embeddings import (
            generate_research_concept_embedding,
        )
        instance = service_cls.return_value
        instance.generate_embedding.return_value = [0.0] * 1536

        generate_research_concept_embedding(
            name="attention", description=GENERATED,
        )

        text_arg = instance.generate_embedding.call_args.args[0]
        assert "attention" in text_arg
        assert GENERATED in text_arg
