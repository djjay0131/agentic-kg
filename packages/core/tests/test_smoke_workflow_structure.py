"""Structural assertions on ``.github/workflows/smoke-ingest.yml``.

Parses the workflow YAML and verifies each acceptance criterion that
constrains the workflow's shape. This is the standard pattern for
testing CI config without firing the actual workflow.

Covers AC-1 (triggers + timeout), AC-2 (path filter), AC-3 (Neo4j
service + schema init), AC-4 (ingest invocation), AC-5 (single retry),
AC-9 (artifact upload), AC-11 (dispatch inputs), AC-12 (env block),
AC-13 (workflow name + doesn't touch other workflows), AC-14
(concurrency group).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "smoke-ingest.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    """Parse the workflow YAML once per module."""
    with open(_WORKFLOW_PATH) as f:
        # YAML's boolean coercion turns the top-level ``on`` key into
        # Python ``True``; we don't need to fight it here — the test
        # helpers below normalize.
        return yaml.safe_load(f)


def _triggers(workflow: dict) -> dict:
    """Return the workflow's trigger block, handling YAML's ``on`` key
    getting parsed as Python ``True`` on some PyYAML versions."""
    # ruff: noqa: E712 — comparing to True is intentional here
    return workflow.get("on") or workflow.get(True)


def _smoke_job(workflow: dict) -> dict:
    """Return the single ``smoke`` job block."""
    return workflow["jobs"]["smoke"]


def _step_by_name(workflow: dict, name: str) -> dict:
    """Return the workflow step whose ``name`` field matches ``name``."""
    steps = _smoke_job(workflow)["steps"]
    matches = [s for s in steps if s.get("name") == name]
    assert len(matches) == 1, (
        f"expected exactly one step named {name!r}; found {len(matches)}"
    )
    return matches[0]


# =============================================================================
# AC-1: workflow file exists, name, triggers, timeout
# =============================================================================


class TestWorkflowIdentity:
    def test_workflow_file_exists(self):
        assert _WORKFLOW_PATH.exists()

    def test_workflow_name_matches_ac13(self, workflow):
        # AC-13: distinguishable name for the PR check label.
        assert workflow["name"] == "Smoke Test — Ingest"

    def test_all_three_triggers_defined(self, workflow):
        # AC-1: workflow_dispatch + pull_request + schedule.
        triggers = _triggers(workflow)
        assert "workflow_dispatch" in triggers
        assert "pull_request" in triggers
        assert "schedule" in triggers

    def test_cron_schedule_matches_spec(self, workflow):
        triggers = _triggers(workflow)
        schedule = triggers["schedule"]
        assert len(schedule) == 1
        # 06:17 UTC daily — off-peak deterministic minute per spec.
        assert schedule[0]["cron"] == "17 6 * * *"

    def test_timeout_at_most_15_minutes(self, workflow):
        assert _smoke_job(workflow)["timeout-minutes"] <= 15


# =============================================================================
# AC-2: path filter scope
# =============================================================================


class TestPathFilter:
    def test_pull_request_targets_master(self, workflow):
        triggers = _triggers(workflow)
        pr = triggers["pull_request"]
        assert pr["branches"] == ["master"]

    def test_path_filter_includes_all_documented_paths(self, workflow):
        triggers = _triggers(workflow)
        paths = triggers["pull_request"]["paths"]
        expected = {
            "packages/core/**",
            "pyproject.toml",
            ".github/workflows/smoke-ingest.yml",
            "scripts/smoke_assert.py",
        }
        assert set(paths) == expected

    def test_no_master_push_trigger(self, workflow):
        """Per spec Non-Goals: no master push trigger; PR + cron cover it."""
        triggers = _triggers(workflow)
        assert "push" not in triggers


# =============================================================================
# AC-3: Neo4j service + schema init
# =============================================================================


class TestNeo4jService:
    def test_service_uses_5_26_community(self, workflow):
        neo4j = _smoke_job(workflow)["services"]["neo4j"]
        assert neo4j["image"] == "neo4j:5.26-community"

    def test_apoc_plugin_enabled(self, workflow):
        neo4j = _smoke_job(workflow)["services"]["neo4j"]
        assert "apoc" in neo4j["env"]["NEO4J_PLUGINS"]

    def test_ports_exposed(self, workflow):
        neo4j = _smoke_job(workflow)["services"]["neo4j"]
        ports = neo4j["ports"]
        assert "7687:7687" in ports
        assert "7474:7474" in ports

    def test_healthcheck_configured(self, workflow):
        neo4j = _smoke_job(workflow)["services"]["neo4j"]
        options = neo4j["options"]
        assert "--health-cmd" in options
        assert "--health-interval" in options
        assert "--health-retries" in options

    def test_schema_init_step_exists(self, workflow):
        step = _step_by_name(workflow, "Initialize Neo4j schema")
        # AC-3: calls initialize_schema(force=True).
        assert "initialize_schema" in step["run"]
        assert "force=True" in step["run"]


# =============================================================================
# AC-4: ingest invocation
# =============================================================================


class TestIngestStep:
    def test_ingest_step_present(self, workflow):
        _step_by_name(workflow, "Ingest (with single retry)")

    def test_ingest_uses_default_on_flags(self, workflow):
        run = _step_by_name(workflow, "Ingest (with single retry)")["run"]
        # AC-4: no opt-out flags — default extract_entities=True and
        # normalize_cross_entity_collisions=True apply.
        assert "--no-extract-entities" not in run
        assert "--no-normalize-cross-entity" not in run

    def test_ingest_writes_json(self, workflow):
        run = _step_by_name(workflow, "Ingest (with single retry)")["run"]
        assert "--json" in run
        # Per-attempt file + final artifact.
        assert "ingest_result_" in run
        assert "ingest_result.json" in run

    def test_query_default_from_spec(self, workflow):
        run = _step_by_name(workflow, "Ingest (with single retry)")["run"]
        assert "retrieval augmented generation" in run

    def test_limit_default_is_three(self, workflow):
        run = _step_by_name(workflow, "Ingest (with single retry)")["run"]
        # LIMIT default fallback in the shell parameter expansion.
        assert "3" in run


# =============================================================================
# AC-5: single-retry mechanism
# =============================================================================


class TestRetryLoop:
    def test_max_attempts_is_two(self, workflow):
        run = _step_by_name(workflow, "Ingest (with single retry)")["run"]
        assert "MAX=2" in run

    def test_sleep_30_between_attempts(self, workflow):
        run = _step_by_name(workflow, "Ingest (with single retry)")["run"]
        assert "sleep 30" in run

    def test_exit_1_on_final_failure(self, workflow):
        run = _step_by_name(workflow, "Ingest (with single retry)")["run"]
        assert "exit 1" in run

    def test_exit_0_on_success(self, workflow):
        run = _step_by_name(workflow, "Ingest (with single retry)")["run"]
        assert "exit 0" in run

    def test_final_artifact_file_populated(self, workflow):
        """Per AC-5 + AC-9: even on both-attempts-failure, ingest_result.json
        should exist so the assertion / artifact steps can inspect it."""
        run = _step_by_name(workflow, "Ingest (with single retry)")["run"]
        assert "cp \"ingest_result_2.json\" ingest_result.json" in run


# =============================================================================
# AC-9: artifact upload
# =============================================================================


class TestArtifactUpload:
    def test_upload_step_present(self, workflow):
        step = _step_by_name(workflow, "Upload artifact")
        assert step["uses"].startswith("actions/upload-artifact@")

    def test_upload_always_runs(self, workflow):
        step = _step_by_name(workflow, "Upload artifact")
        assert step["if"] == "always()"

    def test_retention_14_days(self, workflow):
        step = _step_by_name(workflow, "Upload artifact")
        assert step["with"]["retention-days"] == 14

    def test_uploads_ingest_result_files(self, workflow):
        step = _step_by_name(workflow, "Upload artifact")
        assert "ingest_result*.json" in step["with"]["path"]

    def test_artifact_name_uses_run_id(self, workflow):
        step = _step_by_name(workflow, "Upload artifact")
        assert "github.run_id" in step["with"]["name"]


# =============================================================================
# AC-11: workflow_dispatch inputs
# =============================================================================


class TestDispatchInputs:
    def test_query_input_default(self, workflow):
        dispatch = _triggers(workflow)["workflow_dispatch"]
        assert dispatch["inputs"]["query"]["default"] == (
            "retrieval augmented generation"
        )

    def test_limit_input_default(self, workflow):
        dispatch = _triggers(workflow)["workflow_dispatch"]
        assert dispatch["inputs"]["limit"]["default"] == "3"


# =============================================================================
# AC-12: env block, no GCP secrets
# =============================================================================


class TestEnvBlock:
    def test_env_lists_required_keys(self, workflow):
        env = _smoke_job(workflow)["env"]
        assert set(env) == {
            "OPENAI_API_KEY",
            "NEO4J_URI",
            "NEO4J_USERNAME",
            "NEO4J_PASSWORD",
            "NEO4J_DATABASE",
        }

    def test_openai_key_from_secret(self, workflow):
        env = _smoke_job(workflow)["env"]
        assert "secrets.OPENAI_API_KEY" in env["OPENAI_API_KEY"]

    def test_neo4j_uri_is_localhost(self, workflow):
        """AC-12: NEO4J_URI always points at localhost via the service
        container. No staging traffic."""
        env = _smoke_job(workflow)["env"]
        assert env["NEO4J_URI"] == "bolt://localhost:7687"

    def test_no_gcp_secrets_referenced(self, workflow):
        """No WIF auth, no GCP-scoped secrets."""
        text = _WORKFLOW_PATH.read_text()
        # Guard against accidental copy-paste of GCP integration tokens.
        assert "GCP_" not in text
        assert "workload_identity_provider" not in text
        assert "GOOGLE_APPLICATION_CREDENTIALS" not in text


# =============================================================================
# AC-13: existing workflows untouched
# =============================================================================


class TestExistingWorkflowsUntouched:
    def test_smoke_workflow_stands_alone(self):
        """Sanity check that this file is the only smoke workflow."""
        workflows_dir = _REPO_ROOT / ".github" / "workflows"
        smoke_files = [
            p for p in workflows_dir.iterdir()
            if "smoke" in p.name.lower()
        ]
        assert len(smoke_files) == 1
        assert smoke_files[0].name == "smoke-ingest.yml"


# =============================================================================
# AC-14: concurrency group
# =============================================================================


class TestConcurrency:
    def test_concurrency_group_configured(self, workflow):
        assert "concurrency" in workflow
        conc = workflow["concurrency"]
        assert "smoke-ingest" in conc["group"]
        assert "github.ref" in conc["group"]

    def test_cancel_in_progress_true(self, workflow):
        assert workflow["concurrency"]["cancel-in-progress"] is True


# =============================================================================
# Step ordering — contract that assert runs AFTER ingest AFTER init.
# =============================================================================


class TestStepOrdering:
    def _step_index(self, workflow: dict, name: str) -> int:
        steps = _smoke_job(workflow)["steps"]
        for i, s in enumerate(steps):
            if s.get("name") == name:
                return i
        raise AssertionError(f"step {name!r} not in workflow")

    def test_schema_init_before_ingest(self, workflow):
        assert self._step_index(workflow, "Initialize Neo4j schema") \
            < self._step_index(workflow, "Ingest (with single retry)")

    def test_ingest_before_assert(self, workflow):
        assert self._step_index(workflow, "Ingest (with single retry)") \
            < self._step_index(workflow, "Assert graph shape")

    def test_upload_artifact_is_last(self, workflow):
        """Artifact upload runs last so it captures state from all
        preceding steps (pass or fail via ``if: always()``)."""
        steps = _smoke_job(workflow)["steps"]
        assert steps[-1]["name"] == "Upload artifact"
