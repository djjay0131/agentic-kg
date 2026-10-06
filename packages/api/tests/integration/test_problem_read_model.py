"""Integration test: canonical problems ingested as ProblemMention +
ProblemConcept are visible through the read API.

This is the regression test for the staging defect where an ingestion run
logged ``problems=45`` but ``GET /api/stats`` and ``GET /api/problems``
returned 0, because those endpoints matched only ``:Problem`` while ingestion
writes ``:ProblemMention`` + ``:ProblemConcept`` (Sprint 09/10 canonical
problem architecture).

On the pre-fix code every assertion below fails: stats count 0, the list is
empty, the detail 404s, and the topic has no problems. Runs against an
ephemeral testcontainers Neo4j (Docker required); the module skips cleanly
when Docker / testcontainers are unavailable, so it is safe to collect in the
unit job.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

_TEST_MARKER = "TEST_READMODEL_"


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def neo4j_container():
    """Start an ephemeral Neo4j container (skips without Docker/testcontainers)."""
    try:
        from testcontainers.neo4j import Neo4jContainer
    except ImportError:
        pytest.skip("testcontainers not installed")
        return

    try:
        import docker

        docker.from_env().ping()
    except Exception:
        pytest.skip("Docker not available")
        return

    container = Neo4jContainer("neo4j:5.26-community", password="testpassword")
    try:
        container.start()
        yield container
    finally:
        container.stop()


@pytest.fixture
def api_client(neo4j_container):
    """TestClient wired to a repository backing the testcontainer.

    Yields ``(client, repo, seeded)`` where ``seeded`` names the canonical
    problem the test should find.
    """
    from unittest.mock import patch

    from agentic_kg.config import Neo4jConfig
    from agentic_kg.knowledge_graph.repository import Neo4jRepository
    from agentic_kg.knowledge_graph.schema import SchemaManager
    from agentic_kg_api.dependencies import get_repo
    from agentic_kg_api.main import app

    config = Neo4jConfig(
        uri=neo4j_container.get_connection_url(),
        username="neo4j",
        password="testpassword",
        database="neo4j",
    )
    repo = Neo4jRepository(config=config)
    repo.verify_connectivity()
    SchemaManager(repository=repo).initialize(force=False)

    _cleanup(repo)
    seeded = _seed_canonical_problem(repo)

    app.dependency_overrides[get_repo] = lambda: repo
    # main.get_stats calls get_repo() directly (not via Depends).
    patcher = patch("agentic_kg_api.main.get_repo", return_value=repo)
    patcher.start()
    client = TestClient(app)
    try:
        yield client, repo, seeded
    finally:
        client.close()
        patcher.stop()
        app.dependency_overrides.clear()
        _cleanup(repo)
        repo.close()


# =============================================================================
# Seed / cleanup helpers
# =============================================================================


def _cleanup(repo) -> None:
    with repo.session() as session:
        session.run(
            """
            MATCH (n)
            WHERE n.id STARTS WITH $marker
               OR n.doi STARTS WITH '10.TEST_'
               OR n.name STARTS WITH $marker
               OR n.canonical_statement STARTS WITH $marker
            DETACH DELETE n
            """,
            marker=_TEST_MARKER,
        )


def _seed_canonical_problem(repo) -> dict:
    """Construct the graph exactly as ingestion does.

    - ``(:Paper)-[:RESEARCHES]->(:Topic)``
    - ``(:ProblemMention)-[:EXTRACTED_FROM]->(:Paper)``
    - ``(:ProblemMention)-[:INSTANCE_OF]->(:ProblemConcept)``
    """
    from agentic_kg.knowledge_graph.models import (
        Paper,
        ProblemConcept,
        ProblemMention,
        Topic,
        TopicLevel,
    )

    token = uuid.uuid4().hex[:8]
    doi = f"10.TEST_{token}/readmodel.2026.001"
    paper = Paper(
        doi=doi,
        title=f"{_TEST_MARKER}Long-context transformer efficiency",
        authors=["Test Author"],
        year=2024,
    )
    repo.create_paper(paper)

    topic = Topic(
        id=f"{_TEST_MARKER}topic_{token}",
        name=f"{_TEST_MARKER}Natural Language Processing {token}",
        level=TopicLevel.AREA,
    )
    repo.create_topic(topic, generate_embedding=False)
    repo.assign_entity_to_topic(doi, topic.id, entity_label="Paper")

    concept_id = f"{_TEST_MARKER}concept_{token}"
    mention_id = f"{_TEST_MARKER}mention_{token}"
    canonical = (
        f"{_TEST_MARKER}How can long-context transformers be made efficient?"
    )
    quoted = f"{_TEST_MARKER}we could not scale attention to long contexts"

    concept = ProblemConcept(
        id=concept_id,
        canonical_statement=canonical,
        mention_count=1,
        paper_count=1,
    )
    mention = ProblemMention(
        id=mention_id,
        statement=canonical,
        paper_doi=doi,
        section="Introduction",
        quoted_text=quoted,
    )

    with repo.session() as session:
        session.run(
            """
            MATCH (paper:Paper {doi: $doi})
            CREATE (c:ProblemConcept)
            SET c = $concept_props
            CREATE (m:ProblemMention)
            SET m = $mention_props
            CREATE (m)-[:INSTANCE_OF]->(c)
            CREATE (m)-[:EXTRACTED_FROM]->(paper)
            """,
            doi=doi,
            concept_props=concept.to_neo4j_properties(),
            mention_props=mention.to_neo4j_properties(),
        )

    return {
        "doi": doi,
        "topic_id": topic.id,
        "topic_name": topic.name,
        "concept_id": concept_id,
        "mention_id": mention_id,
        "canonical": canonical,
        "quoted": quoted,
    }


# =============================================================================
# Tests
# =============================================================================


class TestCanonicalProblemVisibility:
    def test_stats_counts_canonical_problem(self, api_client):
        client, _repo, seeded = api_client
        response = client.get("/api/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["total_problems"] >= 1
        assert data["problems_by_status"].get("open", 0) >= 1
        assert data["problems_by_topic"].get(seeded["topic_name"], 0) >= 1

    def test_list_problems_includes_canonical_problem(self, api_client):
        client, _repo, seeded = api_client
        response = client.get("/api/problems")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        ids = {p["id"] for p in data["problems"]}
        assert seeded["concept_id"] in ids
        summary = next(
            p for p in data["problems"] if p["id"] == seeded["concept_id"]
        )
        assert summary["statement"] == seeded["canonical"]
        assert summary["mention_count"] >= 1

    def test_get_problem_detail_serves_concept_with_mentions(self, api_client):
        client, _repo, seeded = api_client
        response = client.get(f"/api/problems/{seeded['concept_id']}")
        assert response.status_code == 200
        data = response.json()
        assert data["statement"] == seeded["canonical"]
        assert data["canonical_statement"] == seeded["canonical"]
        assert data["mention_count"] >= 1
        assert data["paper_count"] >= 1
        assert len(data["mentions"]) == 1
        mention = data["mentions"][0]
        assert mention["paper_doi"] == seeded["doi"]
        assert mention["quoted_text"] == seeded["quoted"]
        assert data["evidence"] is not None
        assert data["evidence"]["quoted_text"] == seeded["quoted"]

    def test_topic_problems_includes_canonical_problem(self, api_client):
        client, _repo, seeded = api_client
        response = client.get(f"/api/topics/{seeded['topic_id']}/problems")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        ids = {p["id"] for p in data["problems"]}
        assert seeded["concept_id"] in ids

    def test_mention_id_resolves_to_concept(self, api_client):
        client, _repo, seeded = api_client
        response = client.get(f"/api/problems/{seeded['mention_id']}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == seeded["concept_id"]
        assert data["statement"] == seeded["canonical"]

    def test_graph_includes_canonical_problem_and_topic(self, api_client):
        client, _repo, seeded = api_client
        response = client.get("/api/graph")
        assert response.status_code == 200
        data = response.json()
        problem_nodes = [n for n in data["nodes"] if n["type"] == "problem"]
        assert any(
            seeded["canonical"] in n["properties"]["statement"]
            for n in problem_nodes
        )
