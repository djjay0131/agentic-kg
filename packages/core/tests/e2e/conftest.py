"""
E2E test configuration and fixtures.

Configure via environment variables:
    STAGING_API_URL=https://agentic-kg-api-staging-tqpsba7pza-uc.a.run.app
    STAGING_NEO4J_URI=bolt://34.173.74.125:7687
    STAGING_NEO4J_PASSWORD=<from terraform output>
    OPENAI_API_KEY=<for LLM extraction tests>
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from neo4j import Driver


@dataclass
class E2EConfig:
    """Configuration for E2E tests."""

    api_url: str
    neo4j_uri: str
    neo4j_password: str
    neo4j_user: str = "neo4j"
    openai_api_key: str | None = None

    @classmethod
    def from_env(cls) -> "E2EConfig":
        """Load config from environment variables."""
        api_url = os.environ.get(
            "STAGING_API_URL",
            "https://agentic-kg-api-staging-tqpsba7pza-uc.a.run.app",
        )
        neo4j_uri = os.environ.get(
            "STAGING_NEO4J_URI",
            "bolt://34.173.74.125:7687",
        )
        neo4j_password = os.environ.get("STAGING_NEO4J_PASSWORD", "")
        openai_api_key = os.environ.get("OPENAI_API_KEY")

        if not neo4j_password:
            pytest.skip("STAGING_NEO4J_PASSWORD not set")

        return cls(
            api_url=api_url,
            neo4j_uri=neo4j_uri,
            neo4j_password=neo4j_password,
            openai_api_key=openai_api_key,
        )


@pytest.fixture(scope="session")
def e2e_config() -> E2EConfig:
    """Provide E2E configuration from environment."""
    return E2EConfig.from_env()


@pytest.fixture(scope="session")
def neo4j_driver(e2e_config: E2EConfig) -> "Driver":
    """Create Neo4j driver for E2E tests."""
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        e2e_config.neo4j_uri,
        auth=(e2e_config.neo4j_user, e2e_config.neo4j_password),
    )
    # Verify connection
    driver.verify_connectivity()
    yield driver
    driver.close()


@pytest.fixture(scope="function")
def neo4j_session(neo4j_driver: "Driver"):
    """Provide a Neo4j session for each test."""
    with neo4j_driver.session() as session:
        yield session


@pytest.fixture(scope="session")
def api_client(e2e_config: E2EConfig):
    """Create HTTP client for API tests."""
    import httpx

    with httpx.Client(base_url=e2e_config.api_url, timeout=30.0) as client:
        yield client


@pytest.fixture(scope="session")
def async_api_client(e2e_config: E2EConfig):
    """Create async HTTP client for API tests."""
    import httpx

    return httpx.AsyncClient(base_url=e2e_config.api_url, timeout=30.0)


# Test data constants
TEST_PAPER_IDS = {
    # Attention Is All You Need (Transformer paper)
    "semantic_scholar": "204e3073870fae3d05bcbc2f6a8e263d9b72e776",
    # Same paper on arXiv
    "arxiv": "1706.03762",
}

TEST_DOMAIN = "natural language processing"
