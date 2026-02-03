"""
E2E test configuration for API tests.

Re-exports core E2E fixtures and adds API-specific ones.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx
import pytest


@dataclass
class APITestConfig:
    """Configuration for API E2E tests."""

    api_url: str
    neo4j_uri: str
    neo4j_password: str
    neo4j_user: str = "neo4j"

    @classmethod
    def from_env(cls) -> "APITestConfig":
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

        if not neo4j_password:
            pytest.skip("STAGING_NEO4J_PASSWORD not set")

        return cls(
            api_url=api_url,
            neo4j_uri=neo4j_uri,
            neo4j_password=neo4j_password,
        )


@pytest.fixture(scope="session")
def api_config() -> APITestConfig:
    """Provide API test configuration."""
    return APITestConfig.from_env()


@pytest.fixture(scope="session")
def api_client(api_config: APITestConfig) -> httpx.Client:
    """Create HTTP client for API tests."""
    with httpx.Client(base_url=api_config.api_url, timeout=30.0) as client:
        yield client


@pytest.fixture(scope="session")
def async_api_client(api_config: APITestConfig):
    """Create async HTTP client for API tests."""
    return httpx.AsyncClient(base_url=api_config.api_url, timeout=30.0)
