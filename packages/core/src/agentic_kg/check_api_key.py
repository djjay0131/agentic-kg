"""Cloud Run Job: prove the API accepts the current ``API_KEY`` (ADR-0007).

``python -m agentic_kg.check_api_key`` runs as the API's runtime identity, so
it can read the ``API_KEY`` secret. It reads the value **in process**, sends it
to ``GET /api/auth/check``, and exits 0 or 1. Nothing but the literal result is
printed: no key, no URL, no response body, no status detail.

The rotation workflow executes this Job *after* rolling the API service onto
``:latest`` and stops before disabling superseded versions if it fails
(ADR-0007 rule 5: verification is by effect, not by value).

Environment: ``API_URL``, ``GOOGLE_CLOUD_PROJECT``, ``API_KEY_SECRET_ID``
(default ``API_KEY``), ``API_KEY_CHECK_ATTEMPTS`` (default 8) and
``API_KEY_CHECK_INTERVAL_SECONDS`` (default 6) to ride out a cold revision.
8 x (30 s request timeout + 6 s wait) stays inside the Job's 300 s timeout.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from collections.abc import Callable
from typing import Any

from agentic_kg.rotate_password import SecretStore

PLACEHOLDER = "unset-seed-not-an-api-key"
DEFAULT_SECRET_ID = "API_KEY"
CHECK_PATH = "/api/auth/check"


class CheckFailed(RuntimeError):
    """The API did not accept the installed key."""


def _urlopen(request: urllib.request.Request, *, timeout: float):
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 — API_URL is operator config


def check_api_key(
    api_url: str,
    api_key: str,
    *,
    attempts: int = 1,
    interval: float = 6.0,
    http: Callable[..., Any] = _urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Call ``GET /api/auth/check`` with ``api_key``; raise if it does not pass."""
    if not api_key or api_key == PLACEHOLDER:
        raise CheckFailed("no usable API key")

    url = f"{api_url.rstrip('/')}{CHECK_PATH}"
    last_error: Exception | None = None

    for attempt in range(max(1, attempts)):
        try:
            request = urllib.request.Request(url, headers={"X-API-Key": api_key})
            with http(request, timeout=30) as response:
                status = getattr(response, "status", 0)
                body = response.read()
            if status != 200:
                raise CheckFailed("API rejected the key")
            if json.loads(body or b"{}").get("ok") is not True:
                raise CheckFailed("API did not confirm the key")
            return
        except Exception as exc:  # noqa: BLE001 — retry any transport/HTTP error
            last_error = exc
            if attempt + 1 < max(1, attempts):
                sleep(interval)

    raise CheckFailed("API key check did not pass") from last_error


def _missing(name: str) -> bool:
    return not os.environ.get(name)


def main() -> None:
    """Entrypoint: print exactly PASS or FAIL, then exit 0 or 1."""
    if _missing("API_URL") or _missing("GOOGLE_CLOUD_PROJECT"):
        print("API key check: FAIL")
        sys.exit(1)

    secret_id = os.environ.get("API_KEY_SECRET_ID", DEFAULT_SECRET_ID)
    try:
        api_key = SecretStore(os.environ["GOOGLE_CLOUD_PROJECT"]).access_version(secret_id)
        check_api_key(
            os.environ["API_URL"],
            api_key,
            attempts=int(os.environ.get("API_KEY_CHECK_ATTEMPTS", "8")),
            interval=float(os.environ.get("API_KEY_CHECK_INTERVAL_SECONDS", "6")),
        )
    except Exception:  # noqa: BLE001 — never print the reason (it could carry a value)
        print("API key check: FAIL")
        sys.exit(1)

    print("API key check: PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
