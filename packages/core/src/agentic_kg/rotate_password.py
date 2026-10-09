"""Cloud Run Job entrypoint: rotate the Neo4j credential (ADR-0006, ADR-0007).

Neo4j is VPC-private after ADR-0006, so this runs *inside* the VPC as the
``agentic-kg-rotate-neo4j-<env>`` Cloud Run Job, under its own
``neo4j-rotator-<env>`` service account.

**In-GCP mode (ADR-0007), entrypoint ``python -m agentic_kg.rotate_password_gcp``.**
A separate module on purpose: an image that predates it fails with
``ModuleNotFoundError`` before touching Neo4j. The Job
*generates* the new password itself, so it never leaves GCP:

1. authenticate with ``NEO4J_PASSWORD`` (current). If that fails and the
   staged ``NEO4J_PASSWORD_NEXT`` works, a previous rotation was interrupted
   after ``ALTER``: promote the staged value to ``NEO4J_PASSWORD`` first;
2. generate a strong password and store it as a new ``NEO4J_PASSWORD_NEXT``
   version *before* touching Neo4j (a crash can never lose the live value);
3. ``ALTER CURRENT USER SET PASSWORD`` and verify a read with the new value;
4. store it as a new ``NEO4J_PASSWORD`` version.

The orchestrating workflow then only rolls services and manages version
*metadata*; it never reads a value.

Environment: ``NEO4J_URI``, ``NEO4J_USERNAME`` (default ``neo4j``),
``NEO4J_PASSWORD``, ``NEO4J_PASSWORD_NEXT`` (staged/recovery value, optional
in GCP mode), ``GOOGLE_CLOUD_PROJECT``, ``NEO4J_PASSWORD_SECRET_ID``,
``NEO4J_PASSWORD_NEXT_SECRET_ID``.

**Legacy mode** (``python -m agentic_kg.rotate_password``): the workflow
supplied ``NEO4J_PASSWORD_NEXT`` and the Job only changes and verifies it. It
refuses the non-secret placeholder.

No password is ever logged.

Exit codes (mirroring ``job_runner.py``):
  0 = rotation complete and verified
  1 = rotation failed
  2 = missing configuration
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import sys
import urllib.request
from collections.abc import Callable

from neo4j import GraphDatabase
from neo4j.exceptions import AuthError

logger = logging.getLogger(__name__)

_ALTER_PASSWORD = (
    "ALTER CURRENT USER SET PASSWORD FROM $currentPassword TO $nextPassword"
)
_READ_PROBE = "RETURN 1 AS ok"


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        logger.error("%s env var is required", name)
        sys.exit(2)
    return value


def _rotate(
    uri: str,
    username: str,
    current_password: str,
    next_password: str,
    driver_factory=GraphDatabase.driver,
) -> None:
    """Perform and verify the password change.

    ``driver_factory`` is injectable so the behaviour can be unit-tested
    without a live Neo4j.
    """
    with driver_factory(uri, auth=(username, current_password)) as driver:
        driver.verify_connectivity()
        with driver.session() as session:
            session.run(
                _ALTER_PASSWORD,
                currentPassword=current_password,
                nextPassword=next_password,
            ).consume()

    # Prove the new credential works before the caller promotes it.
    with driver_factory(uri, auth=(username, next_password)) as driver:
        driver.verify_connectivity()
        with driver.session() as session:
            record = session.run(_READ_PROBE).single()
            if record is None or record["ok"] != 1:
                raise RuntimeError("post-rotation read probe returned no result")


# Must match the non-secret seed Terraform writes and the workflow restores
# (infra/main.tf ``neo4j_password_next_seed``): it means "nothing staged".
PLACEHOLDER = "unset-seed-not-a-password"

_METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/"
    "service-accounts/default/token"
)
_SECRET_MANAGER = "https://secretmanager.googleapis.com/v1"


def _http(url: str, *, method: str = "GET", headers=None, body: bytes | None = None) -> bytes:
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 — fixed GCP hosts
        return resp.read()


class SecretStore:
    """Adds Secret Manager versions over REST with the Job's own identity.

    Standard library only (no new image dependency). ``http`` is injectable
    for tests. Only the *version name* is ever logged.
    """

    def __init__(self, project: str, http: Callable[..., bytes] = _http) -> None:
        self._project = project
        self._http = http

    def _token(self) -> str:
        raw = self._http(_METADATA_TOKEN_URL, headers={"Metadata-Flavor": "Google"})
        return str(json.loads(raw)["access_token"])

    def add_version(self, secret_id: str, value: str) -> str:
        url = f"{_SECRET_MANAGER}/projects/{self._project}/secrets/{secret_id}:addVersion"
        body = json.dumps(
            {"payload": {"data": base64.b64encode(value.encode()).decode()}}
        ).encode()
        raw = self._http(
            url,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token()}",
                "Content-Type": "application/json",
            },
            body=body,
        )
        name = str(json.loads(raw).get("name", "?"))
        logger.info("Added secret version %s", name)
        return name


def _authenticates(uri: str, username: str, password: str, driver_factory) -> bool:
    try:
        with driver_factory(uri, auth=(username, password)) as driver:
            driver.verify_connectivity()
        return True
    except AuthError:
        return False


def _generate() -> str:
    return secrets.token_urlsafe(48)


def rotate_in_gcp(
    *,
    uri: str,
    username: str,
    current_password: str,
    staged_password: str | None,
    store: SecretStore,
    password_secret_id: str,
    next_secret_id: str,
    driver_factory=GraphDatabase.driver,
    generate: Callable[[], str] = _generate,
) -> None:
    """ADR-0007 rotation: generate, stage, change, verify, store — all in GCP."""
    if not _authenticates(uri, username, current_password, driver_factory):
        staged = staged_password if staged_password and staged_password != PLACEHOLDER else None
        if staged is None or not _authenticates(uri, username, staged, driver_factory):
            raise RuntimeError("neither the current nor the staged credential authenticates")
        logger.warning("Recovering an interrupted rotation: promoting the staged credential")
        store.add_version(password_secret_id, staged)
        current_password = staged

    new_password = generate()
    store.add_version(next_secret_id, new_password)  # stored before Neo4j changes
    _rotate(uri, username, current_password, new_password, driver_factory)
    store.add_version(password_secret_id, new_password)


def main() -> None:
    """Legacy entrypoint: the workflow staged NEO4J_PASSWORD_NEXT.

    In-GCP generation (ADR-0007) is ``python -m agentic_kg.rotate_password_gcp``.
    """
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    uri = _required_env("NEO4J_URI")
    current_password = _required_env("NEO4J_PASSWORD")
    next_password = _required_env("NEO4J_PASSWORD_NEXT")
    username = os.environ.get("NEO4J_USERNAME", "neo4j")

    # Never install the public, non-secret seed as the database password
    # (review of #132: a stale image with nothing staged would have done so).
    if next_password == PLACEHOLDER:
        logger.error("NEO4J_PASSWORD_NEXT holds the placeholder; refusing to rotate")
        sys.exit(2)

    try:
        _rotate(uri, username, current_password, next_password)
    except Exception as exc:  # noqa: BLE001 — log type, never the value
        logger.error("Neo4j password rotation failed: %s", type(exc).__name__)
        sys.exit(1)

    logger.info("Neo4j password rotation complete and verified")
    sys.exit(0)


def main_gcp() -> None:
    """ADR-0007 entrypoint: generate and store the password inside GCP."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    uri = _required_env("NEO4J_URI")
    current_password = _required_env("NEO4J_PASSWORD")
    store = SecretStore(_required_env("GOOGLE_CLOUD_PROJECT"))
    password_secret_id = _required_env("NEO4J_PASSWORD_SECRET_ID")
    next_secret_id = _required_env("NEO4J_PASSWORD_NEXT_SECRET_ID")
    try:
        rotate_in_gcp(
            uri=uri,
            username=os.environ.get("NEO4J_USERNAME", "neo4j"),
            current_password=current_password,
            staged_password=os.environ.get("NEO4J_PASSWORD_NEXT"),
            store=store,
            password_secret_id=password_secret_id,
            next_secret_id=next_secret_id,
        )
    except Exception as exc:  # noqa: BLE001 — log type, never the value
        logger.error("Neo4j password rotation failed: %s", type(exc).__name__)
        sys.exit(1)
    logger.info("Neo4j password rotation complete and verified")
    sys.exit(0)


if __name__ == "__main__":
    main()
