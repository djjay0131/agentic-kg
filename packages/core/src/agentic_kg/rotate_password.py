"""Cloud Run Job entrypoint: rotate the Neo4j credential (ADR-0003).

Neo4j is VPC-private after ADR-0003, so this runs *inside* the VPC as the
``agentic-kg-rotate-neo4j-<env>`` Cloud Run Job. It reads:

- ``NEO4J_URI``          — the internal bolt URI (Secret Manager)
- ``NEO4J_USERNAME``     — defaults to ``neo4j``
- ``NEO4J_PASSWORD``     — the *current* password (Secret Manager)
- ``NEO4J_PASSWORD_NEXT``— the *next* password (transport Secret Manager)

It changes the password with ``ALTER CURRENT USER SET PASSWORD``, then
reconnects with the new credential and runs a read query to prove the
change took effect. Neither password is ever logged.

Exit codes (mirroring ``job_runner.py``):
  0 = rotation complete and verified
  1 = rotation failed
  2 = missing configuration
"""

from __future__ import annotations

import logging
import os
import sys

from neo4j import GraphDatabase

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


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    uri = _required_env("NEO4J_URI")
    current_password = _required_env("NEO4J_PASSWORD")
    next_password = _required_env("NEO4J_PASSWORD_NEXT")
    username = os.environ.get("NEO4J_USERNAME", "neo4j")

    try:
        _rotate(uri, username, current_password, next_password)
    except Exception as exc:  # noqa: BLE001 — log type, never the value
        logger.error("Neo4j password rotation failed: %s", type(exc).__name__)
        sys.exit(1)

    logger.info("Neo4j password rotation complete and verified")
    sys.exit(0)


if __name__ == "__main__":
    main()
