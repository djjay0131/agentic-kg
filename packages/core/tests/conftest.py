"""
Shared pytest fixtures for agentic-kg tests.

Provides common test data and fixtures used across test modules.
"""

from datetime import datetime, timezone
from typing import Generator

import pytest

# =============================================================================
# Neo4j Testcontainer Fixture
# =============================================================================


@pytest.fixture(scope="session")
def neo4j_container():
    """
    Start a Neo4j container for integration tests.

    This fixture is session-scoped to avoid starting a new container
    for each test. Tests should clean up their own data.
    """
    try:
        from testcontainers.neo4j import Neo4jContainer
    except ImportError:
        pytest.skip("testcontainers not installed")
        return

    # Check if Docker is available
    try:
        import docker
        client = docker.from_env()
        client.ping()
    except Exception:
        pytest.skip("Docker not available")
        return

    container = Neo4jContainer("neo4j:5.26-community")
    container.with_env("NEO4J_PLUGINS", '["apoc"]')

    try:
        container.start()
        yield container
    finally:
        container.stop()


@pytest.fixture
def neo4j_config(neo4j_container, monkeypatch):
    """
    Configure Neo4j connection to use the test container.

    Returns the Neo4jConfig for direct use.
    """
    from agentic_kg.config import Neo4jConfig, reset_config

    # Get connection details from container
    uri = neo4j_container.get_connection_url()
    username = "neo4j"
    password = "neo4j"  # Default password for test container

    # Set environment variables
    monkeypatch.setenv("NEO4J_URI", uri)
    monkeypatch.setenv("NEO4J_USERNAME", username)
    monkeypatch.setenv("NEO4J_PASSWORD", password)
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j")

    # Reset config to pick up new env vars
    reset_config()

    config = Neo4jConfig(
        uri=uri,
        username=username,
        password=password,
        database="neo4j",
    )

    yield config


@pytest.fixture
def neo4j_repository(neo4j_config):
    """
    Create a repository connected to the test container.

    Initializes schema and cleans up after test.
    """
    from agentic_kg.knowledge_graph.repository import Neo4jRepository
    from agentic_kg.knowledge_graph.schema import SchemaManager

    repo = Neo4jRepository(config=neo4j_config)

    try:
        # Verify connection and initialize schema
        repo.verify_connectivity()
        schema_manager = SchemaManager(repository=repo)
        schema_manager.initialize(force=True)

        yield repo

        # Clean up after test
        schema_manager.drop_all(confirm=True)
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
    """Return a valid DOI string."""
    return "10.1234/example.2024.001"


@pytest.fixture
def sample_orcid() -> str:
    """Return a valid ORCID string."""
    return "0000-0001-2345-6789"


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
    """Return valid Problem model data."""
    return {
        "statement": (
            "How can we improve the efficiency of transformer models "
            "for long-context understanding?"
        ),
        "domain": "Natural Language Processing",
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
    """Return valid Author model data."""
    return {
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
