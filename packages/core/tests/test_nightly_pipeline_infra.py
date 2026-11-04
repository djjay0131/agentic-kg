"""Structural tests for the nightly pipeline's IAM and failure visibility.

No network, no Docker, no Terraform. These pin the two defects that let the
nightly pipeline fail silently for six nights:

* the Workflow's service account held ``roles/run.invoker`` on the Jobs, which
  grants ``run.jobs.run`` but not ``run.jobs.runWithOverrides`` (needed because
  the Workflow passes env overrides);
* it had no read grant for the project-scoped Operation that ``jobs.run``
  returns, so polling completion would fail;
* the manual runner printed the execution state but exited 0 on ``FAILED``.

They also pin the read-only diagnostics that must make the next failure
legible without a human running ``gcloud`` by hand.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MAIN_TF = _REPO_ROOT / "infra" / "main.tf"
_NIGHTLY_RUN = _REPO_ROOT / ".github" / "workflows" / "nightly-run.yml"
_DIAGNOSE = _REPO_ROOT / ".github" / "workflows" / "gcp-diagnose.yml"

_NIGHTLY_JOBS = (
    "agentic-kg-nightly-staging",
    "agentic-kg-ingest-staging",
    "agentic-kg-adviser-staging",
)


def _main_tf() -> str:
    return _MAIN_TF.read_text(encoding="utf-8")


def _resource(text: str, address: str) -> str:
    """Return a top-level Terraform resource block by ``type.name``."""
    resource_type, resource_name = address.split(".", 1)
    marker = f'resource "{resource_type}" "{resource_name}"'
    assert marker in text, f"missing {address}"
    return text.split(marker, 1)[1].split("\n}\n", 1)[0]


def test_nightly_runner_can_start_a_job_with_overrides():
    block = _resource(_main_tf(), "google_cloud_run_v2_job_iam_member.nightly_runner")
    assert 'role     = "roles/run.jobsExecutorWithOverrides"' in block
    assert "roles/run.invoker" not in block
    assert "google_cloud_run_v2_job.nightly[0].name" in block


def test_adviser_runner_can_start_a_job_with_overrides():
    block = _resource(_main_tf(), "google_cloud_run_v2_job_iam_member.adviser_runner")
    assert 'role     = "roles/run.jobsExecutorWithOverrides"' in block
    assert "roles/run.invoker" not in block
    assert "google_cloud_run_v2_job.adviser[0].name" in block


def test_research_cycle_runner_can_start_a_job_with_overrides():
    block = _resource(
        _main_tf(), "google_cloud_run_v2_job_iam_member.research_cycle_runner"
    )
    assert 'role     = "roles/run.jobsExecutorWithOverrides"' in block
    assert "roles/run.invoker" not in block
    assert "google_cloud_run_v2_job.research_cycle[0].name" in block


def test_workflow_can_read_the_project_scoped_operation_it_polls():
    block = _resource(_main_tf(), "google_project_iam_member.nightly_run_viewer")
    assert 'role    = "roles/run.viewer"' in block
    assert "serviceAccount:${local.nightly_sa_email}" in block


def test_workflow_starts_both_jobs_and_polls_their_operations():
    block = _resource(_main_tf(), "google_workflows_workflow.nightly")
    assert "googleapis.run.v2.projects.locations.jobs.run" in block
    assert "google_cloud_run_v2_job.nightly[0].id" in block
    assert "local.adviser_job_id" in block
    assert "local.research_cycle_job_id" in block
    assert "googleapis.run.v2.projects.locations.operations.get" in block
    assert "service_account = local.nightly_sa_email" in block


def test_manual_runner_describes_the_execution_and_fails_on_non_success():
    workflow = yaml.safe_load(_NIGHTLY_RUN.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["run"]["steps"]
    run = next(s for s in steps if s["name"] == "Run the nightly workflow")["run"]
    assert "gcloud workflows executions describe" in run
    assert "--format=json" in run
    assert "error=" in run
    assert 'if [ "${state}" != "SUCCEEDED" ]' in run
    assert "exit 1" in run


def test_diagnose_lists_workflow_scheduler_and_job_failures():
    text = _DIAGNOSE.read_text(encoding="utf-8")
    assert "gcloud workflows executions list agentic-kg-nightly-staging" in text
    assert "gcloud workflows executions describe" in text
    assert "gcloud scheduler jobs describe agentic-kg-nightly-staging" in text
    assert "lastAttemptTime" in text

    for job in _NIGHTLY_JOBS:
        assert job in text, job
    assert "gcloud run jobs executions list" in text
    assert "gcloud logging read" in text
    assert "severity>=ERROR" in text
    assert "jsonPayload.event" in text
    assert "textPayload" in text


def test_diagnose_never_reads_secret_values():
    text = _DIAGNOSE.read_text(encoding="utf-8")
    assert "gcloud secrets versions access" not in text
    assert "gcloud secrets versions describe" not in text
