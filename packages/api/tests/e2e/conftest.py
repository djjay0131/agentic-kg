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
    """Configuration for API E2E tests.

    ADR-0003: these tests run against the deployed, public API only. Neo4j
    is VPC-private, so the runner no longer has (and must not need) direct
    database credentials. The API's ``/health`` response is what proves the
    private Neo4j path works.
    """

    api_url: str
    neo4j_uri: str = ""
    neo4j_password: str = ""
    neo4j_user: str = "neo4j"

    @classmethod
    def from_env(cls) -> "APITestConfig":
        """Load config from environment variables."""
        api_url = os.environ.get("STAGING_API_URL")

        # No baked-in defaults: an unset/rotated staging endpoint must skip,
        # not silently point the suite at a stale address. Get the real value
        # from the Terraform `api_url` output or the STAGING_API_URL secret.
        if not api_url:
            pytest.skip("STAGING_API_URL not set")

        return cls(api_url=api_url)


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
