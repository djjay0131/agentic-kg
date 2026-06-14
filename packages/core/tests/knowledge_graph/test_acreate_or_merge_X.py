"""Async sibling acreate_or_merge_X tests (E-6 Unit 5, AC-6 and AC-7).

Tests against the testcontainers Neo4j. Mocks the LLM helper to avoid
real LLM calls but exercises the full async-sync delegation, real Cypher,
and real embeddings (via fake_embedding fixtures).
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.integration


def _test_name(label: str) -> str:
    return f"TEST_{label}_{uuid.uuid4().hex[:8]}"


def _fake_embedding(seed: float = 0.5) -> list[float]:
    return [seed] * 1536


@pytest.fixture
def mock_llm():
    """Mocked BaseLLMClient — we patch the helper, not the client."""
    c = MagicMock()
    c.extract = AsyncMock()
    return c


# =============================================================================
# AC-6 — happy path on each entity type
# =============================================================================


class TestAcreateOrMergeMethodHappy:
    @pytest.mark.asyncio
    async def test_generates_description_on_new_node(
        self, neo4j_repository, mock_llm, monkeypatch,
    ):
        from agentic_kg.knowledge_graph import description_generation as dg
        monkeypatch.setattr(
            dg, "generate_description_with_self_check",
            AsyncMock(return_value="A real generated description here.")
        )

        name = _test_name("fine-tuning")
        method, created = await neo4j_repository.acreate_or_merge_method(
            name=name,
            embedding=_fake_embedding(0.5),
            generate_description=True,
            llm_client=mock_llm,
        )

        assert created is True
        assert method.description == "A real generated description here."

    @pytest.mark.asyncio
    async def test_explicit_description_wins_over_generation(
        self, neo4j_repository, mock_llm, monkeypatch,
    ):
        """AC-6: explicit description wins. No LLM call."""
        from agentic_kg.knowledge_graph import description_generation as dg
        helper = AsyncMock(return_value="LLM-generated (should NOT be used)")
        monkeypatch.setattr(
            dg, "generate_description_with_self_check", helper,
        )

        name = _test_name("explicit")
        method, _ = await neo4j_repository.acreate_or_merge_method(
            name=name,
            description="Operator-supplied description value here.",
            embedding=_fake_embedding(0.51),
            generate_description=True,
            llm_client=mock_llm,
        )

        assert method.description == "Operator-supplied description value here."
        helper.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_llm_client_with_generate_true_warns_and_skips(
        self, neo4j_repository, monkeypatch, caplog,
    ):
        """AC-6: generate_description=True but llm_client=None → WARN + no LLM call."""
        import logging

        from agentic_kg.knowledge_graph import description_generation as dg
        helper = AsyncMock()
        monkeypatch.setattr(
            dg, "generate_description_with_self_check", helper,
        )

        name = _test_name("nollm")
        with caplog.at_level(logging.WARNING):
            method, _ = await neo4j_repository.acreate_or_merge_method(
                name=name,
                embedding=_fake_embedding(0.52),
                generate_description=True,
                llm_client=None,
            )

        assert method.description is None
        helper.assert_not_called()
        assert any(
            "no llm_client provided" in r.message for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_default_kwargs_skip_helper_entirely(
        self, neo4j_repository, monkeypatch,
    ):
        """AC-11: existing call sites that omit generate_description should
        see no behavior change."""
        from agentic_kg.knowledge_graph import description_generation as dg
        helper = AsyncMock()
        monkeypatch.setattr(
            dg, "generate_description_with_self_check", helper,
        )

        name = _test_name("default")
        method, created = await neo4j_repository.acreate_or_merge_method(
            name=name, embedding=_fake_embedding(0.53),
        )

        assert created is True
        assert method.description is None
        helper.assert_not_called()

    @pytest.mark.asyncio
    async def test_validation_rejection_persists_none(
        self, neo4j_repository, mock_llm, monkeypatch,
    ):
        """AC-10 smoke sentinel: rejection (helper returns None) → node persists
        without description, no crash."""
        from agentic_kg.knowledge_graph import description_generation as dg
        monkeypatch.setattr(
            dg, "generate_description_with_self_check",
            AsyncMock(return_value=None),
        )

        name = _test_name("rejected")
        method, _ = await neo4j_repository.acreate_or_merge_method(
            name=name,
            embedding=_fake_embedding(0.54),
            generate_description=True,
            llm_client=mock_llm,
        )

        assert method.description is None


# =============================================================================
# AC-7 — Merge path applies generated description to existing NULL nodes
# =============================================================================


class TestAcreateOrMergeMethodMergeAppliesGeneratedDescription:
    @pytest.mark.asyncio
    async def test_merge_fills_existing_null_description(
        self, neo4j_repository, mock_llm, monkeypatch,
    ):
        """AC-7: pre-existing Method with description=None gets the
        LLM-generated description on async re-create that dedup-merges."""
        from agentic_kg.knowledge_graph import description_generation as dg

        name = _test_name("mergedesc")
        emb = _fake_embedding(0.6)

        # Step 1: create node with description=None.
        method, created_a = neo4j_repository.create_or_merge_method(
            name=name, embedding=emb,
        )
        assert created_a is True
        assert method.description is None

        # Step 2: async create — dedup matches; LLM-generated description applies.
        monkeypatch.setattr(
            dg, "generate_description_with_self_check",
            AsyncMock(return_value="A fresh generated description here."),
        )
        merged, created_b = await neo4j_repository.acreate_or_merge_method(
            name=name,
            embedding=emb,
            generate_description=True,
            llm_client=mock_llm,
        )

        assert created_b is False
        assert merged.description == "A fresh generated description here."
        # Same node id.
        assert merged.id == method.id

    @pytest.mark.asyncio
    async def test_merge_does_not_overwrite_existing_description(
        self, neo4j_repository, mock_llm, monkeypatch,
    ):
        """Variant: if existing node already has a description, the merge
        path's existing 'best.description or description' semantics preserve
        it (the generated description loses).

        This is a known minor inefficiency: we generate a description
        eagerly, then the sync merge throws it away. Documented in the
        spec as accepted trade-off for sync/async simplicity.
        """
        from agentic_kg.knowledge_graph import description_generation as dg

        name = _test_name("existing")
        emb = _fake_embedding(0.61)

        # Pre-create with existing description.
        method, _ = neo4j_repository.create_or_merge_method(
            name=name, description="Existing description present.",
            embedding=emb,
        )
        assert method.description == "Existing description present."

        # Async merge with generation enabled.
        monkeypatch.setattr(
            dg, "generate_description_with_self_check",
            AsyncMock(return_value="Generated value (should be discarded)."),
        )
        merged, _ = await neo4j_repository.acreate_or_merge_method(
            name=name,
            embedding=emb,
            generate_description=True,
            llm_client=mock_llm,
        )

        # Existing description wins.
        assert merged.description == "Existing description present."


# =============================================================================
# Model + ResearchConcept smoke tests (same delegation pattern)
# =============================================================================


class TestAcreateOrMergeModelSmoke:
    @pytest.mark.asyncio
    async def test_generates_description_on_new_node(
        self, neo4j_repository, mock_llm, monkeypatch,
    ):
        from agentic_kg.knowledge_graph import description_generation as dg
        monkeypatch.setattr(
            dg, "generate_description_with_self_check",
            AsyncMock(return_value="A real description of the model."),
        )

        name = _test_name("modelgen")
        model, created = await neo4j_repository.acreate_or_merge_model(
            name=name,
            embedding=_fake_embedding(0.7),
            generate_description=True,
            llm_client=mock_llm,
        )
        assert created is True
        assert model.description == "A real description of the model."


class TestAcreateOrMergeResearchConceptSmoke:
    @pytest.mark.asyncio
    async def test_generates_description_on_new_node(
        self, neo4j_repository, mock_llm, monkeypatch,
    ):
        from agentic_kg.knowledge_graph import description_generation as dg
        monkeypatch.setattr(
            dg, "generate_description_with_self_check",
            AsyncMock(return_value="A real description of the concept."),
        )

        name = _test_name("conceptgen")
        concept, created = await neo4j_repository.acreate_or_merge_research_concept(
            name=name,
            embedding=_fake_embedding(0.8),
            generate_description=True,
            llm_client=mock_llm,
        )
        assert created is True
        assert concept.description == "A real description of the concept."
