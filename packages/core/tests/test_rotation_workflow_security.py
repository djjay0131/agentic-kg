"""Structural assertions for the Neo4j password-rotation workflow (ADR-0006).

Parses ``.github/workflows/rotate-neo4j-password.yml`` on disk (no network,
no Docker) and fails if the security properties regress:

- dispatch-only: never push/PR/schedule
- least privilege: top-level ``contents: read``; only the job escalates
  ``id-token: write`` for WIF
- ``environment: staging``
- WIF auth exactly as the deploy workflows
- the generated password is masked before it is used, and is never echoed,
  written to ``GITHUB_OUTPUT``/``GITHUB_ENV``/the summary, or traced
- the rotation runs inside the VPC (Cloud Run Job) and verifies the new
  credential *before* disabling the superseded secret version
- no dependency on the removed ``STAGING_NEO4J_*`` GitHub secrets
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_WORKFLOW = (
    Path(__file__).resolve().parents[3]
    / ".github"
    / "workflows"
    / "rotate-neo4j-password.yml"
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


def test_is_workflow_dispatch_only(workflow: dict) -> None:
    triggers = _triggers(workflow)
    assert set(triggers) == {"workflow_dispatch"}


def test_permissions_are_minimal(workflow: dict) -> None:
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["rotate"]
    assert job["permissions"] == {"contents": "read", "id-token": "write"}


def test_runs_in_staging_environment(workflow: dict) -> None:
    assert workflow["jobs"]["rotate"]["environment"] == "staging"


def test_wif_auth_matches_deploy_workflows(workflow: dict) -> None:
    steps = workflow["jobs"]["rotate"]["steps"]
    auth = [
        s
        for s in steps
        if s.get("uses", "").startswith("google-github-actions/auth@")
    ]
    assert len(auth) == 1
    with_args = auth[0]["with"]
    assert with_args["workload_identity_provider"] == (
        "${{ secrets.GCP_WORKLOAD_IDENTITY_PROVIDER }}"
    )
    assert with_args["service_account"] == "${{ secrets.GCP_SERVICE_ACCOUNT }}"


def test_password_is_masked_before_use(text: str) -> None:
    mask = text.index("::add-mask::")
    staged = text.index("secrets versions add")
    assert mask < staged, "password must be masked before it is written anywhere"


def test_password_is_never_echoed_or_persisted(text: str) -> None:
    for raw in text.splitlines():
        line = raw.strip()
        if "NEW_PASSWORD" not in line:
            continue
        # The one permitted `echo` is the masking action itself.
        if re.search(r"\becho\b", line):
            assert "::add-mask::" in line, f"password echoed: {line}"
        # Never persist it into a file GitHub uploads or exposes.
        assert not re.search(r"GITHUB_(OUTPUT|ENV|STEP_SUMMARY)", line), line
    assert not re.search(r"\bset -x\b", text), "shell tracing can leak the value"


def test_rotates_inside_the_vpc_via_a_cloud_run_job(text: str) -> None:
    # After ADR-0006 Neo4j is private; the runner cannot reach it directly.
    assert "gcloud run jobs execute" in text
    assert "agentic-kg-rotate-neo4j-" in text


def test_verifies_before_disabling_the_old_version(text: str) -> None:
    verify = text.index("Verify the API reads the graph")
    disable = text.index("Disable the superseded secret version")
    destroy = text.index("Destroy the transport version")
    assert verify < disable < destroy


def test_does_not_depend_on_removed_github_secrets(text: str) -> None:
    assert "STAGING_NEO4J_URI" not in text
    assert "STAGING_NEO4J_PASSWORD" not in text
