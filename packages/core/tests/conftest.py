"""
Shared pytest fixtures for agentic-kg tests.

Provides common test data and fixtures used across test modules.
"""

from datetime import datetime, timezone
from typing import Generator
import uuid

import pytest

# =============================================================================
# Neo4j Integration Test Fixtures
# =============================================================================
#
# These fixtures support two modes:
# 1. Environment variables (CI/staging): Set NEO4J_URI, NEO4J_PASSWORD
# 2. Testcontainers (local dev): Spins up a Neo4j container via Docker
#


import os


def _get_neo4j_from_env():
    """Check if Neo4j connection details are in environment."""
    uri = os.environ.get("NEO4J_URI")
    password = os.environ.get("NEO4J_PASSWORD")
    return uri, password


@pytest.fixture(scope="session")
def neo4j_container():
    """
    Start a Neo4j container for integration tests.

    This fixture is session-scoped to avoid starting a new container
    for each test. Tests should clean up their own data.

    If NEO4J_URI is set in environment, this fixture is skipped
    (we use the external Neo4j instance instead).
    """
    # Check if we should use environment variables instead
    uri, password = _get_neo4j_from_env()
    if uri and password:
        # Return None - we'll use env vars in neo4j_config
        yield None
        return

    # Otherwise, try to use testcontainers
    try:
        from testcontainers.neo4j import Neo4jContainer
    except ImportError:
        pytest.skip("testcontainers not installed and NEO4J_URI not set")
        return

    # Check if Docker is available
    try:
        import docker
        client = docker.from_env()
        client.ping()
    except Exception:
        pytest.skip("Docker not available and NEO4J_URI not set")
        return

    container = Neo4jContainer("neo4j:5.26-community", password="testpassword")
    container.with_env("NEO4J_PLUGINS", '["apoc"]')

    try:
        container.start()
        yield container
    finally:
        container.stop()


@pytest.fixture
def neo4j_config(neo4j_container, monkeypatch):
    """
    Configure Neo4j connection for tests.

    Uses environment variables if set, otherwise uses testcontainer.
    Returns the Neo4jConfig for direct use.
    """
    from agentic_kg.config import Neo4jConfig, reset_config

    # Check for environment variables first
    env_uri, env_password = _get_neo4j_from_env()

    if env_uri and env_password:
        # Use environment variables (CI mode)
        uri = env_uri
        username = os.environ.get("NEO4J_USERNAME", "neo4j")
        password = env_password
        database = os.environ.get("NEO4J_DATABASE", "neo4j")
    elif neo4j_container is not None:
        # Use testcontainer (local dev mode)
        uri = neo4j_container.get_connection_url()
        username = "neo4j"
        password = "testpassword"  # Must match Neo4jContainer(password=...)
        database = "neo4j"
    else:
        pytest.skip("No Neo4j connection available")
        return

    # Set environment variables for the test
    monkeypatch.setenv("NEO4J_URI", uri)
    monkeypatch.setenv("NEO4J_USERNAME", username)
    monkeypatch.setenv("NEO4J_PASSWORD", password)
    monkeypatch.setenv("NEO4J_DATABASE", database)

    # Reset config to pick up new env vars
    reset_config()

    config = Neo4jConfig(
        uri=uri,
        username=username,
        password=password,
        database=database,
    )

    yield config


class SharedDatabaseSweepError(RuntimeError):
    """
    Raised when a global test-data sweep is attempted against a database that
    this pytest session does not exclusively own.

    The sweep below matches test data by *prefix*, and the prefix is global
    rather than per-run. Two CI runs pointed at the same Neo4j therefore share
    one namespace: each run's setup/teardown sweep deletes the other run's
    in-flight rows, producing "NotFoundError: <uuid> not found" failures on
    freshly created nodes. That is a data race, not flakiness, and the fix is
    to give every run its own database -- not to make the sweep cleverer.
    """


# Identifying properties that mark a node as test data. Kept in one place so
# the predicate used to count and the predicate used to delete cannot drift.
#
# NOTE: '10.1/TEST-' is included deliberately. Tests in test_method_repository
# and test_model_repository mint Paper DOIs as f"10.1/TEST-{uuid4().hex[:6]}",
# which the original predicate ('10.TEST_' only) could never match. Those
# Papers accumulated in the shared staging database indefinitely, and with only
# 6 hex characters of entropy the birthday collision eventually surfaced as
# "DuplicateError: Paper with DOI 10.1/TEST-c854bd already exists".
TEST_DATA_PREDICATE = """
        n.id STARTS WITH 'TEST_'
           OR n.doi STARTS WITH '10.TEST_'
           OR n.doi STARTS WITH '10.1/TEST-'
           OR n.name STARTS WITH 'TEST_'
           OR n.domain STARTS WITH 'TEST_'
           OR n.statement STARTS WITH 'TEST_'
"""

TEST_DATA_COUNT_QUERY = f"MATCH (n) WHERE {TEST_DATA_PREDICATE} RETURN count(n) AS n"
TEST_DATA_SWEEP_QUERY = f"MATCH (n) WHERE {TEST_DATA_PREDICATE} DETACH DELETE n"


def neo4j_is_ephemeral(container=None) -> bool:
    """
    True when this pytest session exclusively owns the Neo4j it is talking to.

    Two ways to earn that:
      * the session started its own throwaway container (testcontainers), or
      * the operator asserted it by setting AKG_NEO4J_EPHEMERAL=1 (used by the
        Integration Tests workflow, which now provisions a per-run container).

    Anything else -- notably a NEO4J_URI pointing at shared staging -- is NOT
    ephemeral, and the global sweep is refused there.
    """
    if container is not None:
        return True
    return os.environ.get("AKG_NEO4J_EPHEMERAL", "").strip().lower() in {"1", "true", "yes"}


def sweep_test_data(repo, *, ephemeral: bool) -> int:
    """
    Delete every node matching the test-data predicate.

    Refuses to run unless the caller owns the database exclusively. This is the
    guard that makes concurrent CI runs safe: a run that does not own its
    database cannot issue a cross-run destructive sweep, full stop.

    Returns the number of nodes deleted.
    """
    if not ephemeral:
        raise SharedDatabaseSweepError(
            "Refusing to run a global TEST_-prefix sweep against a database this "
            "session does not own. The prefix is global, so this sweep would "
            "delete test data belonging to any other run sharing this instance. "
            "Run integration tests against a per-run Neo4j (testcontainers, the "
            "default when NEO4J_URI is unset) or set AKG_NEO4J_EPHEMERAL=1 only "
            "if the database really is exclusive to this run."
        )

    with repo.session() as session:
        record = session.run(TEST_DATA_COUNT_QUERY).single()
        doomed = record["n"] if record else 0
        session.run(TEST_DATA_SWEEP_QUERY)
    return doomed


@pytest.fixture(scope="session")
def neo4j_exclusive(neo4j_container) -> bool:
    """Whether this session exclusively owns the Neo4j under test."""
    return neo4j_is_ephemeral(neo4j_container)


@pytest.fixture
def neo4j_repository(neo4j_config, neo4j_exclusive):
    """
    Create a repository connected to Neo4j.

    Initializes schema and sweeps this session's test data on teardown.

    Requires an exclusively-owned database (see ``neo4j_is_ephemeral``). The
    sweep is teardown-only: a per-run database starts empty, so the old
    pre-test sweep bought nothing and doubled the window in which a concurrent
    run's rows could be destroyed.
    """
    from agentic_kg.knowledge_graph.repository import Neo4jRepository
    from agentic_kg.knowledge_graph.schema import SchemaManager

    if not neo4j_exclusive:
        raise SharedDatabaseSweepError(
            "Integration tests require a Neo4j exclusive to this run; refusing "
            "to run against a shared instance because teardown would sweep other "
            "runs' data. Unset NEO4J_URI to use testcontainers, or set "
            "AKG_NEO4J_EPHEMERAL=1 if the instance really is per-run."
        )

    repo = Neo4jRepository(config=neo4j_config)

    try:
        # Verify connection
        repo.verify_connectivity()

        # Initialize schema (idempotent - won't destroy existing data)
        schema_manager = SchemaManager(repository=repo)
        schema_manager.initialize(force=False)

        yield repo

        # Teardown-only sweep, guarded by exclusive ownership.
        sweep_test_data(repo, ephemeral=neo4j_exclusive)
    finally:
        repo.close()


# =============================================================================
# Environment Fixtures
# =============================================================================


@pytest.fixture
def clean_env(monkeypatch) -> Generator[None, None, None]:
    """Clear all relevant environment variables."""
    env_vars = [
        "NEO4J_URI",
        "NEO4J_USERNAME",
        "NEO4J_PASSWORD",
        "NEO4J_DATABASE",
        "OPENAI_API_KEY",
        "EMBEDDING_MODEL",
        "ENVIRONMENT",
        "DEBUG",
    ]
    for var in env_vars:
        monkeypatch.delenv(var, raising=False)
    yield


@pytest.fixture
def production_env(monkeypatch) -> None:
    """Set up production environment."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("NEO4J_PASSWORD", "secure_password_12345")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-api-key-12345")


@pytest.fixture
def development_env(monkeypatch) -> None:
    """Set up development environment."""
    monkeypatch.setenv("ENVIRONMENT", "development")


# =============================================================================
# Sample Data Fixtures
# =============================================================================


@pytest.fixture
def sample_doi() -> str:
    """Return a unique DOI string with TEST_ suffix for test isolation.
    DOI must start with '10.' per Pydantic validation.
    """
    return f"10.TEST_{uuid.uuid4().hex[:8]}/example.2024.001"


@pytest.fixture
def sample_orcid() -> str:
    """Return a unique ORCID string for test isolation.
    ORCID must start with '0000-' per validation.
    Uses a random hex segment to make each test run unique.
    """
    hex_segment = uuid.uuid4().hex[:4].upper()
    return f"0000-{hex_segment}-2345-6789"


@pytest.fixture
def sample_datetime() -> datetime:
    """Return a sample UTC datetime."""
    return datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)


@pytest.fixture
def sample_evidence_data(sample_doi) -> dict:
    """Return valid Evidence model data."""
    return {
        "source_doi": sample_doi,
        "source_title": "A Sample Research Paper Title",
        "section": "Introduction",
        "quoted_text": "This is the quoted text from the paper.",
        "char_offset_start": 100,
        "char_offset_end": 150,
    }


@pytest.fixture
def sample_extraction_metadata_data() -> dict:
    """Return valid ExtractionMetadata model data."""
    return {
        "extraction_model": "gpt-4",
        "confidence_score": 0.95,
        "extractor_version": "1.0.0",
        "human_reviewed": False,
    }


@pytest.fixture
def sample_assumption_data() -> dict:
    """Return valid Assumption model data."""
    return {
        "text": "The data follows a normal distribution",
        "implicit": False,
        "confidence": 0.9,
    }


@pytest.fixture
def sample_constraint_data() -> dict:
    """Return valid Constraint model data."""
    return {
        "text": "Requires GPU with at least 16GB memory",
        "type": "computational",
        "confidence": 0.85,
    }


@pytest.fixture
def sample_dataset_data() -> dict:
    """Return valid Dataset model data."""
    return {
        "name": "ImageNet-1K",
        "url": "https://image-net.org/",
        "available": True,
        "size": "150GB",
    }


@pytest.fixture
def sample_metric_data() -> dict:
    """Return valid Metric model data."""
    return {
        "name": "F1-score",
        "description": "Harmonic mean of precision and recall",
        "baseline_value": 0.85,
    }


@pytest.fixture
def sample_baseline_data(sample_doi) -> dict:
    """Return valid Baseline model data."""
    return {
        "name": "BERT-base",
        "paper_doi": sample_doi,
        "performance": {"accuracy": 0.82, "f1": 0.79},
    }


@pytest.fixture
def sample_problem_data(sample_evidence_data, sample_extraction_metadata_data) -> dict:
    """Return valid Problem model data with a unique, TEST_-marked statement.

    The statement carries a per-test unique TEST_ marker so the node is
    caught by cleanup (which matches `statement STARTS WITH 'TEST_'`) and
    never collides with statement-based dedup across runs. The id is left
    to auto-generate so model-level tests can exercise that behavior.
    """
    unique = uuid.uuid4().hex[:12]
    return {
        "statement": (
            f"TEST_{unique} How can we improve the efficiency of transformer "
            "models for long-context understanding?"
        ),
        "status": "open",
        "evidence": sample_evidence_data,
        "extraction_metadata": sample_extraction_metadata_data,
    }


@pytest.fixture
def sample_paper_data(sample_doi) -> dict:
    """Return valid Paper model data."""
    return {
        "doi": sample_doi,
        "title": "Advances in Transformer Architecture for NLP Tasks",
        "authors": ["John Doe", "Jane Smith"],
        "venue": "NeurIPS 2024",
        "year": 2024,
        "abstract": "This paper presents novel improvements to transformer architectures.",
        "arxiv_id": "2401.12345",
    }


@pytest.fixture
def sample_author_data(sample_orcid) -> dict:
    """Return valid Author model data with a unique, TEST_-marked id.

    The id carries a TEST_ marker so the node is caught by cleanup; the
    name is left as a plain value so model-level tests can assert on it.
    """
    return {
        "id": f"TEST_{uuid.uuid4().hex[:12]}",
        "name": "John Doe",
        "affiliations": ["MIT", "Google Research"],
        "orcid": sample_orcid,
    }


# =============================================================================
# Config Reset Fixture
# =============================================================================


@pytest.fixture(autouse=True)
def reset_config_singleton():
    """Reset the config singleton before and after each test."""
    from agentic_kg.config import reset_config

    reset_config()
    yield
    reset_config()
