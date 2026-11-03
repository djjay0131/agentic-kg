"""Human identity verification for mutating decisions (ADR-0011).

No GCP and no network: IAP-shaped tokens are signed with a locally generated
P-256 key and the public key map is injected.
"""

from __future__ import annotations

import pytest
from agentic_kg_api import iap
from agentic_kg_api.config import reset_api_config
from agentic_kg_api.dependencies import current_user
from fastapi import HTTPException

from .iap_test_support import AUDIENCE, KID, make_signer, make_token

PUBLIC_PEM, SIGNER = make_signer()


@pytest.fixture
def iap_mode(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "iap")
    monkeypatch.setenv("IAP_AUDIENCE", AUDIENCE)
    reset_api_config()
    iap.reset_iap_key_cache()
    monkeypatch.setattr(iap, "_fetch_iap_keys", lambda: {KID: PUBLIC_PEM})
    yield
    reset_api_config()
    iap.reset_iap_key_cache()


def test_valid_assertion_returns_the_verified_email(iap_mode):
    token = make_token(SIGNER)
    assert current_user(x_user_id=None, iap_assertion=token) == "alice@vt.edu"


def test_email_is_normalised(iap_mode):
    token = make_token(SIGNER, email="Alice@VT.edu")
    assert current_user(x_user_id=None, iap_assertion=token) == "alice@vt.edu"


def test_missing_assertion_is_401(iap_mode):
    with pytest.raises(HTTPException) as exc:
        current_user(x_user_id=None, iap_assertion=None)
    assert exc.value.status_code == 401


def test_x_user_id_is_not_a_fallback_in_iap_mode(iap_mode):
    with pytest.raises(HTTPException) as exc:
        current_user(x_user_id="alice", iap_assertion=None)
    assert exc.value.status_code == 401


def test_wrong_audience_is_401(iap_mode):
    token = make_token(SIGNER, audience="/projects/1/locations/us-central1/services/other")
    with pytest.raises(HTTPException) as exc:
        current_user(x_user_id=None, iap_assertion=token)
    assert exc.value.status_code == 401


def test_wrong_issuer_is_401(iap_mode):
    token = make_token(SIGNER, issuer="https://accounts.google.com")
    with pytest.raises(HTTPException) as exc:
        current_user(x_user_id=None, iap_assertion=token)
    assert exc.value.status_code == 401


def test_expired_assertion_is_401(iap_mode):
    token = make_token(SIGNER, expires_in=-120)
    with pytest.raises(HTTPException) as exc:
        current_user(x_user_id=None, iap_assertion=token)
    assert exc.value.status_code == 401


def test_assertion_without_email_is_401(iap_mode):
    token = make_token(SIGNER, email=None)
    with pytest.raises(HTTPException) as exc:
        current_user(x_user_id=None, iap_assertion=token)
    assert exc.value.status_code == 401


def test_assertion_without_subject_is_401(iap_mode):
    token = make_token(SIGNER, subject=None)
    with pytest.raises(HTTPException) as exc:
        current_user(x_user_id=None, iap_assertion=token)
    assert exc.value.status_code == 401


def test_missing_audience_fails_closed_503(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "iap")
    monkeypatch.delenv("IAP_AUDIENCE", raising=False)
    reset_api_config()
    token = make_token(SIGNER)
    with pytest.raises(HTTPException) as exc:
        current_user(x_user_id=None, iap_assertion=token)
    assert exc.value.status_code == 503

    reset_api_config()


def test_unrecognised_mode_fails_closed_like_iap(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "prod")
    monkeypatch.setenv("IAP_AUDIENCE", AUDIENCE)
    reset_api_config()
    with pytest.raises(HTTPException) as exc:
        current_user(x_user_id="alice", iap_assertion=None)
    assert exc.value.status_code == 401

    reset_api_config()


def test_dev_mode_accepts_x_user_id(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "dev")
    reset_api_config()
    assert current_user(x_user_id="local-dev", iap_assertion=None) == "local-dev"

    reset_api_config()


def test_dev_mode_without_identity_is_401(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "dev")
    reset_api_config()
    with pytest.raises(HTTPException) as exc:
        current_user(x_user_id=None, iap_assertion=None)
    assert exc.value.status_code == 401

    reset_api_config()


def test_dev_mode_does_not_accept_an_iap_token_alone(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "dev")
    reset_api_config()
    token = make_token(SIGNER)
    with pytest.raises(HTTPException) as exc:
        current_user(x_user_id=None, iap_assertion=token)
    assert exc.value.status_code == 401

    reset_api_config()


def test_key_rotation_refreshes_once_and_retries(iap_mode, monkeypatch):
    new_pub, new_signer = make_signer(kid="test-key-2")
    calls = []

    def loader():
        calls.append(1)
        return {KID: PUBLIC_PEM} if len(calls) == 1 else {"test-key-2": new_pub}

    monkeypatch.setattr(iap, "_fetch_iap_keys", loader)
    token = make_token(new_signer, kid="test-key-2")
    assert current_user(x_user_id=None, iap_assertion=token) == "alice@vt.edu"
    assert len(calls) == 2


def test_keys_are_cached_between_requests(iap_mode, monkeypatch):
    calls = []
    monkeypatch.setattr(
        iap,
        "_fetch_iap_keys",
        lambda: calls.append(1) or {KID: PUBLIC_PEM},
    )
    token = make_token(SIGNER)
    current_user(x_user_id=None, iap_assertion=token)
    current_user(x_user_id=None, iap_assertion=token)
    assert len(calls) == 1


def test_auth_me_returns_the_iap_email(client, iap_mode):
    token = make_token(SIGNER)
    response = client.get("/api/auth/me", headers={"x-goog-iap-jwt-assertion": token})
    assert response.status_code == 200
    assert response.json() == {"email": "alice@vt.edu"}


def test_auth_me_requires_a_verified_identity_in_iap_mode(client, iap_mode):
    response = client.get("/api/auth/me", headers={"X-User-ID": "alice"})
    assert response.status_code == 401


def test_auth_me_returns_the_dev_user_in_dev_mode(client, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "dev")
    reset_api_config()
    response = client.get("/api/auth/me", headers={"X-User-ID": "local@dev"})
    assert response.status_code == 200
    assert response.json() == {"email": "local@dev"}
    reset_api_config()


def test_non_es256_algorithm_is_rejected_before_signature_check(iap_mode):
    import base64
    import json
    import time

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    header = b64(json.dumps({"alg": "HS256", "kid": KID, "typ": "JWT"}).encode())
    payload = b64(
        json.dumps(
            {
                "iss": "https://cloud.google.com/iap",
                "aud": AUDIENCE,
                "sub": "user-123",
                "email": "alice@vt.edu",
                "iat": int(time.time()) - 10,
                "exp": int(time.time()) + 300,
            }
        ).encode()
    )
    token = f"{header}.{payload}.not-a-real-signature"

    with pytest.raises(HTTPException) as exc:
        current_user(x_user_id=None, iap_assertion=token)
    assert exc.value.status_code == 401
