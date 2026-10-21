"""Enforced API-key auth (spec R-3 C5, AC-5; ADR-0007).

``GET /api/auth/check`` is the first (and for now only) router behind
``require_api_key``. Public endpoints must stay public, and a missing or
placeholder key must fail closed rather than open.
"""

from __future__ import annotations

import pytest
from agentic_kg_api.config import API_KEY_PLACEHOLDER, reset_api_config

_KEY = "test-api-key-0123456789abcdef"


@pytest.fixture
def auth_client(client, monkeypatch):
    """The shared client with a real API_KEY installed for the request."""
    monkeypatch.setenv("API_KEY", _KEY)
    reset_api_config()
    yield client
    reset_api_config()


@pytest.fixture
def unconfigured_client(client, monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    reset_api_config()
    yield client
    reset_api_config()


def test_missing_key_is_401_without_saying_which(auth_client) -> None:
    response = auth_client.get("/api/auth/check")
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid or missing API key"


def test_wrong_key_is_the_same_401_as_missing(auth_client) -> None:
    missing = auth_client.get("/api/auth/check").json()
    wrong = auth_client.get("/api/auth/check", headers={"X-API-Key": "nope"})
    assert wrong.status_code == 401
    assert wrong.json() == missing


def test_right_key_returns_ok(auth_client) -> None:
    response = auth_client.get("/api/auth/check", headers={"X-API-Key": _KEY})
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_unset_key_fails_closed(unconfigured_client) -> None:
    response = unconfigured_client.get(
        "/api/auth/check", headers={"X-API-Key": "anything"}
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "auth not configured"


def test_placeholder_seed_fails_closed(client, monkeypatch) -> None:
    monkeypatch.setenv("API_KEY", API_KEY_PLACEHOLDER)
    reset_api_config()
    try:
        response = client.get(
            "/api/auth/check", headers={"X-API-Key": API_KEY_PLACEHOLDER}
        )
        assert response.status_code == 503
        assert response.json()["detail"] == "auth not configured"
    finally:
        reset_api_config()


def test_public_endpoints_are_unchanged(auth_client, mock_repo) -> None:
    mock_repo.list_problem_views.return_value = []

    assert auth_client.get("/").status_code == 200
    assert auth_client.get("/health").status_code == 200
    assert auth_client.get("/api/problems").status_code == 200


def test_key_is_compared_with_compare_digest(auth_client, monkeypatch) -> None:
    import agentic_kg_api.dependencies as deps

    calls = []
    real = deps.hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(deps.hmac, "compare_digest", spy)
    assert (
        auth_client.get("/api/auth/check", headers={"X-API-Key": _KEY}).status_code
        == 200
    )
    assert calls


def test_decision_comes_from_compare_digest(auth_client, monkeypatch) -> None:
    """With compare_digest forced to False the right key must still be refused,
    so no earlier ``==`` shortcut can accept it."""
    import agentic_kg_api.dependencies as deps

    monkeypatch.setattr(deps.hmac, "compare_digest", lambda a, b: False)
    assert (
        auth_client.get("/api/auth/check", headers={"X-API-Key": _KEY}).status_code
        == 401
    )
