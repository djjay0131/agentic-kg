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
