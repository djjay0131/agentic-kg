"""Terminal detection against recorded Cloud Run Admin **v2 REST** resources.

#172 polled a finished execution 115 times and then reported ``job_timeout``:
the code read the v1/gcloud ``status.completionTime`` view while the v2 REST
Execution carries ``completionTime``, ``succeededCount``, ``failedCount``,
``cancelledCount``, ``runningCount``, ``conditions[].state`` and
``reconciling`` at the top level.

These fixtures are recorded shapes (not dicts written to match the parser), with
the exact transition from the failing deploy: the run operation is done, the
execution is already complete, and polling must return on the first execution
GET.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agentic_kg.agents.config import SandboxConfig
from agentic_kg.agents.sandbox import (
    CloudRunJobBackend,
    _OBJECT_COMPLETE_KEY,
    _poll_execution,
    execution_condition_message,
    execution_succeeded,
    is_execution_terminal,
    is_operation,
    operation_execution_name,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cloud_run_v2"


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.parametrize(
    ("name", "terminal", "succeeded"),
    [
        ("execution-running.json", False, False),
        ("execution-succeeded.json", True, True),
        ("execution-failed.json", True, False),
        ("execution-cancelled.json", True, False),
    ],
)
def test_v2_execution_shapes_are_recognised(name, terminal, succeeded):
    execution = _fixture(name)

    assert is_execution_terminal(execution) is terminal
    assert execution_succeeded(execution) is succeeded


def test_v2_condition_message_comes_from_the_top_level_condition():
    execution = _fixture("execution-failed.json")

    message = execution_condition_message(execution)

    assert "NonZeroExitCode" in message
    assert "exit code: 1" in message


def test_operation_detected_with_and_without_done():
    done = _fixture("operation-done.json")
    in_flight = _fixture("operation-not-done.json")

    assert is_operation(done) is True
    assert is_operation(in_flight) is True
    assert operation_execution_name(done) == (
        "projects/vt-gcp-00042/locations/us-central1/jobs/"
        "agentic-kg-experiment-staging/executions/"
        "agentic-kg-experiment-staging-6h6hp"
    )
    assert operation_execution_name(in_flight) is None


def test_poll_returns_on_the_first_execution_get_after_a_done_operation():
    operation = _fixture("operation-done.json")
    execution = _fixture("execution-succeeded.json")
    execution_name = execution["name"]

    class Run:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get_execution(self, name: str) -> dict:
            self.calls.append(name)
            if name == operation["name"]:
                return operation
            if name == execution_name:
                return execution
            raise AssertionError(f"unexpected GET {name}")

    run = Run()
    clock = FakeClock()

    result = _poll_execution(
        run,
        operation["name"],
        330,
        clock=clock,
        sleep=clock.sleep,
        interval=2,
    )

    assert result["completionTime"] == "2026-11-28T18:25:24.647250Z"
    assert run.calls == [operation["name"], execution_name]
    assert clock.now == 0


def test_a_running_v2_execution_is_not_terminal_and_times_out():
    execution = _fixture("execution-running.json")

    class Run:
        def __init__(self) -> None:
            self.calls = 0

        def get_execution(self, name: str) -> dict:
            self.calls += 1
            return execution

    run = Run()
    clock = FakeClock()

    with pytest.raises(TimeoutError):
        _poll_execution(
            run,
            execution["name"],
            5,
            clock=clock,
            sleep=clock.sleep,
            interval=2,
        )

    assert run.calls >= 2


class ObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def download_bytes(self, uri: str) -> bytes | None:
        return self.objects.get(uri)


def test_result_object_end_is_completion_when_the_control_plane_lags():
    execution = _fixture("execution-running.json")
    store = ObjectStore()
    result_uri = "gs://bucket/staging/exp-1"
    store.objects[f"{result_uri}/result.json"] = (
        b'{"exit_code": 0, "timed_out": false}'
    )

    class Run:
        def __init__(self) -> None:
            self.calls = 0

        def get_execution(self, name: str) -> dict:
            self.calls += 1
            return execution

    run = Run()
    clock = FakeClock()

    result = _poll_execution(
        run,
        execution["name"],
        330,
        clock=clock,
        sleep=clock.sleep,
        interval=2,
        object_store=store,
        result_uri=result_uri,
        object_probe_every=2,
    )

    assert result[_OBJECT_COMPLETE_KEY] is True
    assert run.calls == 2


class BackendStore(ObjectStore):
    def __init__(self) -> None:
        super().__init__()
        self.uploads: list[tuple[str, bytes]] = []

    def upload_bytes(
        self, uri: str, data: bytes, content_type: str | None = None
    ) -> None:
        self.objects[uri] = data
        self.uploads.append((uri, data))


def _config() -> SandboxConfig:
    return SandboxConfig(
        backend="cloudrun_job",
        gcs_bucket="vt-gcp-00042-agentic-kg-experiments",
        gcs_prefix="staging",
        project_id="vt-gcp-00042",
        region="us-central1",
        job_name="agentic-kg-experiment-staging",
        poll_interval_seconds=1,
        gcs_endpoint="https://storage.googleapis.com",
    )


def test_backend_reads_a_v2_succeeded_execution_and_the_result_object():
    store = BackendStore()
    execution = _fixture("execution-succeeded.json")

    class Run:
        def run_job(self, job_resource_name, *, args, env):
            prefix = args[0].rsplit("/", 1)[0]
            store.upload_bytes(f"{prefix}/stdout.txt", b"ok")
            store.upload_bytes(f"{prefix}/stderr.txt", b"")
            store.upload_bytes(
                f"{prefix}/result.json",
                json.dumps({"exit_code": 0, "timed_out": False}).encode(),
            )
            return execution["name"]

        def get_execution(self, name: str) -> dict:
            return execution

    clock = FakeClock()
    backend = CloudRunJobBackend(
        _config(), store=store, run_client=Run(), clock=clock, sleep=clock.sleep
    )

    result = backend.execute("print('hi')", timeout_s=30)

    assert result.success is True
    assert result.backend == "cloudrun_job"
    assert result.exit_code == 0
    assert clock.now == 0


def test_backend_reports_a_cancelled_v2_execution_as_infrastructure():
    store = BackendStore()
    execution = _fixture("execution-cancelled.json")

    class Run:
        def run_job(self, job_resource_name, *, args, env):
            return execution["name"]

        def get_execution(self, name: str) -> dict:
            return execution

    clock = FakeClock()
    backend = CloudRunJobBackend(
        _config(), store=store, run_client=Run(), clock=clock, sleep=clock.sleep
    )

    result = backend.execute("print('hi')", timeout_s=30)

    assert result.success is False
    assert result.infrastructure_failure is True
    assert result.failure_category == "job_failed"
    assert "Cancelled" in result.stderr
