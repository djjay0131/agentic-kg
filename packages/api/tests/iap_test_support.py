"""Offline helpers for signing IAP-shaped assertions (ADR-0011 tests).

The API verifies ES256 signatures against Google's published IAP keys. Tests
generate a local P-256 key and sign tokens with the same algorithm so the
verification path is exercised without any network call or real credential.
"""

from __future__ import annotations

import base64
import json
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from google.auth import crypt

IAP_ISSUER = "https://cloud.google.com/iap"
AUDIENCE = "/projects/123456789/locations/us-central1/services/agentic-kg-ui-staging"
KID = "test-key-1"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def make_signer(kid: str = KID):
    """Return ``(public_pem, signer)`` for a fresh local ES256 key."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    return public_pem, crypt.ES256Signer.from_string(private_pem)


def make_token(
    signer,
    *,
    kid: str = KID,
    audience: str = AUDIENCE,
    issuer: str = IAP_ISSUER,
    email: str | None = "alice@vt.edu",
    subject: str | None = "user-123",
    issued_at: int | None = None,
    expires_in: int = 300,
) -> str:
    """Build an IAP-shaped ES256 JWT. ``expires_in`` may be negative."""
    now = int(time.time())
    payload: dict[str, object] = {
        "iss": issuer,
        "aud": audience,
        "iat": now - 10 if issued_at is None else issued_at,
        "exp": now + expires_in,
    }
    if email is not None:
        payload["email"] = email
    if subject is not None:
        payload["sub"] = subject
    header = {"alg": "ES256", "kid": kid, "typ": "JWT"}
    signing_input = (
        f"{_b64(json.dumps(header, separators=(',', ':')).encode())}."
        f"{_b64(json.dumps(payload, separators=(',', ':')).encode())}"
    )
    signature = signer.sign(signing_input.encode("ascii"))
    return f"{signing_input}.{_b64(signature)}"
