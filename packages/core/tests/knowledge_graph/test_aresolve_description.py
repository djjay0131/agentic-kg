"""Direct unit tests for ``Neo4jRepository._aresolve_description``.

The async siblings delegate to this helper. The three short-circuit
branches must be covered without needing Neo4j (Docker-skipped
integration tests cover them via the full async path, but the verify
gate wants line coverage even when Docker is unavailable).

Branches:
  * explicit description present → return it (no LLM call)
  * generate_description=False    → return description unchanged
  * llm_client is None            → WARN + return description unchanged
"""

import logging

import pytest
from agentic_kg.knowledge_graph.repository import Neo4jRepository


def _make_repo() -> Neo4jRepository:
    """Bare repo. _aresolve_description never touches the DB so this is safe."""
    return Neo4jRepository.__new__(Neo4jRepository)


class TestAresolveDescriptionShortCircuits:
    @pytest.mark.asyncio
    async def test_explicit_description_wins(self):
        repo = _make_repo()
        result = await repo._aresolve_description(
            entity_type="method",
            name="anything",
            description="A real explicit description here.",
            aliases=[],
            generate_description=True,
            llm_client=object(),  # would be used if we reached the LLM call
        )
        assert result == "A real explicit description here."

    @pytest.mark.asyncio
    async def test_generate_false_returns_input_description(self):
        """generate_description=False short-circuits, regardless of llm_client."""
        repo = _make_repo()
        result = await repo._aresolve_description(
            entity_type="method",
            name="x",
            description=None,
            aliases=[],
            generate_description=False,
            llm_client=object(),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_no_llm_client_warns_and_returns_input(self, caplog):
        """generate_description=True but llm_client=None → WARN + skip."""
        repo = _make_repo()
        with caplog.at_level(logging.WARNING):
            result = await repo._aresolve_description(
                entity_type="model",
                name="some-model",
                description=None,
                aliases=["alias1"],
                generate_description=True,
                llm_client=None,
            )
        assert result is None
        assert any(
            "no llm_client provided" in r.message and "some-model" in r.message
            for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_empty_string_description_treated_as_missing(self):
        """description="" is treated the same as None and falls through to
        the generate-description short-circuit (since generate_description=False)."""
        repo = _make_repo()
        result = await repo._aresolve_description(
            entity_type="concept",
            name="x",
            description="",
            aliases=[],
            generate_description=False,
            llm_client=None,
        )
        assert result == ""
