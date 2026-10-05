"""Structural assertions for ``.github/workflows/integration-tests.yml``.

As reconciled for ADR-0006 (#105) on top of #103: staging Neo4j is
VPC-private, so a GitHub-hosted runner can no longer reach it. The E2E job
therefore exercises the deployed public API and the public live data sources,
and never receives direct-database credentials. The core e2e tests that assert
on graph contents are marked ``requires_db`` and run only where the database is
reachable.

These tests pin the shape that makes the workflow both runnable on demand and
trustworthy:

- a manual ``workflow_dispatch`` trigger, and dispatch is allowed to reach the
  integration and E2E jobs
- the push-to-master gate is retained for automatic runs
- the integration job runs against testcontainers, with no Neo4j secret
- the E2E job targets the deployed API (``STAGING_API_URL``) and the live
  sources (step-scoped ``SEMANTIC_SCHOLAR_API_KEY``), and passes **no**
  ``STAGING_NEO4J_*`` secret anywhere
- the graph-content e2e tests are explicitly deselected by ``requires_db``
- the E2E steps fail loudly when a required secret is absent instead of
  skipping to a green job that asserted nothing (issue #80)
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "integration-tests.yml"

_API_STEP = "Run API E2E tests"
_LIVE_STEP = "Run live-source E2E tests"


@pytest.fixture(scope="module")
def workflow() -> dict:
    with open(_WORKFLOW_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def workflow_text() -> str:
    return _WORKFLOW_PATH.read_text(encoding="utf-8")


def _triggers(workflow: dict) -> dict:
    """Return the trigger block, handling YAML's ``on`` -> ``True`` key."""
    # ruff: noqa: E712 — comparing to True is intentional here
    return workflow.get("on") or workflow.get(True)


def _if_block(workflow: dict, job: str) -> str:
    """Return a job's ``if:`` condition as a string (handles block scalars)."""
    return str(workflow["jobs"][job].get("if", ""))


def _step(workflow: dict, name: str) -> dict:
    steps = workflow["jobs"]["e2e-tests"]["steps"]
    matches = [s for s in steps if s.get("name") == name]
    assert len(matches) == 1, f"expected exactly one '{name}' step"
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


class TestNoDirectDatabaseAccess:
    def test_no_staging_neo4j_secret_anywhere(self, workflow_text):
        """ADR-0006: the duplicated GitHub DB secrets were deleted, and the
        runners must not regain direct access to the private database."""
        assert "STAGING_NEO4J_URI" not in workflow_text
        assert "STAGING_NEO4J_PASSWORD" not in workflow_text
        assert "STAGING_NEO4J_USER" not in workflow_text

    def test_e2e_steps_receive_no_db_credentials(self, workflow):
        for name in (_API_STEP, _LIVE_STEP):
            env = _step(workflow, name).get("env", {})
            assert not any("STAGING_NEO4J" in key for key in env), name


class TestE2EStepShape:
    def test_api_step_targets_deployed_api(self, workflow):
        assert "packages/api/tests/e2e" in _step(workflow, _API_STEP)["run"]

    def test_live_source_step_targets_core_public_tests(self, workflow):
        run = _step(workflow, _LIVE_STEP)["run"]
        assert "packages/core/tests/e2e" in run
        # Explicit deselection of the DB-backed tests, with the reason in the
        # workflow comment (they need an environment that can reach Neo4j).
        assert "not requires_db" in run
        assert "not costly" in run


class TestE2EStepSecrets:
    def test_api_step_has_staging_api_url(self, workflow):
        env = _step(workflow, _API_STEP)["env"]
        assert "secrets.STAGING_API_URL" in env["STAGING_API_URL"]

    def test_live_source_step_has_step_scoped_s2_key(self, workflow):
        env = _step(workflow, _LIVE_STEP)["env"]
        assert "secrets.SEMANTIC_SCHOLAR_API_KEY" in env["SEMANTIC_SCHOLAR_API_KEY"]

    def test_s2_key_is_not_job_wide(self, workflow):
        """#80: the S2 key lives on the live-source step only, so checkout /
        install / artifact steps never hold it."""
        job_env = workflow["jobs"]["e2e-tests"].get("env", {})
        assert "SEMANTIC_SCHOLAR_API_KEY" not in job_env

    def test_secret_presence_guard_fails_loudly(self, workflow):
        api_run = _step(workflow, _API_STEP)["run"]
        assert "STAGING_API_URL:?" in api_run
        live_run = _step(workflow, _LIVE_STEP)["run"]
        assert "STAGING_API_URL:?" in live_run
        assert "SEMANTIC_SCHOLAR_API_KEY:?" in live_run
