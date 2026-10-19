"""Structural assertions for the vendor-key roll workflow (ADR-0007).

Replaces the sync-s2-key.yml checks. Parses
``.github/workflows/roll-vendor-keys.yml`` on disk (no network, no Docker) and
fails if the security properties regress:

- dispatchable only, never push/PR/schedule
- one concurrency group per secret, queued runs never cancel a roll
- least privilege: top-level ``contents: read``; only the job escalates
  ``id-token: write`` for WIF; ``environment: staging``
- the workflow NEVER reads a value: no ``versions access``, no GitHub-secret
  copies of vendor keys, no ``GITHUB_OUTPUT``/``GITHUB_ENV`` writes, no tracing
- version numbers are compared as basenames, so the Terraform-seed guard fires
- superseded versions are disabled only when explicitly asked
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_WORKFLOW = (
    Path(__file__).resolve().parents[3] / ".github" / "workflows" / "roll-vendor-keys.yml"
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
    return workflow["jobs"]["roll"]


def _step(workflow: dict, prefix: str) -> dict:
    return next(s for s in _job(workflow)["steps"] if s.get("name", "").startswith(prefix))


def test_is_dispatchable_only(workflow: dict) -> None:
    assert set(_triggers(workflow)) == {"workflow_dispatch"}


def test_secret_choice_is_closed(workflow: dict) -> None:
    secret = _triggers(workflow)["workflow_dispatch"]["inputs"]["secret"]
    assert secret["type"] == "choice"
    assert set(secret["options"]) == {
        "SEMANTIC_SCHOLAR_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    }


def test_rolls_never_overlap(workflow: dict) -> None:
    concurrency = workflow["concurrency"]
    assert concurrency["group"].startswith("roll-vendor-keys-")
    assert concurrency["cancel-in-progress"] is False


def test_least_privilege(workflow: dict) -> None:
    assert workflow["permissions"] == {"contents": "read"}
    assert _job(workflow)["permissions"] == {"contents": "read", "id-token": "write"}
    assert _job(workflow)["environment"] == "staging"


def test_never_reads_or_exports_a_value(text: str) -> None:
    assert "versions access" not in text
    assert "secrets.SEMANTIC_SCHOLAR_API_KEY" not in text
    assert "secrets.OPENAI_API_KEY" not in text
    assert "secrets.ANTHROPIC_API_KEY" not in text
    assert "GITHUB_OUTPUT" not in text
    assert "GITHUB_ENV" not in text
    assert "set -x" not in text


def test_seed_guard_compares_version_numbers(workflow: dict) -> None:
    run = _step(workflow, "Check a real version exists")["run"]
    assert "name.basename()" in run
    assert '"$NEWEST" = "1"' in run


def test_disable_only_on_request(workflow: dict) -> None:
    inputs = _triggers(workflow)["workflow_dispatch"]["inputs"]
    assert inputs["disable_superseded"]["default"] is False
    step = _step(workflow, "Disable superseded versions")
    assert step["if"] == "github.event.inputs.disable_superseded == 'true'"
    assert "name.basename()" in step["run"]


def test_rolls_with_a_label_stamp(workflow: dict) -> None:
    run = _step(workflow, "Roll the ingest Job")["run"]
    assert "--update-secrets=" in run and ":latest" in run
    assert "--update-labels=vendor-key-rolled=" in run
