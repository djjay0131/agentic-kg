"""
Embedding generation for semantic search.

Integrates with OpenAI embeddings to generate vector representations
of research problems for semantic similarity search.
"""

import logging
import time
from typing import Optional

from agentic_kg.config import EmbeddingConfig, get_config
from agentic_kg.knowledge_graph.models import Problem

logger = logging.getLogger(__name__)


class EmbeddingError(Exception):
    """Raised when embedding generation fails."""

    pass


class EmbeddingService:
    """
    Service for generating text embeddings using OpenAI.

    Handles API calls, batching, and retry logic.
    """

    def __init__(self, config: Optional[EmbeddingConfig] = None):
        """
        Initialize embedding service.

        Args:
            config: Embedding configuration. Uses global config if not provided.
        """
        self._config = config or get_config().embedding
        self._client = None

    @property
    def client(self):
        """Lazy-load the OpenAI client."""
        if self._client is None:
            try:
                from openai import OpenAI
                self._client = OpenAI(api_key=self._config.api_key)
            except ImportError:
                raise EmbeddingError(
                    "openai package not installed. Run: pip install openai"
                )
        return self._client

    def generate_embedding(self, text: str) -> list[float]:
        """
        Generate embedding for a single text.

        Args:
            text: Text to embed.

        Returns:
            Embedding vector (1536 dimensions for text-embedding-3-small).

        Raises:
            EmbeddingError: If generation fails.
        """
        if not self._config.is_configured:
            raise EmbeddingError("OpenAI API key not configured")

        if not text or not text.strip():
            raise EmbeddingError("Cannot embed empty text")

        for attempt in range(self._config.max_retries):
            try:
                response = self.client.embeddings.create(
                    model=self._config.model,
                    input=text,
                )
                embedding = response.data[0].embedding
                logger.debug(
                    f"Generated embedding ({len(embedding)} dims) for text: {text[:50]}..."
                )
                return embedding

            except Exception as e:
                if attempt < self._config.max_retries - 1:
                    delay = self._config.retry_delay * (2**attempt)
                    logger.warning(
                        f"Embedding attempt {attempt + 1} failed, retrying in {delay}s: {e}"
                    )
                    time.sleep(delay)
                else:
                    raise EmbeddingError(f"Failed to generate embedding: {e}") from e

        return []  # Should not reach here

    def generate_embeddings_batch(
        self, texts: list[str]
    ) -> list[Optional[list[float]]]:
        """
        Generate embeddings for multiple texts.

        Args:
            texts: List of texts to embed.

        Returns:
            List of embeddings (None for failed texts).
        """
        if not self._config.is_configured:
            raise EmbeddingError("OpenAI API key not configured")

        results: list[Optional[list[float]]] = []
        batch_size = self._config.batch_size

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            batch_results = self._process_batch(batch)
            results.extend(batch_results)

        return results

    def _process_batch(self, texts: list[str]) -> list[Optional[list[float]]]:
        """Process a single batch of texts."""
        # Filter empty texts
        valid_indices = [i for i, t in enumerate(texts) if t and t.strip()]
        valid_texts = [texts[i] for i in valid_indices]

        if not valid_texts:
            return [None] * len(texts)

        for attempt in range(self._config.max_retries):
            try:
                response = self.client.embeddings.create(
                    model=self._config.model,
                    input=valid_texts,
                )

                # Map back to original positions
                results: list[Optional[list[float]]] = [None] * len(texts)
                for idx, embedding_data in enumerate(response.data):
                    original_idx = valid_indices[idx]
                    results[original_idx] = embedding_data.embedding

                logger.info(f"Generated {len(valid_texts)} embeddings in batch")
                return results

            except Exception as e:
                if attempt < self._config.max_retries - 1:
                    delay = self._config.retry_delay * (2**attempt)
                    logger.warning(
                        f"Batch embedding attempt {attempt + 1} failed, retrying in {delay}s: {e}"
                    )
                    time.sleep(delay)
                else:
                    logger.error(f"Batch embedding failed: {e}")
                    return [None] * len(texts)

        return [None] * len(texts)


def generate_problem_embedding(problem: Problem) -> list[float]:
    """
    Generate embedding for a Problem's statement.

    Combines the problem statement with domain context for better
    semantic representation.

    Args:
        problem: Problem to embed.

    Returns:
        Embedding vector.

    Raises:
        EmbeddingError: If generation fails.
    """
    # Construct embedding text with context
    parts = [problem.statement]

    if problem.domain:
        parts.insert(0, f"[Domain: {problem.domain}]")

    # Add key assumptions for context
    if problem.assumptions:
        assumption_texts = [a.text for a in problem.assumptions[:3]]
        parts.append(f"Assumptions: {'; '.join(assumption_texts)}")

    text = " ".join(parts)

    service = EmbeddingService()
    return service.generate_embedding(text)


def generate_problem_embeddings_batch(
    problems: list[Problem],
) -> list[Optional[list[float]]]:
    """
    Generate embeddings for multiple problems.

    Args:
        problems: Problems to embed.

    Returns:
        List of embeddings (None for failed problems).
    """
    texts = []
    for problem in problems:
        parts = [problem.statement]
        if problem.domain:
            parts.insert(0, f"[Domain: {problem.domain}]")
        if problem.assumptions:
            assumption_texts = [a.text for a in problem.assumptions[:3]]
            parts.append(f"Assumptions: {'; '.join(assumption_texts)}")
        texts.append(" ".join(parts))

    service = EmbeddingService()
    return service.generate_embeddings_batch(texts)


# Singleton service
_embedding_service: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    """Get the embedding service singleton."""
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService()
    return _embedding_service


def reset_embedding_service() -> None:
    """Reset the embedding service singleton."""
    global _embedding_service
    _embedding_service = None
