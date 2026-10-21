"""Cloud Run Job entrypoint: rotate the API key inside GCP (ADR-0007 rule 2).

``python -m agentic_kg.rotate_api_key_gcp`` runs as the dedicated
``api-key-rotator-<env>`` service account, whose only permission is
``secretmanager.secretVersionAdder`` on the ``API_KEY`` secret. The Job
**generates** the key with :func:`secrets.token_urlsafe` and writes it straight
to Secret Manager, so the value never transits a GitHub runner, a log, a
workflow output, an artifact or a person.

The orchestrating workflow (``.github/workflows/rotate-api-key.yml``) only
executes this Job, rolls the API service onto ``:latest``, executes the check
Job, and then disables superseded versions. It never reads a value.

Guards:

* the public Terraform placeholder seed is never installed as a key;
* a generated value that is empty, too short or the placeholder is refused
  *before* the store is touched;
* if the store write fails, nothing else has happened (there is exactly one
  write), and the process exits non-zero so the workflow stops before rolling.

Environment: ``GOOGLE_CLOUD_PROJECT``, ``API_KEY_SECRET_ID`` (default
``API_KEY``). ``API_KEY``, if present, is only inspected for the placeholder;
the Job never needs to read the current key.

Exit codes: 0 = a new version was added; 1 = rotation failed; 2 = missing
configuration. No key material is ever logged.
"""

from __future__ import annotations

import logging
import os
import secrets
import sys
from collections.abc import Callable

from agentic_kg.rotate_password import SecretStore

logger = logging.getLogger(__name__)

#: Must match the non-secret seed Terraform writes to the ``API_KEY`` secret
#: (``infra/main.tf``) and the API's ``API_KEY_PLACEHOLDER``. It means "no key
#: installed yet"; it must never become the live key.
PLACEHOLDER = "unset-seed-not-an-api-key"

DEFAULT_SECRET_ID = "API_KEY"

#: token_urlsafe(48) yields 64 URL-safe characters. Refuse anything that is
#: clearly not the product of the intended generator.
MIN_KEY_LENGTH = 32


def _generate() -> str:
    return secrets.token_urlsafe(48)


def rotate_api_key(
    *,
    store: SecretStore,
    secret_id: str,
    generate: Callable[[], str] = _generate,
) -> str:
    """Generate a key and add it as a new Secret Manager version.

    Returns the new version's resource name (metadata only). Raises before
    writing anything if the generated value is unusable, and propagates a
    store failure unchanged so the caller can exit non-zero.
    """
    new_key = generate()
    if not new_key or new_key == PLACEHOLDER:
        raise RuntimeError("refusing to install the placeholder as the API key")
    if len(new_key) < MIN_KEY_LENGTH:
        raise RuntimeError("generated API key is too short")

    name = store.add_version(secret_id, new_key)
    logger.info("Added API_KEY version %s", name)
    return name


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        logger.error("%s env var is required", name)
        sys.exit(2)
    return value


def main() -> None:
    """Entrypoint: generate and store a new ``API_KEY`` version inside GCP."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    project = _required_env("GOOGLE_CLOUD_PROJECT")
    secret_id = os.environ.get("API_KEY_SECRET_ID", DEFAULT_SECRET_ID)

    # Defensive: a Job wired to the placeholder value must refuse rather than
    # seed the live key. The rotation Job never needs API_KEY for anything else.
    if os.environ.get("API_KEY") == PLACEHOLDER:
        logger.error("API_KEY holds the placeholder; refusing to rotate")
        sys.exit(2)

    try:
        rotate_api_key(store=SecretStore(project), secret_id=secret_id)
    except Exception as exc:  # noqa: BLE001 — log type, never the value
        logger.error("API key rotation failed: %s", type(exc).__name__)
        sys.exit(1)

    logger.info("API key rotation complete; a new version was added")
    sys.exit(0)


if __name__ == "__main__":
    main()
