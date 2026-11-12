"""Verify Google Identity-Aware Proxy signed assertions (ADR-0012).

IAP puts a short-lived ES256 JWT in ``x-goog-iap-jwt-assertion``. The API only
trusts an identity that verifies against Google's published IAP keys with the
audience of the IAP-protected resource.

The UI is the IAP-protected resource, so its audience (not the API's own) is
configured in ``IAP_AUDIENCE``. The Next.js server receives the assertion from
IAP and forwards it to the private API; the API verifies the signature before
recording a human decision. The opaque ``X-Goog-Authenticated-User-*`` headers
are never trusted.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
import urllib.request
from typing import Callable, Mapping

from google.auth import exceptions as google_auth_exceptions
from google.auth import jwt as google_jwt

logger = logging.getLogger(__name__)

IAP_ISSUER = "https://cloud.google.com/iap"
IAP_PUBLIC_KEY_URL = "https://www.gstatic.com/iap/verify/public_key"

#: IAP rotates keys; an hour is short enough to pick up a new key and long
#: enough to keep the verification path off the network.
KEY_TTL_SECONDS = 3600.0
CLOCK_SKEW_SECONDS = 30


class IapAuthError(Exception):
    """The assertion is missing, malformed, expired, or for the wrong audience."""


class IncompleteKeySet(Exception):
    """The assertion names a key id that is not in the supplied key set."""


def _fetch_iap_keys() -> dict[str, str]:
    """Fetch the current ``kid -> PEM public key`` map from Google.

    Kept as a module-level function so offline tests can replace it with a
    locally generated key.
    """
    request = urllib.request.Request(
        IAP_PUBLIC_KEY_URL,
        headers={"User-Agent": "agentic-kg-api"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - fixed Google URL
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise IapAuthError("IAP key document is not an object")
    keys = {str(kid): str(pem) for kid, pem in payload.items() if isinstance(pem, str) and pem}
    if not keys:
        raise IapAuthError("IAP key document is empty")
    return keys


_key_lock = threading.Lock()
_cached_keys: tuple[float, dict[str, str]] | None = None


def get_iap_keys(
    *,
    force: bool = False,
    loader: Callable[[], Mapping[str, str]] | None = None,
    now: Callable[[], float] = time.monotonic,
) -> dict[str, str]:
    """Return cached IAP public keys, refreshing after :data:`KEY_TTL_SECONDS`."""
    global _cached_keys

    with _key_lock:
        if not force and _cached_keys is not None:
            fetched_at, keys = _cached_keys
            if now() - fetched_at < KEY_TTL_SECONDS:
                return keys

        keys = dict((loader or _fetch_iap_keys)())
        if not keys:
            raise IapAuthError("IAP key document is empty")
        _cached_keys = (now(), keys)
        return keys


def reset_iap_key_cache() -> None:
    """Clear the key cache (tests and forced rotation)."""
    global _cached_keys
    with _key_lock:
        _cached_keys = None


def _unverified_claims(token: str) -> Mapping[str, object] | None:
    """Decode the token payload *without* trusting it, for safe diagnostics.

    Used only to log ``aud``/``iss`` when verification fails. Never returns or
    logs the assertion, the email, or the subject.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        padding = "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + padding))
    except Exception:  # noqa: BLE001 - diagnostics must never raise
        return None
    return payload if isinstance(payload, dict) else None


def _log_verification_failure(reason: str, token: str) -> None:
    """Log *why* verification failed and the unverified aud/iss claims.

    The token itself is a short-lived bearer credential, so it is never logged.
    The audience/issuer are not secret and are exactly what an operator needs to
    distinguish a header-forwarding bug from an audience mismatch.
    """
    claims = _unverified_claims(token)
    if claims is None:
        logger.warning("IAP verification failed: %s (claims unreadable)", reason)
        return
    logger.warning(
        "IAP verification failed: %s (aud=%r iss=%r)",
        reason,
        claims.get("aud"),
        claims.get("iss"),
    )


def _audience_candidates(audience: str) -> list[str]:
    """Return the trusted audiences to try, primary first.

    Google's documented direct-Cloud-Run audience is
    ``/projects/PROJECT_NUMBER/locations/REGION/services/SERVICE_NAME``. Some
    deployments have observed a different identifier form on the token (for
    example the project *id* instead of the number). ``IAP_AUDIENCE_ALIASES``
    is an explicit, comma-separated allowlist for those cases: each alias is
    still verified cryptographically, so this widens trust to values an
    operator deliberately named, never to an arbitrary ``aud``.
    """
    candidates = [audience]
    for item in os.getenv("IAP_AUDIENCE_ALIASES", "").split(","):
        item = item.strip()
        if item and item not in candidates:
            candidates.append(item)
    return candidates


def _decode_any(
    token: str, certs: dict[str, str], audiences: list[str]
) -> Mapping[str, object]:
    """Verify against the first matching audience, preserving key-set errors.

    ``IncompleteKeySet`` is re-raised unchanged so the caller can refresh the
    key cache once; otherwise the last audience failure is raised.
    """
    last_exc: Exception | None = None
    for candidate in audiences:
        try:
            return _decode(token, certs, candidate)
        except IncompleteKeySet:
            raise
        except Exception as exc:  # noqa: BLE001 - tried again with next alias
            last_exc = exc
    assert last_exc is not None
    raise last_exc


def verify_iap_jwt(
    token: str,
    audience: str,
    *,
    keys: Mapping[str, str] | None = None,
    loader: Callable[[], Mapping[str, str]] | None = None,
) -> str:
    """Verify an IAP assertion and return the authenticated email.

    Raises :class:`IapAuthError` for every failure. A failure logs the reason and
    the unverified ``aud``/``iss`` claims — never the token, email, or subject.
    """
    if not token:
        raise IapAuthError("missing IAP assertion")
    if not audience:
        raise IapAuthError("IAP audience is not configured")

    audiences = _audience_candidates(audience)

    if keys is not None:
        certs = dict(keys)
    else:
        certs = get_iap_keys(loader=loader)

    try:
        claims = _decode_any(token, certs, audiences)
    except IncompleteKeySet:
        # A key rotation can race the TTL cache: refresh once and retry.
        logger.info("IAP assertion used an uncached key; refreshing IAP keys")
        certs = get_iap_keys(force=True, loader=loader)
        try:
            claims = _decode_any(token, certs, audiences)
        except Exception as retry_exc:  # noqa: BLE001 - normalised below
            _log_verification_failure(
                f"invalid assertion after key refresh: {retry_exc}", token
            )
            raise IapAuthError("invalid IAP assertion") from retry_exc
    except Exception as exc:  # noqa: BLE001 - normalised so callers get one error
        _log_verification_failure(f"invalid assertion: {exc}", token)
        raise IapAuthError("invalid IAP assertion") from exc

    if claims.get("iss") != IAP_ISSUER:
        _log_verification_failure("issuer mismatch", token)
        raise IapAuthError("invalid IAP assertion issuer")

    email = claims.get("email")
    if not isinstance(email, str) or not email.strip():
        _log_verification_failure("missing email claim", token)
        raise IapAuthError("IAP assertion has no email")
    if not claims.get("sub"):
        _log_verification_failure("missing sub claim", token)
        raise IapAuthError("IAP assertion has no subject")

    return email.strip().lower()


def _unverified_header(token: str) -> Mapping[str, object]:
    """Parse the JOSE header without trusting it, to enforce ES256 ourselves.

    ``google.auth.jwt.decode`` selects a verifier from the token's own ``alg``
    header. Without this check a token could declare HS256 and use the public
    IAP key (which is public) as an HMAC secret. IAP only ever signs ES256.
    """
    try:
        encoded = token.split(".")[0]
        padding = "=" * (-len(encoded) % 4)
        header = json.loads(base64.urlsafe_b64decode(encoded + padding))
    except Exception as exc:  # noqa: BLE001 - malformed token, normalised
        raise IapAuthError("malformed IAP assertion header") from exc
    if not isinstance(header, dict):
        raise IapAuthError("malformed IAP assertion header")
    return header


def _decode(token: str, certs: dict[str, str], audience: str) -> Mapping[str, object]:
    header = _unverified_header(token)
    if header.get("alg") != "ES256":
        raise IapAuthError("IAP assertion is not ES256")
    key_id = header.get("kid")
    if not isinstance(key_id, str) or key_id not in certs:
        raise IncompleteKeySet("IAP assertion names an unknown key id")

    try:
        return google_jwt.decode(
            token,
            certs=certs,
            audience=audience,
            clock_skew_in_seconds=CLOCK_SKEW_SECONDS,
        )
    except google_auth_exceptions.MalformedError as exc:
        if "Certificate for key id" in str(exc):
            raise IncompleteKeySet(str(exc)) from exc
        raise


__all__ = [
    "IAP_ISSUER",
    "IapAuthError",
    "get_iap_keys",
    "reset_iap_key_cache",
    "verify_iap_jwt",
]
