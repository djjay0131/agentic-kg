"""
E2E test configuration and fixtures.

Configure via environment variables:
    STAGING_API_URL=<Cloud Run URL, e.g. the `api_url` terraform output>
    OPENAI_API_KEY=<for LLM extraction tests>

The database-backed tests additionally require a reachable Neo4j (ADR-0006
makes staging Neo4j VPC-private, so these run only in-VPC or via an IAP
tunnel):
    STAGING_NEO4J_URI=<bolt URI, the `neo4j_bolt_uri` terraform output>
    STAGING_NEO4J_PASSWORD=<the `neo4j_password` terraform output>
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from neo4j import Driver


# Run-unique namespace for every node an e2e test writes (#78). It keeps the
# TEST_ marker cleanup relies on while guaranteeing two concurrent runs
# against the same staging database never delete each other's rows.
E2E_NAMESPACE = f"TEST_{uuid.uuid4().hex[:8]}"


@dataclass
class E2EConfig:
    """Configuration for E2E tests."""

    api_url: str
    # ADR-0006: Neo4j is VPC-private. API- and live-source-only e2e tests run
    # without database credentials; the DB-backed tests (marker
    # ``requires_db``) are deselected in the GitHub-hosted job and assert on
    # these fields via the ``neo4j_driver`` fixture, which fails loudly when
    # they are absent rather than skipping to a green run.
    neo4j_uri: str = ""
    neo4j_password: str = ""
    neo4j_user: str = "neo4j"
    openai_api_key: str | None = None

    @classmethod
    def from_env(cls) -> "E2EConfig":
        """Load config from environment variables."""
        api_url = os.environ.get("STAGING_API_URL")
        neo4j_uri = os.environ.get("STAGING_NEO4J_URI", "")
        neo4j_password = os.environ.get("STAGING_NEO4J_PASSWORD", "")
        openai_api_key = os.environ.get("OPENAI_API_KEY")

        # No baked-in defaults: an unset/rotated staging endpoint must skip,
        # not silently point the suite at a stale address. Get the real values
        # from the Terraform outputs (`neo4j_bolt_uri`, `api_url`) or CI secrets.
        if not api_url:
            pytest.skip("STAGING_API_URL not set")

        return cls(
            api_url=api_url,
            neo4j_uri=neo4j_uri,
            neo4j_password=neo4j_password,
            openai_api_key=openai_api_key,
        )


@pytest.fixture(scope="session")
def e2e_config() -> E2EConfig:
    """Provide E2E configuration from environment.

    Declares the staging target to the ownership guard in
    ``packages/core/tests/conftest.py``. This is the one suite whose purpose is
    to exercise a real deployed environment, and it runs only under explicit
    commands (``make test-e2e``, the ``E2E Tests (Staging)`` job) -- never from
    ``make test`` or the README's "unit tests" line, both of which reach it only
    after ``E2EConfig.from_env()`` has already skipped for want of
    STAGING_NEO4J_PASSWORD.

    The declaration is deliberate, in code, and reached only when an e2e test
    actually asks for this config. It is not an environment switch: nothing a
    developer exports can produce one.

    NOTE (issue #78 item 5): the cleanup this suite runs is still a run-global
    prefix sweep, so two concurrent e2e runs can still delete each other's rows
    -- the same data race #75 removed from the integration suite. Declaring the
    target here does not fix that; it is tracked separately.
    """
    config = E2EConfig.from_env()

    from ..conftest import declare_session_database

    declare_session_database(
        config.neo4j_uri,
        reason="e2e suite deliberately targets the deployed staging environment",
    )
    return config


@pytest.fixture(scope="session")
def neo4j_driver(e2e_config: E2EConfig) -> "Driver":
    """Create Neo4j driver for the DB-backed (``requires_db``) E2E tests.

    Fails loudly when credentials are absent instead of skipping: these tests
    are only meaningful where the private database is reachable, and a silent
    skip is how a job reports green while asserting nothing (issue #80).
    """
    from neo4j import GraphDatabase

    if not e2e_config.neo4j_uri or not e2e_config.neo4j_password:
        pytest.fail(
            "Neo4j credentials are not set. DB-backed e2e tests (marker "
            "`requires_db`) run only in an environment that can reach the "
            "VPC-private database (ADR-0006): in-VPC, or locally via an IAP "
            "tunnel. Use `-m 'not requires_db'` to deselect them."
        )

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
