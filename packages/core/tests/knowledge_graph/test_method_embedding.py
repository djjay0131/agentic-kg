"""Unit tests for ``generate_method_embedding`` (E-4, Unit 3).

Pure-Python test that mocks the EmbeddingService — no OpenAI key needed.
Mirrors E-3's ``test_model_embedding.py``.
"""

from unittest.mock import MagicMock, patch


class TestGenerateMethodEmbedding:
    def test_includes_description_in_embedded_text(self):
        with patch("agentic_kg.knowledge_graph.embeddings.EmbeddingService") as mock_cls:
            svc = MagicMock()
            svc.generate_embedding.return_value = [0.0] * 1536
            mock_cls.return_value = svc

            from agentic_kg.knowledge_graph.embeddings import (
                generate_method_embedding,
            )

            result = generate_method_embedding(
                "contrastive learning",
                description="self-supervised pretraining via positive/negative pair contrast",
            )
            assert result == [0.0] * 1536
            svc.generate_embedding.assert_called_once_with(
                "contrastive learning: self-supervised pretraining via "
                "positive/negative pair contrast"
            )

    def test_falls_back_to_name_when_no_description(self):
        with patch("agentic_kg.knowledge_graph.embeddings.EmbeddingService") as mock_cls:
            svc = MagicMock()
            svc.generate_embedding.return_value = [0.0] * 1536
            mock_cls.return_value = svc

            from agentic_kg.knowledge_graph.embeddings import (
                generate_method_embedding,
            )

            generate_method_embedding("fine-tuning")
            svc.generate_embedding.assert_called_once_with("fine-tuning")
