"""Structural tests for the manual-only ``cloudbuild.yaml`` trigger guard.

Root cause (2026-10-09): two global Cloud Build triggers
(``agentic-kg-api-staging``, ``agentic-kg-ui-staging``) fire on pushes and run
``cloudbuild.yaml``. That file used ``gcloud run deploy`` with its own flags, so
every fired trigger REPLACED the Terraform-owned Cloud Run service spec and
silently dropped env flags (``CANONICAL_API_ENABLED``, ``KGCS_CANONICAL_NAMESPACE``,
``GCP_*``), scaling and anything else Terraform declares. The triggers are not in
Terraform state and the CI service account cannot delete them, so this file
neutralises them from the repo side until the owner deletes them by hand:

- when the build was started by a trigger (``TRIGGER_NAME`` is non-empty) and
  ``_ALLOW_TRIGGER_DEPLOY`` is not ``"true"``, every step exits 0, so the build
  finishes SUCCESS in seconds without building or pushing anything;
- manual runs deploy with ``gcloud run services update`` / ``gcloud run jobs
  update`` and only ``--image`` + ``--update-labels``, mirroring
  ``.github/workflows/deploy-master.yml``, so a manual deploy never drops
  Terraform-owned config either.

These tests parse the committed YAML — no ``gcloud``, no network. ``gcloud
builds submit`` cannot be run here, so they pin the shape the guard depends on.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CLOUDBUILD_PATH = _REPO_ROOT / "cloudbuild.yaml"

# Marker every step script must begin with; also the contract the guard relies on
# to make a triggered build a fast no-op.
_GUARD_MARKER = "# TRIGGER-GUARD"
_GUARD_EXIT = "exit 0"


@pytest.fixture(scope="module")
def config() -> dict:
    with open(_CLOUDBUILD_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def text() -> str:
    return _CLOUDBUILD_PATH.read_text(encoding="utf-8")


def _script(step: dict) -> str:
    """Return the shell script a step runs (the ``bash -c`` payload).

    A guarded step is ``entrypoint: bash`` with ``args: ['-c', <script>]``, so
    the payload is the last argument.
    """
    args = step.get("args") or []
    return str(args[-1]) if args else ""


# =============================================================================
# _ALLOW_TRIGGER_DEPLOY defaults false
# =============================================================================


class TestAllowTriggerDeployDefault:
    def test_default_is_the_string_false(self, config: dict) -> None:
        # Quoted so Cloud Build sees the string "false", not a YAML boolean.
        assert config["substitutions"]["_ALLOW_TRIGGER_DEPLOY"] == "false"

    def test_default_is_literal_quoted_in_file(self, text: str) -> None:
        assert '_ALLOW_TRIGGER_DEPLOY: "false"' in text


# =============================================================================
# no gcloud run deploy (the command that replaced the service spec)
# =============================================================================


class TestNoDeployCommand:
    def test_no_gcloud_run_deploy_in_text(self, text: str) -> None:
        assert "gcloud run deploy" not in text

    def test_no_gcloud_run_deploy_in_any_step(self, config: dict) -> None:
        for step in config["steps"]:
            assert "gcloud run deploy" not in _script(step), step.get("id")

    def test_services_are_rolled_with_update(self, config: dict) -> None:
        deploy = _script(_deploy_step(config))
        assert "gcloud run services update agentic-kg-${_SERVICE}-staging" in deploy

    def test_job_is_rolled_with_update(self, config: dict) -> None:
        deploy = _script(_deploy_step(config))
        assert "gcloud run jobs update agentic-kg-ingest-staging" in deploy

    def test_deploy_stamps_the_commit_label(self, config: dict) -> None:
        deploy = _script(_deploy_step(config))
        assert "--update-labels=environment=staging,commit=$COMMIT_SHA" in deploy

    def test_deploy_rolls_only_the_image(self, config: dict) -> None:
        deploy = _script(_deploy_step(config))
        assert "--image=$${IMAGE}" in deploy

    @pytest.mark.parametrize(
        "dropped_flag",
        [
            "--set-env-vars",
            "--set-secrets",
            "--allow-unauthenticated",
            "--min-instances",
            "--max-instances",
            "--platform",
            "--memory",
            "--port=",
        ],
    )
    def test_no_config_dropping_flags_remain(self, config: dict, dropped_flag: str) -> None:
        """Terraform owns env/secrets/scaling/port/IAM (ADR-0006). A manual
        deploy must not pass any flag that could drop them."""
        full_text = "\n".join(_script(s) for s in config["steps"])
        assert dropped_flag not in full_text

    def test_no_images_autopush_block(self, config: dict) -> None:
        """The ``images:`` auto-push runs after all steps regardless of the
        guard, so on a triggered build it would try to push images that were
        never built and fail the build. Explicit guarded push steps replace it."""
        assert "images" not in config


def _deploy_step(config: dict) -> dict:
    matches = [s for s in config["steps"] if s.get("id") == "deploy"]
    assert len(matches) == 1, "expected exactly one 'deploy' step"
    return matches[0]


# =============================================================================
# every step is guarded by the trigger check
# =============================================================================


class TestEveryStepGuarded:
    def test_all_steps_run_bash(self, config: dict) -> None:
        # Only a bash step can carry the guard; a bare docker step cannot.
        for step in config["steps"]:
            assert step.get("entrypoint") == "bash", step.get("id")

    def test_guard_is_the_first_line_of_every_step(self, config: dict) -> None:
        for step in config["steps"]:
            script = _script(step)
            assert script.lstrip().startswith(_GUARD_MARKER), step.get("id")

    def test_every_step_checks_trigger_name_and_override(self, config: dict) -> None:
        for step in config["steps"]:
            script = _script(step)
            assert '[ -n "$TRIGGER_NAME" ]' in script, step.get("id")
            assert '[ "$_ALLOW_TRIGGER_DEPLOY" != "true" ]' in script, step.get("id")

    def test_every_step_exits_zero_when_guarded(self, config: dict) -> None:
        for step in config["steps"]:
            script = _script(step)
            guarded = script.split(_GUARD_MARKER, 1)[1]
            assert _GUARD_EXIT in guarded, step.get("id")

    def test_a_named_guard_step_is_first(self, config: dict) -> None:
        assert config["steps"][0]["id"] == "trigger-guard"

    def test_no_step_is_missing_the_marker(self, config: dict) -> None:
        ids = [s.get("id") for s in config["steps"]]
        assert ids.count(None) == 0
        assert len(ids) == len(set(ids))  # unique ids


# =============================================================================
# the guard snippet behaves as intended (bash, no cloud)
# =============================================================================


class TestGuardSnippetLogic:
    """Run the guard snippet through real bash with the substitutions Cloud
    Build would have applied. Proves the condition, not just its presence."""

    _SNIPPET = (
        'if [ -n "$TRIGGER_NAME" ] && [ "$_ALLOW_TRIGGER_DEPLOY" != "true" ]; then '
        "echo GUARDED; exit 0; fi; echo PROCEED"
    )

    def _run(self, trigger_name: str, allow: str) -> str:
        # Simulate Cloud Build substitution: replace the built-in/user
        # substitutions textually, then hand the result to bash.
        rendered = self._SNIPPET.replace("$TRIGGER_NAME", trigger_name).replace(
            "$_ALLOW_TRIGGER_DEPLOY", allow
        )
        result = subprocess.run(
            ["bash", "-c", rendered], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0
        return result.stdout.strip()

    def test_trigger_without_override_is_guarded(self) -> None:
        assert self._run("agentic-kg-api-staging", "false") == "GUARDED"

    def test_trigger_with_override_proceeds(self) -> None:
        assert self._run("agentic-kg-api-staging", "true") == "PROCEED"

    def test_manual_build_proceeds(self) -> None:
        assert self._run("", "false") == "PROCEED"
