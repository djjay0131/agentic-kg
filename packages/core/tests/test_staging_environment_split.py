"""Structural assertions for the staging/staging-infra environment split.

Routine staging deploys must not wait on a human, while infrastructure and
secret changes stay gated. ``staging`` is the un-gated (or auto-deploy)
environment; ``staging-infra`` is the one with a required reviewer.

Parses the workflow YAML on disk (no network, no Docker) and pins:

- exactly the privileged workflows run in ``staging-infra``: the Terraform
  ``apply`` job and the three key/credential rotation workflows
- no other workflow runs in ``staging-infra``
- routine staging workflows stay on ``staging`` and the release deploy stays
  on ``production``
- the Terraform dispatch ``environment`` input remains the *Terraform target*
  (state prefix + tfvars), not the GitHub approval environment
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_WORKFLOWS_DIR = Path(__file__).resolve().parents[3] / ".github" / "workflows"

_PRIVILEGED = {
    "terraform.yml": ("apply",),
    "roll-vendor-keys.yml": ("roll",),
    "rotate-api-key.yml": ("rotate",),
    "rotate-neo4j-password.yml": ("rotate",),
}


def _load(name: str) -> dict:
    return yaml.safe_load((_WORKFLOWS_DIR / name).read_text(encoding="utf-8"))


def _all_workflows() -> list[Path]:
    return sorted(_WORKFLOWS_DIR.glob("*.yml"))


def _triggers(workflow: dict) -> dict:
    # ruff: noqa: E712 — YAML parses the bare ``on:`` key as True
    return workflow.get("on") or workflow.get(True)


def _environment_name(job: dict) -> str | None:
    environment = job.get("environment")
    if isinstance(environment, dict):
        return environment.get("name")
    return environment


def _job_environments(workflow: dict) -> dict[str, str | None]:
    return {
        name: _environment_name(job) for name, job in workflow["jobs"].items()
    }


def _jobs_using(environment: str) -> dict[str, list[str]]:
    """Map workflow filename -> job names that declare ``environment``."""
    users: dict[str, list[str]] = {}
    for path in _all_workflows():
        jobs = [
            name
            for name, value in _job_environments(_load(path.name)).items()
            if value == environment
        ]
        if jobs:
            users[path.name] = jobs
    return users


@pytest.mark.parametrize(
    ("workflow_name", "job_name"),
    [(name, job) for name, jobs in _PRIVILEGED.items() for job in jobs],
)
def test_privileged_job_runs_in_staging_infra(
    workflow_name: str, job_name: str
) -> None:
    workflow = _load(workflow_name)
    assert _environment_name(workflow["jobs"][job_name]) == "staging-infra"


def test_only_privileged_workflows_use_staging_infra() -> None:
    expected = {
        name: list(jobs) for name, jobs in _PRIVILEGED.items()
    }
    assert _jobs_using("staging-infra") == expected


def test_terraform_plan_is_not_gated() -> None:
    """A PR plan is read-only; only ``apply`` needs the reviewer."""
    assert _environment_name(_load("terraform.yml")["jobs"]["plan"]) is None


def test_terraform_dispatch_input_stays_the_target_environment() -> None:
    """The ``environment`` input selects the state prefix and var-file. It must
    stay ``staging``; the approval comes from the job's ``staging-infra``
    environment, not from this input."""
    workflow = _load("terraform.yml")
    dispatch = _triggers(workflow)["workflow_dispatch"]
    environment_input = dispatch["inputs"]["environment"]
    assert environment_input["options"] == ["staging"]
    assert environment_input["default"] == "staging"
    assert workflow["env"]["TF_ENV"] == (
        "${{ github.event.inputs.environment || 'staging' }}"
    )


def test_routine_staging_workflows_stay_on_staging() -> None:
    assert _environment_name(_load("deploy-master.yml")["jobs"]["deploy-staging"]) == (
        "staging"
    )
    assert _environment_name(_load("nightly-run.yml")["jobs"]["run"]) == "staging"
    assert _environment_name(_load("gcp-diagnose.yml")["jobs"]["diagnose"]) == "staging"


def test_deploy_branch_stays_on_the_selected_environment() -> None:
    workflow = _load("deploy-branch.yml")
    assert _environment_name(workflow["jobs"]["deploy"]) == (
        "${{ inputs.environment }}"
    )
    options = _triggers(workflow)["workflow_dispatch"]["inputs"]["environment"]["options"]
    assert "staging" in options
    assert "staging-infra" not in options


def test_release_deploy_stays_on_production() -> None:
    assert _environment_name(
        _load("deploy-tag.yml")["jobs"]["deploy-production"]
    ) == "production"
