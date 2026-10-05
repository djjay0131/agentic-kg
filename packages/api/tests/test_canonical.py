"""The read-only canonical API: disabled by default, GET-only, fake-reader driven.

These tests never need the opt-in ``migration`` extra: the router reaches the
graph through ``agentic_kg.migration.canonical_read``, and the reader itself is
overridden here with a plain fake. That is deliberate — it exercises the API
contract without a database and asserts the read-only routing surface.
"""

from __future__ import annotations

import pytest
from agentic_kg_api.config import reset_api_config

ENTITIES = [
    {
        "identity_id": "id-1",
        "entity_type": "Paper",
        "display_name": "CS-KG",
        "status": "ACTIVE",
        "aliases": [{"namespace": "doi", "key": "10.1007/x"}],
    },
    {
        "identity_id": "id-2",
        "entity_type": "Topic",
        "display_name": "Knowledge Graphs",
        "status": "ACTIVE",
        "aliases": [{"namespace": "surface", "key": "knowledge graphs"}],
    },
]
ASSERTIONS = {
    "id-1": [
        {
            "assertion_id": "as-1",
            "subject_identity": "id-1",
            "predicate": "title",
            "evidence_refs": [{"evidence_id": "ev-1"}, {"evidence_id": "ev-2"}],
        }
    ]
}


class FakeReader:
    def current_epoch(self) -> int:
        return 5

    def find_entities(self, entity_type=None, alias=None, options=None):
        if entity_type is None:
            return list(ENTITIES)
        return [e for e in ENTITIES if e["entity_type"] == entity_type]

    def get_entity(self, identity_id, options=None):
        return next((e for e in ENTITIES if e["identity_id"] == identity_id), None)

    def assertions_for(self, identity_id, options=None):
        return list(ASSERTIONS.get(identity_id, []))


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("CANONICAL_API_ENABLED", "1")
    reset_api_config()
    yield
    reset_api_config()


@pytest.fixture
def disabled(monkeypatch):
    monkeypatch.delenv("CANONICAL_API_ENABLED", raising=False)
    reset_api_config()
    yield
    reset_api_config()


@pytest.fixture
def canonical_override():
    from agentic_kg_api.dependencies import get_canonical_reader
    from agentic_kg_api.main import app

    app.dependency_overrides[get_canonical_reader] = lambda: FakeReader()
    yield
    app.dependency_overrides.pop(get_canonical_reader, None)


def test_disabled_returns_404_for_every_route(client, disabled):
    for path in (
        "/api/canonical/summary",
        "/api/canonical/entities",
        "/api/canonical/entities/id-1",
    ):
        assert client.get(path).status_code == 404


def test_summary_reports_epoch_and_counts(client, enabled, canonical_override):
    response = client.get("/api/canonical/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["epoch"] == 5
    assert body["entities_by_type"] == {"Paper": 1, "Topic": 1}


def test_entities_filter_by_type_and_text(client, enabled, canonical_override):
    typed = client.get("/api/canonical/entities", params={"type": "Paper"}).json()
    assert typed["count"] == 1
    assert typed["entities"][0]["identity_id"] == "id-1"

    searched = client.get("/api/canonical/entities", params={"q": "knowledge"}).json()
    assert [e["identity_id"] for e in searched["entities"]] == ["id-2"]


def test_entity_detail_carries_assertions_and_evidence(client, enabled, canonical_override):
    response = client.get("/api/canonical/entities/id-1")
    assert response.status_code == 200
    body = response.json()
    assert body["identity_id"] == "id-1"
    assert body["evidence_refs"] == ["ev-1", "ev-2"]


def test_unknown_entity_404s(client, enabled, canonical_override):
    assert client.get("/api/canonical/entities/nope").status_code == 404


def test_the_canonical_surface_is_get_only():
    from agentic_kg_api.routers import canonical

    routes = list(canonical.router.routes)
    assert routes, "no canonical routes registered"
    for route in routes:
        assert route.path.startswith("/api/canonical")
        assert set(route.methods) <= {"GET", "HEAD"}, (route.path, route.methods)
    # The three documented endpoints are present.
    paths = {route.path for route in routes}
    assert {"/api/canonical/summary", "/api/canonical/entities"} <= paths
    assert "/api/canonical/entities/{identity_id}" in paths
