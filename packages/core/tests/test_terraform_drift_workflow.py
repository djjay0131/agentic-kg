"""Structural + dry-run tests for the daily Terraform drift workflow.

Parses ``.github/workflows/terraform-drift.yml`` on disk (no network, no
Docker, no Terraform) and fails if the drift-detection contract regresses:

- scheduled daily + dispatchable, never push/PR
- least privilege: ``contents: read`` plus ``id-token: write`` (WIF) and
  ``issues: write`` (the ``infra-drift`` issue)
- the same WIF auth / GCS-backend init as ``terraform.yml``
- ``plan`` runs with ``-lock=false -detailed-exitcode -input=false -no-color``
  and the staging var-file, and NEVER applies
- exit 0 closes the issue, exit 2 files/updates it, exit 1 fails the job

It also runs the committed jq summarizer dry-run
(``scripts/test_terraform_drift_summary.sh``) so the extraction the workflow
depends on is actually exercised in CI.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "terraform-drift.yml"
_SUMMARY_TEST = _REPO_ROOT / "scripts" / "test_terraform_drift_summary.sh"


@pytest.fixture(scope="module")
def workflow() -> dict:
    with open(_WORKFLOW_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def text() -> str:
    return _WORKFLOW_PATH.read_text(encoding="utf-8")


def _triggers(workflow: dict) -> dict:
    # YAML parses the bare `on:` key as Python True.
    # ruff: noqa: E712 — comparing to True is intentional here
    return workflow.get("on") or workflow.get(True)


def _step(workflow: dict, name: str) -> dict:
    steps = workflow["jobs"]["drift"]["steps"]
    matches = [s for s in steps if s.get("name") == name]
    assert len(matches) == 1, f"expected exactly one step named {name!r}"
    return matches[0]


def test_is_scheduled_and_dispatchable(workflow: dict) -> None:
    triggers = _triggers(workflow)
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    schedule = triggers["schedule"]
    assert isinstance(schedule, list) and len(schedule) == 1
    assert schedule[0]["cron"] == "23 11 * * *"


def test_permissions_are_minimal(workflow: dict) -> None:
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["drift"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
        "issues": "write",
    }


def test_wif_auth_matches_deploy_workflows(workflow: dict) -> None:
    auth = _step(workflow, "Authenticate to Google Cloud")
    assert auth["uses"] == "google-github-actions/auth@v2"
    assert auth["with"]["workload_identity_provider"] == (
        "${{ secrets.GCP_WORKLOAD_IDENTITY_PROVIDER }}"
    )
    assert auth["with"]["service_account"] == "${{ secrets.GCP_SERVICE_ACCOUNT }}"


def test_init_uses_the_staging_backend_prefix(workflow: dict) -> None:
    init = _step(workflow, "Terraform init")["run"]
    assert 'terraform init -backend-config="prefix=agentic-kg/$TF_ENV"' in init
    assert workflow["env"]["TF_ENV"] == "staging"


def test_plan_detects_drift_without_locking_or_prompting(workflow: dict) -> None:
    run = _step(workflow, "Terraform plan (drift check)")["run"]
    assert "-detailed-exitcode" in run
    assert "-lock=false" in run
    assert "-input=false" in run
    assert "-no-color" in run
    assert '-var-file="envs/$TF_ENV.tfvars"' in run


def test_never_applies(workflow: dict, text: str) -> None:
    assert "terraform apply" not in text
    assert "apply" not in workflow["jobs"]


def test_exit_codes_route_to_the_right_actions(workflow: dict) -> None:
    file_step = _step(workflow, "File or update the drift issue")
    close_step = _step(workflow, "Close the drift issue (no drift)")
    assert file_step["if"] == "steps.plan.outputs.exitcode == '2'"
    assert close_step["if"] == "steps.plan.outputs.exitcode == '0'"
    # Exit 1 (or anything else) fails the plan step itself.
    assert "exit 1" in _step(workflow, "Terraform plan (drift check)")["run"]


def test_issue_is_single_titled_and_labelled(workflow: dict, text: str) -> None:
    assert '"Staging infra drift detected"' in text
    assert "infra-drift" in text
    # The same fixed title is used by both the create and the edit branch, so
    # there is only ever one canonical tracking issue.
    assert text.count('--title "Staging infra drift detected"') == 2
    # Both branches list existing open issues so exactly one stays open.
    assert '--state open' in text
    assert "gh issue create" in text
    assert "gh issue edit" in text
    assert "gh issue close" in text


def test_summary_uses_the_tested_jq_filter(workflow: dict, text: str) -> None:
    assert "scripts/terraform_drift_summary.jq" in text
    assert "terraform show -json tfplan" in text
    # The issue body must never hand jq a `before`/`after` projection.
    assert ".change.before" not in text
    assert ".change.after" not in text


def test_summarizer_dry_run_passes() -> None:
    """Exercise the same jq extraction the workflow uses, against the fixture."""
    result = subprocess.run(
        ["bash", str(_SUMMARY_TEST)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout
