"""
Shared pytest fixtures for agentic-kg tests.

Provides common test data and fixtures used across test modules.
"""

from datetime import datetime, timezone
from typing import Generator
from urllib.parse import urlparse
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


# =============================================================================
# Ownership enforced at the seam (issue #78)
# =============================================================================
#
# The guard below sits on ``Neo4jRepository.__init__`` -- the single choke point
# every database access in this codebase passes through. Bare
# ``Neo4jRepository()``, ``get_repository()`` and ``initialize_schema()`` all
# construct one, so guarding here covers paths no per-file patch could.
#
# Why it exists: two integration modules gated themselves on
# ``skipif(not NEO4J_AVAILABLE)``, so the presence of NEO4J_* credentials
# *enabled* them, and they then built a bare ``Neo4jRepository()`` from those
# same credentials and ran ``initialize_schema(force=True)`` against it. Neither
# carried ``pytest.mark.integration``, so no ``-m`` filter deselected them, and
# they are collected by ``make test``, ``make test-core`` and the command
# README.md documents as "unit tests". Measured on a stand-in database: running
# that documented command with NEO4J_* set took the target from 0 to 10
# constraints -- unauthorised DDL -- while the ownership guard added in #75
# correctly refused everything that went through its fixture.
#
# That is the polarity inversion the audit named: the same environment variable
# that makes the fixture refuse made those modules run. Fixing the two files
# would leave the next such file free to reintroduce it, so the rule is enforced
# structurally instead: a repository may only be constructed against a database
# this session has declared, and the only thing that declares one is starting it.


class UnownedDatabaseError(RuntimeError):
    """
    Raised when a repository is constructed against a database this pytest
    session has not declared as its own.

    Declaration is not an assertion anyone can make from the environment. A
    target becomes declared by being *started* by this session
    (``neo4j_container``), or -- for the e2e suite alone, whose entire purpose
    is to exercise a deployed environment and which runs only under explicit
    commands -- by ``tests/e2e/conftest.py`` naming its staging target
    deliberately in code.
    """


# Targets this session is allowed to talk to, as (host, port).
_DECLARED_TARGETS: set = set()


def _target_key(uri: str):
    """Normalise a bolt/neo4j URI to the (host, port) pair it addresses."""
    parsed = urlparse(uri)
    if parsed.hostname:
        return (parsed.hostname, parsed.port)
    return (uri, None)


def declare_session_database(uri: str, *, reason: str) -> None:
    """Declare a database as one this session may talk to.

    ``reason`` is required so every declaration carries its justification at the
    call site; there are exactly two in this repository.
    """
    _DECLARED_TARGETS.add(_target_key(uri))


def database_is_declared(uri: str) -> bool:
    return _target_key(uri) in _DECLARED_TARGETS


def _forget_declared_databases() -> None:
    """Test hook: reset the registry."""
    _DECLARED_TARGETS.clear()


@pytest.fixture(scope="session", autouse=True)
def enforce_database_ownership():
    """Refuse any repository built against an undeclared database.

    Autouse and session-scoped, so it covers every test module in this tree
    automatically -- including ones written after this fixture. A new test file
    that constructs a bare ``Neo4jRepository()`` gets an
    ``UnownedDatabaseError`` rather than a silent write to whatever ``NEO4J_URI``
    happens to name.
    """
    from agentic_kg.knowledge_graph import repository as repository_module

    original_init = repository_module.Neo4jRepository.__init__

    def guarded_init(self, config=None):
        original_init(self, config)
        uri = getattr(getattr(self, "_config", None), "uri", None)
        if uri is None or database_is_declared(uri):
            return
        raise UnownedDatabaseError(
            f"Refusing to build a Neo4jRepository against {uri!r}: this pytest "
            "session has not declared that database as its own.\n\n"
            "Tests must obtain a repository from the `neo4j_repository` fixture, "
            "which runs against a throwaway container this session starts. Do not "
            "construct `Neo4jRepository()` directly, and do not gate a test on "
            "NEO4J_* being present -- credentials in the environment are not "
            "permission to write to the database they name.\n\n"
            "If Docker is unavailable the fixture skips, which is the correct "
            "outcome: a test that cannot get an owned database does not run."
        )

    repository_module.Neo4jRepository.__init__ = guarded_init
    try:
        yield
    finally:
        repository_module.Neo4jRepository.__init__ = original_init


@pytest.fixture(scope="session")
def neo4j_container():
    """
    Start a throwaway Neo4j container for integration tests.

    Session-scoped, so one container serves the whole run.

    NEO4J_URI is deliberately ignored (issue #78). This fixture used to hand
    back ``None`` when NEO4J_* were set, so the suite would address an external
    database instead; the guard added in #75 then refused it, turning every such
    test into a setup *error* on any machine that happened to export those
    variables. Errors are noisy enough to invite people to disable the guard,
    and the fallback had no legitimate user: CI sets no NEO4J_URI, and the e2e
    suite reads STAGING_* through its own config.

    So there is now exactly one answer to "which database do integration tests
    use?" -- one this session started. When it cannot start one, the tests skip,
    which is the honest outcome.
    """
    try:
        from testcontainers.neo4j import Neo4jContainer
    except ImportError:
        pytest.skip("testcontainers not installed; integration tests need an owned Neo4j")
        return

    # Check if Docker is available
    try:
        import docker
        client = docker.from_env()
        client.ping()
    except Exception:
        pytest.skip("Docker not available; integration tests need an owned Neo4j")
        return

    container = Neo4jContainer("neo4j:5.26-community", password="testpassword")
    container.with_env("NEO4J_PLUGINS", '["apoc"]')

    try:
        container.start()
        # Starting it is what makes it ours -- the only way a target becomes
        # declared for the integration suite.
        declare_session_database(
            container.get_connection_url(),
            reason="this session started this throwaway container",
        )
        yield container
    finally:
        container.stop()


@pytest.fixture
def neo4j_config(neo4j_container, monkeypatch):
    """
    Configure Neo4j connection for tests.

    Always the container this session started -- see ``neo4j_container`` for why
    the environment-variable branch was removed (issue #78). The NEO4J_* values
    are monkeypatched to point at that container, so code under test that calls
    ``get_repository()`` or ``initialize_schema()`` resolves to the owned
    database rather than to whatever the developer's shell exports.
    """
    from agentic_kg.config import Neo4jConfig, reset_config

    if neo4j_container is not None:
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


def session_owns_database(container) -> bool:
    """
    True when this pytest session exclusively owns the Neo4j it is talking to.

    Ownership is a *fact*, not an assertion: it is true exactly when this
    session started its own throwaway container. There is deliberately no
    environment variable or flag that can claim ownership of a database the
    session did not create.

    An earlier revision of this fix accepted ``AKG_NEO4J_EPHEMERAL=1`` as proof
    of ownership. That made the guard bypassable by the one configuration it
    exists to prevent -- NEO4J_URI pointed at shared staging plus the flag set
    -- and CI carried the flag pre-set, so restoring NEO4J_URI to that step
    would have silently re-armed the data race while every test stayed green.
    A switch whose only function is to disable a safety property must not exist.
    """
    return container is not None


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
            "Run integration tests against a per-run Neo4j: leave NEO4J_URI "
            "unset and the fixtures start a throwaway container for this "
            "session alone. There is no flag to override this."
        )

    with repo.session() as session:
        record = session.run(TEST_DATA_COUNT_QUERY).single()
        doomed = record["n"] if record else 0
        session.run(TEST_DATA_SWEEP_QUERY)
    return doomed


@pytest.fixture(scope="session")
def neo4j_exclusive(neo4j_container) -> bool:
    """Whether this session exclusively owns the Neo4j under test."""
    return session_owns_database(neo4j_container)


@pytest.fixture
def neo4j_repository(neo4j_config, neo4j_exclusive):
    """
    Create a repository connected to Neo4j.

    Initializes schema and sweeps this session's test data on teardown.

    Requires an exclusively-owned database (see ``session_owns_database``). The
    sweep is teardown-only: a per-run database starts empty, so the old
    pre-test sweep bought nothing and doubled the window in which a concurrent
    run's rows could be destroyed.
    """
    from agentic_kg.knowledge_graph.repository import Neo4jRepository
    from agentic_kg.knowledge_graph.schema import SchemaManager

    if not neo4j_exclusive:
        raise SharedDatabaseSweepError(
            "Integration tests require a Neo4j exclusive to this run; refusing "
            "to run against a shared instance because teardown would sweep "
            "other runs' data. Unset NEO4J_URI so this session starts its own "
            "container."
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
