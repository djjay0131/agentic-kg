"""Structural assertions for the API-key rotation workflow (ADR-0007, R-3 AC-6).

Parses ``.github/workflows/rotate-api-key.yml`` on disk (no network, no Docker)
and fails if the security properties regress:

- scheduled monthly + dispatchable, but never push/PR
- a single concurrency group so two rotations never overlap
- least privilege: top-level ``contents: read``; only the job escalates
  ``id-token: write`` for WIF; ``environment: staging``
- the workflow never generates, reads, pipes or prints a key: no
  ``versions access``, no ``versions add``, no add-mask, no
  ``GITHUB_OUTPUT``/``GITHUB_ENV``, no shell tracing
- both Jobs are pinned to the ingest Job image and run the in-GCP entrypoints
- the check Job runs after the roll and BEFORE superseded versions are disabled
- version numbers are compared as basenames (metadata only)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_WORKFLOW = (
    Path(__file__).resolve().parents[3] / ".github" / "workflows" / "rotate-api-key.yml"
)


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def text() -> str:
    return _WORKFLOW.read_text(encoding="utf-8")


def _triggers(workflow: dict) -> dict:
    # ruff: noqa: E712 — YAML parses the bare `on:` key as True
    return workflow.get("on") or workflow.get(True)


def _job(workflow: dict) -> dict:
    return workflow["jobs"]["rotate"]


def test_is_scheduled_monthly_and_dispatchable(workflow: dict) -> None:
    triggers = _triggers(workflow)
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    schedule = triggers["schedule"]
    assert isinstance(schedule, list) and len(schedule) == 1
    # Monthly, 04:23 UTC — deliberately off the hour.
    assert schedule[0]["cron"] == "23 4 1 * *"


def test_rotations_never_overlap(workflow: dict) -> None:
    concurrency = workflow["concurrency"]
    assert concurrency["group"] == "rotate-api-key"
    assert concurrency["cancel-in-progress"] is False


def test_permissions_are_minimal(workflow: dict) -> None:
    assert workflow["permissions"] == {"contents": "read"}
    assert _job(workflow)["permissions"] == {"contents": "read", "id-token": "write"}


def test_runs_in_staging_environment(workflow: dict) -> None:
    assert _job(workflow)["environment"] == "staging"


def test_wif_auth_matches_deploy_workflows(workflow: dict) -> None:
    auth = [
        s
        for s in _job(workflow)["steps"]
        if s.get("uses", "").startswith("google-github-actions/auth@")
    ]
    assert len(auth) == 1
    assert auth[0]["with"]["workload_identity_provider"] == (
        "${{ secrets.GCP_WORKLOAD_IDENTITY_PROVIDER }}"
    )
    assert auth[0]["with"]["service_account"] == "${{ secrets.GCP_SERVICE_ACCOUNT }}"


def test_never_generates_reads_or_pipes_a_key(text: str) -> None:
    # Inspect executable lines only; prose may *name* the forbidden operations.
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    assert "token_urlsafe" not in code
    assert "versions access" not in code
    assert "versions add" not in code
    assert "::add-mask::" not in code
    assert "GITHUB_OUTPUT" not in code
    assert "GITHUB_ENV" not in code
    assert not re.search(r"\bset -x\b", code)
    # Never ask gcloud for a payload or a decoded value.
    assert "payload" not in code
    assert ":access" not in code


def test_pins_both_jobs_and_their_in_gcp_entrypoints(text: str) -> None:
    assert 'gcloud run jobs describe "agentic-kg-ingest-$ENVIRONMENT"' in text
    assert "agentic-kg-rotate-api-key-$ENVIRONMENT" in text
    assert "agentic-kg-check-api-key-$ENVIRONMENT" in text
    assert "--args=-m,agentic_kg.rotate_api_key_gcp" in text
    assert "--args=-m,agentic_kg.check_api_key" in text


def test_rolls_the_api_with_a_label_stamp(text: str) -> None:
    assert "--update-secrets=API_KEY=API_KEY:latest" in text
    assert "--update-labels=api-key-rotated=${{ github.run_id }}" in text


def test_check_runs_after_the_roll_and_before_disabling(text: str) -> None:
    rotate = text.index("gcloud run jobs execute \"agentic-kg-rotate-api-key")
    roll = text.index("gcloud run services update \"agentic-kg-api")
    check = text.index("gcloud run jobs execute \"agentic-kg-check-api-key")
    disable = text.index("Disable the superseded secret versions")
    assert rotate < roll < check < disable


def test_disables_by_version_basename_only(text: str) -> None:
    assert "name.basename()" in text
    assert "gcloud secrets versions disable" in text
