"""E-8 Unit 4 — TopicExtractor.

Covers AC-2: per-instance taxonomy snapshot, closed-set Literal schema,
parallel-to-ProblemExtractor shape, degrade on LLMError, snapshot isolation.
"""

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from agentic_kg.extraction.llm_client import LLMError, LLMResponse, TokenUsage
from agentic_kg.extraction.schemas import _ExtractedTopicAssignmentBase
from agentic_kg.extraction.topic_extractor import TopicExtractor

# =============================================================================
# Fixtures
# =============================================================================


SAMPLE_TAXONOMY = """
- name: Computer Science
  level: domain
  children:
    - name: NLP
      level: area
      children:
        - name: Machine Translation
          level: subtopic
    - name: Computer Vision
      level: area
"""


@pytest.fixture
def taxonomy_file(tmp_path) -> Path:
    p = tmp_path / "tax.yml"
    p.write_text(SAMPLE_TAXONOMY)
    return p


@pytest.fixture
def mock_client() -> MagicMock:
    client = MagicMock()
    client.extract = AsyncMock()
    return client


def _make_envelope_response(extractor: TopicExtractor, topics: list[dict]) -> LLMResponse:
    """Build an LLMResponse carrying the extractor's envelope model populated."""
    envelope = extractor.envelope_model(topics=topics)
    return LLMResponse(content=envelope, usage=TokenUsage(total_tokens=100))


# =============================================================================
# Construction
# =============================================================================


class TestTopicExtractorInit:
    def test_loads_taxonomy_and_builds_models(self, taxonomy_file, mock_client):
        ex = TopicExtractor(client=mock_client, taxonomy_path=taxonomy_file)

        assert "NLP" in ex.taxonomy_names
        assert "Computer Vision" in ex.taxonomy_names
        assert "Machine Translation" in ex.taxonomy_names

        # The dynamic model exposes a topic_name field bound to the snapshot.
        assert "topic_name" in ex.assignment_model.model_fields
        # The envelope wraps the assignment list.
        assert "topics" in ex.envelope_model.model_fields

    def test_rejects_topic_name_outside_taxonomy(self, taxonomy_file, mock_client):
        ex = TopicExtractor(client=mock_client, taxonomy_path=taxonomy_file)
        # Inside the closed set — accepted.
        ex.assignment_model(topic_name="NLP", level="area", confidence=0.9)
        # Outside the closed set — rejected by Pydantic Literal.
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ex.assignment_model(
                topic_name="Quantum Computing", level="area", confidence=0.9
            )


class TestTopicExtractorEmptyTaxonomy:
    """Pathological input: taxonomy parses but yields no names.

    parse_taxonomy already rejects truly empty input, but a structure
    that walks into nothing (e.g. handcrafted via internal helpers) would
    otherwise crash with the cryptic ``Literal[()]`` TypeError at model
    construction. The guard fails fast with a usable message.
    """

    def test_empty_taxonomy_raises_value_error(self, tmp_path, mock_client, monkeypatch):
        path = tmp_path / "shallow.yml"
        path.write_text("- name: CS\n  level: domain\n")

        # Patch flatten_taxonomy at the call site to simulate an empty walk
        # (real parse_taxonomy would refuse the input upstream).
        import agentic_kg.extraction.topic_extractor as mod

        monkeypatch.setattr(mod, "flatten_taxonomy", lambda parsed: {})

        with pytest.raises(ValueError, match="zero topic names"):
            TopicExtractor(client=mock_client, taxonomy_path=path)


class TestTopicExtractorSnapshotIsolation:
    """AC-2 final clause: two instances against different taxonomies have
    independent accepted-name sets."""

    def test_two_instances_independent(self, tmp_path, mock_client):
        tax_a = tmp_path / "a.yml"
        tax_a.write_text(SAMPLE_TAXONOMY)

        tax_b = tmp_path / "b.yml"
        tax_b.write_text(
            "- name: CS\n  level: domain\n  children:\n"
            "    - name: Robotics\n      level: area\n"
        )

        ex_a = TopicExtractor(client=mock_client, taxonomy_path=tax_a)
        ex_b = TopicExtractor(client=mock_client, taxonomy_path=tax_b)

        assert "NLP" in ex_a.taxonomy_names
        assert "NLP" not in ex_b.taxonomy_names
        assert "Robotics" in ex_b.taxonomy_names
        assert "Robotics" not in ex_a.taxonomy_names

    def test_mid_file_mutation_does_not_affect_existing_instance(
        self, tmp_path, mock_client
    ):
        """Edge case: taxonomy file mutated after instantiation. The first
        instance keeps its original snapshot; a second instance picks up
        the change.
        """
        path = tmp_path / "live.yml"
        path.write_text(SAMPLE_TAXONOMY)
        ex_first = TopicExtractor(client=mock_client, taxonomy_path=path)

        # Mutate on disk — add an area.
        path.write_text(
            SAMPLE_TAXONOMY
            + "    - name: Reinforcement Learning\n      level: area\n"
        )

        # First instance is unchanged.
        assert "Reinforcement Learning" not in ex_first.taxonomy_names

        # Second instance picks up the change.
        ex_second = TopicExtractor(client=mock_client, taxonomy_path=path)
        assert "Reinforcement Learning" in ex_second.taxonomy_names


# =============================================================================
# Extraction behavior
# =============================================================================


class TestTopicExtractorExtract:
    @pytest.mark.asyncio
    async def test_extract_returns_topics_above_threshold(
        self, taxonomy_file, mock_client
    ):
        ex = TopicExtractor(
            client=mock_client, taxonomy_path=taxonomy_file, min_confidence=0.7
        )
        mock_client.extract.return_value = _make_envelope_response(
            ex,
            topics=[
                {"topic_name": "NLP", "level": "area", "confidence": 0.95},
                {"topic_name": "Computer Vision", "level": "area", "confidence": 0.6},
            ],
        )
        out = await ex.extract(paper_title="A paper", sections_text="abstract...")
        # The 0.6 confidence is below threshold and filtered.
        assert len(out) == 1
        assert out[0].topic_name == "NLP"

    @pytest.mark.asyncio
    async def test_extract_empty_input_skips_llm_call(
        self, taxonomy_file, mock_client
    ):
        ex = TopicExtractor(client=mock_client, taxonomy_path=taxonomy_file)
        out = await ex.extract(paper_title="A", sections_text="   ")
        assert out == []
        mock_client.extract.assert_not_called()

    @pytest.mark.asyncio
    async def test_extract_llm_error_returns_empty_and_warns(
        self, taxonomy_file, mock_client, caplog
    ):
        ex = TopicExtractor(client=mock_client, taxonomy_path=taxonomy_file)
        mock_client.extract.side_effect = LLMError("upstream 500")

        with caplog.at_level(logging.WARNING):
            out = await ex.extract(paper_title="A", sections_text="text")
        assert out == []
        # A WARN is emitted (the orchestrator's _run will NOT see this as a
        # failure, but the operator can audit logs).
        assert any("Topic extraction failed" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_extract_returns_assignment_subtype(
        self, taxonomy_file, mock_client
    ):
        """The returned items must be Pydantic instances carrying topic_name,
        level, and confidence — the integration layer reads those attributes
        directly.
        """
        ex = TopicExtractor(client=mock_client, taxonomy_path=taxonomy_file)
        mock_client.extract.return_value = _make_envelope_response(
            ex,
            topics=[{"topic_name": "NLP", "level": "area", "confidence": 0.9}],
        )
        out = await ex.extract(paper_title="A", sections_text="abstract")
        assert isinstance(out[0], _ExtractedTopicAssignmentBase)
        assert out[0].topic_name == "NLP"
        assert out[0].level == "area"
        assert out[0].confidence == pytest.approx(0.9)

    @pytest.mark.asyncio
    async def test_extract_passes_prompt_to_client(
        self, taxonomy_file, mock_client
    ):
        ex = TopicExtractor(client=mock_client, taxonomy_path=taxonomy_file)
        mock_client.extract.return_value = _make_envelope_response(ex, topics=[])
        await ex.extract(paper_title="My paper", sections_text="abstract body")

        call_kwargs = mock_client.extract.call_args.kwargs
        assert "My paper" in call_kwargs["prompt"]
        assert "abstract body" in call_kwargs["prompt"]
        # The system prompt must contain the closed-set taxonomy.
        assert "NLP" in call_kwargs["system_prompt"]
        # The response model passed in is the per-instance envelope.
        assert call_kwargs["response_model"] is ex.envelope_model

    @pytest.mark.asyncio
    async def test_extract_filters_all_below_threshold(
        self, taxonomy_file, mock_client
    ):
        ex = TopicExtractor(
            client=mock_client, taxonomy_path=taxonomy_file, min_confidence=0.9
        )
        mock_client.extract.return_value = _make_envelope_response(
            ex,
            topics=[
                {"topic_name": "NLP", "level": "area", "confidence": 0.8},
                {"topic_name": "Computer Vision", "level": "area", "confidence": 0.85},
            ],
        )
        out = await ex.extract(paper_title="A", sections_text="text")
        assert out == []
