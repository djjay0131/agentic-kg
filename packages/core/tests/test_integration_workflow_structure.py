"""Structural assertions for ``.github/workflows/integration-tests.yml``.

The E2E job is how the deployed staging product gets verified, but it only
ran on push to master, so nothing exercised it before merge. These tests pin
the shape that makes it both runnable on demand and trustworthy:

- a manual ``workflow_dispatch`` trigger, and dispatch is allowed to reach the
  integration and E2E jobs
- the push-to-master gate is retained for automatic runs
- the E2E step receives ``SEMANTIC_SCHOLAR_API_KEY`` (issue #80, finding 5)
- the E2E step fails loudly when a required staging secret is absent instead
  of skipping to a green job that asserted nothing (issue #80)
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "integration-tests.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    with open(_WORKFLOW_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _triggers(workflow: dict) -> dict:
    """Return the trigger block, handling YAML's ``on`` -> ``True`` key."""
    # ruff: noqa: E712 — comparing to True is intentional here
    return workflow.get("on") or workflow.get(True)


def _if_block(workflow: dict, job: str) -> str:
    """Return a job's ``if:`` condition as a string (handles block scalars)."""
    return str(workflow["jobs"][job].get("if", ""))


def _e2e_step(workflow: dict) -> dict:
    steps = workflow["jobs"]["e2e-tests"]["steps"]
    matches = [s for s in steps if s.get("name") == "Run E2E tests"]
    assert len(matches) == 1, "expected exactly one 'Run E2E tests' step"
    return matches[0]


class TestDispatchTrigger:
    def test_workflow_dispatch_defined(self, workflow):
        assert "workflow_dispatch" in _triggers(workflow)

    def test_run_e2e_input_defaults_true(self, workflow):
        dispatch = _triggers(workflow)["workflow_dispatch"]
        assert dispatch["inputs"]["run_e2e"]["default"] is True

    def test_existing_triggers_unchanged(self, workflow):
        triggers = _triggers(workflow)
        assert "push" in triggers
        assert "pull_request" in triggers


class TestJobConditions:
    def test_integration_job_allows_dispatch(self, workflow):
        assert "workflow_dispatch" in _if_block(workflow, "integration-tests")

    def test_e2e_job_allows_dispatch(self, workflow):
        condition = _if_block(workflow, "e2e-tests")
        assert "workflow_dispatch" in condition
        assert "inputs.run_e2e" in condition

    def test_e2e_job_retains_push_to_master_gate(self, workflow):
        condition = _if_block(workflow, "e2e-tests")
        assert "github.ref == 'refs/heads/master'" in condition


class TestE2EStepSecrets:
    def test_semantic_scholar_key_on_e2e_step(self, workflow):
        env = _e2e_step(workflow)["env"]
        assert "secrets.SEMANTIC_SCHOLAR_API_KEY" in env["SEMANTIC_SCHOLAR_API_KEY"]

    def test_staging_secrets_still_on_e2e_step(self, workflow):
        env = _e2e_step(workflow)["env"]
        for key in (
            "STAGING_API_URL",
            "STAGING_NEO4J_URI",
            "STAGING_NEO4J_PASSWORD",
        ):
            assert f"secrets.{key}" in env[key], key

    def test_secret_presence_guard_fails_loudly(self, workflow):
        run = _e2e_step(workflow)["run"]
        for key in (
            "STAGING_API_URL",
            "STAGING_NEO4J_URI",
            "STAGING_NEO4J_PASSWORD",
            "SEMANTIC_SCHOLAR_API_KEY",
        ):
            assert f'{key}:?' in run, f"{key} has no fail-loud guard"
