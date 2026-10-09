"""Structural assertions for the Semantic Scholar key-sync workflow (P0-4).

Parses ``.github/workflows/sync-s2-key.yml`` on disk (no network, no Docker)
and fails if the security properties regress:

- dispatchable only, never push/PR/schedule
- a single concurrency group so two syncs never overlap
- least privilege: top-level ``contents: read``; only the job escalates
  ``id-token: write`` for WIF
- ``environment: staging``
- WIF auth exactly as the deploy workflows
- the key is streamed from stdin (never argv), masked before it is used, and
  is never echoed, written to ``GITHUB_OUTPUT``/``GITHUB_ENV``/the summary, or
  traced
- the ingest Job is rolled onto ``latest`` with a label stamp, and the
  superseded versions are disabled only after the new one is written
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
    / "sync-s2-key.yml"
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


def test_is_dispatchable_only(workflow: dict) -> None:
    """Hand-run only; the key is a value rotation, not a build trigger."""
    triggers = _triggers(workflow)
    assert set(triggers) == {"workflow_dispatch"}


def test_syncs_never_overlap(workflow: dict) -> None:
    concurrency = workflow["concurrency"]
    assert concurrency["group"] == "sync-s2-key"
    assert concurrency["cancel-in-progress"] is False


def test_permissions_are_minimal(workflow: dict) -> None:
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["sync"]
    assert job["permissions"] == {"contents": "read", "id-token": "write"}


def test_runs_in_staging_environment(workflow: dict) -> None:
    assert workflow["jobs"]["sync"]["environment"] == "staging"


def test_wif_auth_matches_deploy_workflows(workflow: dict) -> None:
    steps = workflow["jobs"]["sync"]["steps"]
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


def test_key_is_masked_before_use(text: str) -> None:
    mask = text.index("::add-mask::")
    staged = text.index("secrets versions add")
    assert mask < staged, "key must be masked before it is written anywhere"


def test_key_is_streamed_from_stdin_not_argv(text: str) -> None:
    """``--data-file=-`` and a ``printf |`` pipe; the key is never an argument
    (argv is visible in the process table and can leak into logs)."""
    assert "printf '%s' \"$S2_API_KEY\" | gcloud secrets versions add" in text
    assert "--data-file=-" in text
    assert not re.search(r'--data-file="?\$S2_API_KEY', text)


def test_key_is_never_echoed_or_persisted(text: str) -> None:
    for raw in text.splitlines():
        line = raw.strip()
        if "S2_API_KEY" not in line:
            continue
        # The one permitted `echo` is the masking action itself.
        if re.search(r"\becho\b", line):
            assert "::add-mask::" in line, f"key echoed: {line}"
        # Never persist it into a file GitHub uploads or exposes.
        assert not re.search(r"GITHUB_(OUTPUT|ENV|STEP_SUMMARY)", line), line
    assert not re.search(r"\bset -x\b", text), "shell tracing can leak the value"


def test_reads_the_github_secret_into_the_shell_env(workflow: dict) -> None:
    steps = workflow["jobs"]["sync"]["steps"]
    add = [s for s in steps if "secrets versions add" in s.get("run", "")]
    assert len(add) == 1
    assert add[0]["env"]["S2_API_KEY"] == (
        "${{ secrets.SEMANTIC_SCHOLAR_API_KEY }}"
    )


def test_rolls_the_ingest_job_with_a_label_stamp(text: str) -> None:
    assert "gcloud run jobs update" in text
    assert "agentic-kg-ingest-$ENVIRONMENT" in text
    assert (
        "--update-secrets=SEMANTIC_SCHOLAR_API_KEY="
        "SEMANTIC_SCHOLAR_API_KEY:latest" in text
    )
    assert re.search(r"--update-labels=[a-z0-9_-]+=\$\{\{ github\.run_id \}\}", text)


def test_disables_superseded_versions_after_writing(text: str) -> None:
    write = text.index("secrets versions add")
    roll = text.index("Roll the ingest Job")
    disable = text.index("Disable superseded secret versions")
    assert write < roll < disable


def test_does_not_depend_on_removed_github_secrets(text: str) -> None:
    assert "STAGING_NEO4J_URI" not in text
    assert "STAGING_NEO4J_PASSWORD" not in text
