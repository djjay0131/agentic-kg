"""Pin the deploy-time workflow self-check wiring.

The async worker path (after #167) cannot be exercised by the browser suite,
which authenticates through IAP and only checks the UI surface. A broken
``job_runner`` arg/env path left staging runs waiting with a consumed decision
and nothing in the deploy noticed. ``deploy-master`` now runs
``agentic-kg workflow selfcheck`` through the same execution-time args override
the API uses for ``workflow resume``. These offline assertions keep that step
present and correctly wired; the live check runs only in CI.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEPLOY_MASTER = _REPO_ROOT / ".github" / "workflows" / "deploy-master.yml"


def _load() -> dict:
    return yaml.safe_load(_DEPLOY_MASTER.read_text(encoding="utf-8"))


def _steps(job: dict) -> list[dict]:
    return job["steps"]


def _step(job: dict, name: str) -> dict:
    matches = [step for step in _steps(job) if step.get("name") == name]
    assert len(matches) == 1, f"expected exactly one step named {name!r}"
    return matches[0]


def test_deploy_runs_the_workflow_selfcheck_through_the_ingest_job() -> None:
    deploy = _load()["jobs"]["deploy-staging"]
    step = _step(deploy, "Workflow self-check (Job dispatch path)")
    run = step["run"]

    assert "gcloud run jobs execute" in run
    assert "--args=workflow,selfcheck" in run
    assert "agentic-kg-ingest-staging" in run
    # A failed execution must fail the deploy, not be logged and ignored.
    assert "exit 1" in run
    assert "failedCount" in run


def test_workflow_selfcheck_runs_for_api_or_job_changes() -> None:
    deploy = _load()["jobs"]["deploy-staging"]
    step = _step(deploy, "Workflow self-check (Job dispatch path)")
    condition = step["if"]
    assert "needs.changes.outputs.api" in condition
    assert "needs.changes.outputs.job" in condition


def test_deploy_runs_the_experiment_sandbox_check_through_the_ingest_job() -> None:
    """The sandbox runs in the worker Job, not the API, so prove it there.

    #169 failed because the worker reused the API's private GCS endpoint. The
    deploy check must execute from the ingest Job and read the coordinates
    Terraform set on the API, while leaving the endpoint for the worker to
    resolve.
    """
    deploy = _load()["jobs"]["deploy-staging"]
    step = _step(deploy, "Experiment sandbox self-check (Job dispatch path)")
    run = step["run"]

    assert "gcloud run jobs execute" in run
    assert "--args=sandbox-check" in run
    assert "agentic-kg-ingest-staging" in run
    assert "agentic-kg-api-staging" in run
    # The API's endpoint is intentionally not forwarded to the worker.
    assert "EXPERIMENT_GCS_ENDPOINT" not in run
    assert "exit 1" in run
    assert "failedCount" in run


def test_experiment_sandbox_check_runs_for_api_or_job_changes() -> None:
    deploy = _load()["jobs"]["deploy-staging"]
    step = _step(deploy, "Experiment sandbox self-check (Job dispatch path)")
    condition = step["if"]
    assert "needs.changes.outputs.api" in condition
    assert "needs.changes.outputs.job" in condition


def test_each_deploy_check_prints_the_failing_execution_logs() -> None:
    """A non-zero execution must not be reported as just "exited 1".

    The failure category lives only in Cloud Logging; the workflow now reads
    the failing execution's own message fields (never env values).
    """
    deploy = _load()["jobs"]["deploy-staging"]
    for name in (
        "Check configured LLM models answer",
        "Workflow self-check (Job dispatch path)",
        "Experiment sandbox self-check (Job dispatch path)",
    ):
        run = _step(deploy, name)["run"]
        assert "gcloud logging read" in run, name
        assert "resource.type=\\\"cloud_run_job\\\"" in run, name
        assert "jsonPayload.message" in run, name
        # Environment values must never be printed.
        assert "env[].value" not in run, name


def test_sandbox_check_echoes_its_one_line_verdict() -> None:
    deploy = _load()["jobs"]["deploy-staging"]
    run = _step(deploy, "Experiment sandbox self-check (Job dispatch path)")["run"]
    assert "SANDBOX_CHECK_VERDICT" in run
    assert "title=Experiment sandbox verdict::" in run
    # On failure it also describes the experiment Job's latest execution.
    assert "dump_experiment_job" in run
    assert "gcloud run jobs executions describe" in run
    assert "status.failedCount" in run


def test_experiment_job_is_rolled_on_every_deploy() -> None:
    """A skipped experiment build must not leave the Job on another tag."""
    deploy = _load()["jobs"]["deploy-staging"]
    step = _step(deploy, "Deploy experiment Job to staging")
    assert "if" not in step or "needs.changes.outputs.experiment" not in step["if"]
    assert "gcloud run jobs update agentic-kg-experiment-staging" in step["run"]
    assert "experiment Job image:" in step["run"]
    # The image is read through the tested formatter (the old
    # ``--format='value(template.template...)`` printed blank, #171).
    assert "scripts/format_run_job.py" in step["run"]


def test_gcp_diagnose_has_a_read_only_experiment_sandbox_section() -> None:
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[3]
        / ".github"
        / "workflows"
        / "gcp-diagnose.yml"
    )
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["diagnose"]["steps"]
    names = [step.get("name") for step in steps]
    assert "Experiment sandbox: Job definition and recent executions" in names
    assert "Experiment sandbox: bucket IAM for the experiment prefix" in names
    assert "Experiment sandbox: ingest Job definition summary" in names

    joined = "\n".join(str(step.get("run", "")) for step in steps)
    assert "agentic-kg-experiment-staging" in joined
    # Env names only, never values.
    assert "env[].value" not in joined
    assert "get-iam-policy" in joined
    # The job definition comes from the tested, two-shape formatter.
    assert joined.count("scripts/format_run_job.py") >= 2
    assert "status.conditions" in joined
