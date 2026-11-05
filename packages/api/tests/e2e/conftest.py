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

    ADR-0006: these tests run against the deployed API only. Neo4j is
    VPC-private, so the runner no longer has (and must not need) direct
    database credentials. The API's ``/health`` response is what proves the
    private Neo4j path works. ADR-0012 makes the API private: set
    ``STAGING_ID_TOKEN`` to a Cloud Run ID token for the service URL.
    """

    api_url: str
    id_token: str = ""
    neo4j_uri: str = ""
    neo4j_password: str = ""
    neo4j_user: str = "neo4j"

    @property
    def auth_headers(self) -> dict[str, str]:
        """Cloud Run ID token headers when the API is private (ADR-0012)."""
        return {"Authorization": f"Bearer {self.id_token}"} if self.id_token else {}

    @classmethod
    def from_env(cls) -> "APITestConfig":
        """Load config from environment variables."""
        api_url = os.environ.get("STAGING_API_URL")
        id_token = os.environ.get("STAGING_ID_TOKEN", "")

        # No baked-in defaults: an unset/rotated staging endpoint must skip,
        # not silently point the suite at a stale address. Get the real value
        # from the Terraform `api_url` output or the STAGING_API_URL secret.
        if not api_url:
            pytest.skip("STAGING_API_URL not set")

        return cls(api_url=api_url, id_token=id_token)


@pytest.fixture(scope="session")
def api_config() -> APITestConfig:
    """Provide API test configuration."""
    return APITestConfig.from_env()


@pytest.fixture(scope="session")
def api_client(api_config: APITestConfig) -> httpx.Client:
    """Create HTTP client for API tests."""
    with httpx.Client(
        base_url=api_config.api_url,
        timeout=30.0,
        headers=api_config.auth_headers,
    ) as client:
        yield client


@pytest.fixture(scope="session")
def async_api_client(api_config: APITestConfig):
    """Create async HTTP client for API tests."""
    return httpx.AsyncClient(
        base_url=api_config.api_url,
        timeout=30.0,
        headers=api_config.auth_headers,
    )
