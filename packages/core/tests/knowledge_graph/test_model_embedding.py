"""Unit tests for ``generate_model_embedding`` (E-3, Unit 3 supplement).

Pure-Python test that mocks the EmbeddingService — no OpenAI key needed.
Closes the verify-gate coverage gap on the embedding helper.
"""

from unittest.mock import MagicMock, patch


class TestGenerateModelEmbedding:
    def test_includes_description_in_embedded_text(self):
        with patch("agentic_kg.knowledge_graph.embeddings.EmbeddingService") as mock_cls:
            svc = MagicMock()
            svc.generate_embedding.return_value = [0.0] * 1536
            mock_cls.return_value = svc

            from agentic_kg.knowledge_graph.embeddings import (
                generate_model_embedding,
            )

            result = generate_model_embedding(
                "BERT", description="A transformer language model"
            )
            assert result == [0.0] * 1536
            svc.generate_embedding.assert_called_once_with(
                "BERT: A transformer language model"
            )

    def test_falls_back_to_name_when_no_description(self):
        with patch("agentic_kg.knowledge_graph.embeddings.EmbeddingService") as mock_cls:
            svc = MagicMock()
            svc.generate_embedding.return_value = [0.0] * 1536
            mock_cls.return_value = svc

            from agentic_kg.knowledge_graph.embeddings import (
                generate_model_embedding,
            )

            generate_model_embedding("ResNet")
            svc.generate_embedding.assert_called_once_with("ResNet")
