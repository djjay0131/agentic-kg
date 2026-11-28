"""The workflow Job dispatcher: coordinates, args and the fallback contract."""

from __future__ import annotations

import pytest
from agentic_kg.agents.dispatch import (
    CloudRunJobDispatcher,
    WorkflowDispatchError,
    build_dispatcher,
    default_job_name,
    job_resource_name,
)


class _RecordingClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict] = []

    def run_job(self, job_resource_name: str, *, args: list[str], env: dict[str, str]) -> str:
        self.calls.append({"job": job_resource_name, "args": args, "env": env})
        if self.fail:
            raise RuntimeError("403 Permission denied on run.jobs.runWithOverrides")
        return "projects/p/locations/r/executions/e1"


def test_default_job_name_reuses_the_ingest_job():
    assert default_job_name({"GCP_ENV": "staging"}) == "agentic-kg-ingest-staging"
    assert (
        default_job_name(
            {"GCP_ENV": "staging", "WORKFLOW_JOB_NAME": "agentic-kg-workflow-staging"}
        )
        == "agentic-kg-workflow-staging"
    )


def test_job_resource_name():
    assert (
        job_resource_name(project="p", region="r", job="j")
        == "projects/p/locations/r/jobs/j"
    )


def test_auto_builds_a_dispatcher_when_coordinates_are_present():
    client = _RecordingClient()
    dispatcher = build_dispatcher(
        env={"GCP_PROJECT": "p", "GCP_REGION": "r", "GCP_ENV": "staging"},
        run_client_factory=lambda _p, _r: client,
    )

    assert isinstance(dispatcher, CloudRunJobDispatcher)
    assert dispatcher.job_resource == "projects/p/locations/r/jobs/agentic-kg-ingest-staging"


def test_inline_mode_has_no_dispatcher():
    assert build_dispatcher(env={"WORKFLOW_EXECUTION_MODE": "inline"}) is None


def test_auto_without_coordinates_uses_inline():
    assert build_dispatcher(env={}) is None


def test_job_mode_without_coordinates_fails_loudly():
    with pytest.raises(WorkflowDispatchError, match="GCP_PROJECT"):
        build_dispatcher(env={"WORKFLOW_EXECUTION_MODE": "job"})


def test_unknown_mode_raises():
    with pytest.raises(ValueError, match="WORKFLOW_EXECUTION_MODE"):
        build_dispatcher(env={"WORKFLOW_EXECUTION_MODE": "queue"})


def test_dispatch_passes_the_phase_and_run_id_as_args():
    client = _RecordingClient()
    dispatcher = CloudRunJobDispatcher(
        job_resource="projects/p/locations/r/jobs/j", run_client=client
    )

    assert dispatcher.dispatch("run-1", phase="resume") is True
    assert client.calls == [
        {"job": "projects/p/locations/r/jobs/j", "args": ["workflow", "resume", "run-1"], "env": {}}
    ]


def test_permission_failure_falls_back_when_not_strict():
    dispatcher = CloudRunJobDispatcher(
        job_resource="projects/p/locations/r/jobs/j",
        run_client=_RecordingClient(fail=True),
    )

    assert dispatcher.dispatch("run-1", phase="resume") is False


def test_permission_failure_is_loud_in_strict_mode():
    dispatcher = CloudRunJobDispatcher(
        job_resource="projects/p/locations/r/jobs/j",
        run_client=_RecordingClient(fail=True),
        strict=True,
    )

    with pytest.raises(WorkflowDispatchError, match="runWithOverrides"):
        dispatcher.dispatch("run-1", phase="resume")
