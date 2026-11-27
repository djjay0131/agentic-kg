"""Structural assertions for the post-deploy UI suite (T54).

The live suite only runs in CI against staging, so these offline checks pin the
parts that make it trustworthy:

* it is automatic after a deploy, and runnable on demand;
* it authenticates with the existing Workload Identity Federation login and a
  short-lived service-account JWT — there is no stored key or password;
* it fails on suite failure and always uploads the HTML report and traces;
* it skips with a notice (not a failure) until the dedicated test identity
  exists, so applying the Terraform is the only owner action;
* the Playwright suite itself lives under ``e2e/ui``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"
_DEPLOY = _WORKFLOWS / "deploy-master.yml"
_ON_DEMAND = _WORKFLOWS / "ui-tests.yml"
_SUITE = _REPO_ROOT / "e2e" / "ui"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    # YAML turns the top-level ``on`` key into True on some PyYAML versions.
    return workflow.get("on") or workflow.get(True)


def _steps(job: dict) -> list[dict]:
    return job["steps"]


def _step(job: dict, name: str) -> dict:
    matches = [step for step in _steps(job) if step.get("name") == name]
    assert len(matches) == 1, f"expected exactly one step named {name!r}"
    return matches[0]


def test_suite_lives_under_e2e_ui() -> None:
    assert (_SUITE / "playwright.config.ts").is_file()
    assert (_SUITE / "package.json").is_file()
    assert list((_SUITE / "tests").glob("*.spec.ts"))


def test_deploy_master_runs_ui_tests_after_the_deploy_job() -> None:
    workflow = _load(_DEPLOY)
    job = workflow["jobs"]["post-deploy-ui-tests"]
    assert "deploy-staging" in job["needs"]
    assert job["if"] == "needs.changes.outputs.any_service == 'true'"


def test_deploy_master_skips_cleanly_until_the_identity_exists() -> None:
    job = _load(_DEPLOY)["jobs"]["post-deploy-ui-tests"]
    probe = _step(job, "Check the dedicated test identity and resolve the UI URL")
    run = probe["run"]
    assert "gcloud iam service-accounts describe" in run
    assert 'echo "exists=false"' in run
    assert "::notice" in run
    assert "exit 0" in run

    suite = _step(job, "Run the post-deploy UI suite")
    assert suite["if"] == "steps.probe.outputs.exists == 'true'"


def test_deploy_master_uploads_report_and_traces() -> None:
    job = _load(_DEPLOY)["jobs"]["post-deploy-ui-tests"]
    upload = _step(job, "Upload Playwright report and traces")
    assert upload["uses"].startswith("actions/upload-artifact")
    assert upload["if"].startswith("always()")
    path = upload["with"]["path"]
    assert "e2e/ui/playwright-report" in path
    assert "e2e/ui/test-results" in path


@pytest.mark.parametrize(
    ("path", "job_name"),
    [(_DEPLOY, "post-deploy-ui-tests"), (_ON_DEMAND, "ui-tests")],
    ids=["deploy", "on-demand"],
)
def test_jwt_is_minted_with_wif_and_never_logged(
    path: Path, job_name: str
) -> None:
    workflow = _load(path)
    job = workflow["jobs"][job_name]

    assert any(
        step.get("uses", "").startswith("google-github-actions/auth")
        for step in _steps(job)
    )
    assert "agentic-kg-e2e" in job["env"]["E2E_SERVICE_ACCOUNT"]

    mint = _step(job, "Mint the IAP service-account JWT")["run"]
    assert "gcloud iam service-accounts sign-jwt" in mint
    assert "$RUNNER_TEMP/e2e-iap.jwt" in mint
    assert "$E2E_SERVICE_ACCOUNT" in mint

    # The token is read from the runner temp file into the test process only.
    run = _step(job, "Run the post-deploy UI suite")["run"]
    assert 'E2E_IAP_JWT="$(cat "$RUNNER_TEMP/e2e-iap.jwt")"' in run
    assert "npm test" in run
    # No step may print the token.
    assert 'echo "$E2E_IAP_JWT"' not in run
    assert "echo ${E2E_IAP_JWT}" not in run
    assert 'echo "$TOKEN"' not in run


def test_on_demand_workflow_is_dispatchable() -> None:
    workflow = _load(_ON_DEMAND)
    assert "workflow_dispatch" in _triggers(workflow)
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["ui-tests"]
    assert job["permissions"]["id-token"] == "write"
    assert job["environment"] == "staging"
