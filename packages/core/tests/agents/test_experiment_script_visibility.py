"""Defect #171: the experiment runner must see the script the worker uploaded.

These tests exercise the two *real* code paths (``CloudRunJobBackend`` upload +
``experiment_runner`` download) against one fake GCS that is strict about
object names, and pin the failure-classification behaviour:

* an object name that differs by a slash, prefix or bucket fails loudly;
* a GCS 403 / 401 / 404 / 5xx is reported with its status, not collapsed to
  "not found" (the pre-#171 ``blob.exists()`` behaviour);
* the private VIP gets the canonical ``Host: storage.googleapis.com`` header;
* ``:run`` returns a long-running operation, so polling must use ``done`` and
  then the Execution's terminal state instead of waiting the full budget;
* a runner ``error.json`` names the stage; a non-zero script is a result.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from agentic_kg.agents import experiment_runner
from agentic_kg.agents.config import SandboxConfig
from agentic_kg.agents.sandbox import (
    CloudRunJobBackend,
    ExecutionOperationError,
    _poll_execution,
    classify_sandbox_error,
)

_BUCKET = "vt-gcp-00042-agentic-kg-experiments"
_PREFIX = "staging"


class StrictGcs:
    """A GCS fake that stores exact ``gs://bucket/key`` names.

    No normalisation: a leading slash, a doubled slash, a wrong bucket or a
    different prefix is a different key and therefore "not found". This is how
    a real upload/download name mismatch surfaces in the round-trip test.
    """

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.uploads: list[str] = []

    def _check(self, uri: str) -> str:
        parsed = urlparse(uri)
        if parsed.scheme != "gs":
            raise AssertionError(f"not a gs:// URI: {uri!r}")
        if parsed.netloc != _BUCKET:
            raise AssertionError(f"unexpected bucket in {uri!r}")
        key = uri.split("/", 3)[3]
        if key.startswith("/") or "//" in key:
            raise AssertionError(f"ambiguous object key in {uri!r}")
        return key

    # Backend surface (GcsObjectStore)
    def upload_bytes(self, uri: str, data: bytes, content_type: str | None = None) -> None:
        self._check(uri)
        self.objects[uri] = data
        self.uploads.append(uri)

    def download_bytes(self, uri: str) -> bytes | None:
        self._check(uri)
        return self.objects.get(uri)

    # Runner surface (_Store)
    def download(self, uri: str) -> bytes | None:
        return self.download_bytes(uri)

    def upload(self, uri: str, data: bytes, content_type: str) -> None:
        self.upload_bytes(uri, data, content_type)


class RunnerDrivingRunClient:
    """``run_job`` actually invokes the runner; ``get_execution`` is terminal."""

    def __init__(self) -> None:
        self.args: list[str] = []
        self.executions: list[str] = []

    def run_job(self, job_resource_name: str, *, args: list[str], env: dict[str, str]):
        self.args = args
        self.executions.append("projects/p/locations/r/executions/e1")
        experiment_runner.run(args[0])
        return "projects/p/locations/r/executions/e1"

    def get_execution(self, execution_name: str) -> dict:
        return {
            "status": {
                "completionTime": "2026-11-28T00:00:01Z",
                "succeededCount": 1,
            }
        }


def _config(**overrides) -> SandboxConfig:
    values = {
        "backend": "cloudrun_job",
        "gcs_bucket": _BUCKET,
        "gcs_prefix": _PREFIX,
        "project_id": "vt-gcp-00042",
        "region": "us-central1",
        "job_name": "agentic-kg-experiment-staging",
        "poll_interval_seconds": 1,
    }
    values.update(overrides)
    return SandboxConfig(**values)


def test_upload_and_runner_resolve_the_same_object_name(monkeypatch):
    """Round trip: the backend's URI is exactly the runner's script URI."""
    store = StrictGcs()
    run = RunnerDrivingRunClient()
    monkeypatch.setattr(experiment_runner, "_Store", lambda: store)

    backend = CloudRunJobBackend(_config(), store=store, run_client=run)
    result = backend.execute(
        "import json\nprint(json.dumps({'metrics': {'ok': 1}}))\n",
        inputs={"seed": 3},
        timeout_s=30,
    )

    assert result.success is True
    assert result.exit_code == 0
    assert result.metrics == {"metrics": {"ok": 1}}

    # Every object lives under the one prefix the runner derived.
    prefix = f"gs://{_BUCKET}/{_PREFIX}/{result.experiment_id}"
    assert run.args == [f"{prefix}/script.py"]
    assert set(store.objects) == {
        f"{prefix}/script.py",
        f"{prefix}/inputs.json",
        f"{prefix}/stdout.txt",
        f"{prefix}/stderr.txt",
        f"{prefix}/metrics.json",
        f"{prefix}/result.json",
    }


def test_a_name_mismatch_is_not_silently_rewritten():
    store = StrictGcs()
    store.upload_bytes(f"gs://{_BUCKET}/{_PREFIX}/a/script.py", b"x")
    # A leading slash is a different object; the strict fake refuses to hide it.
    with pytest.raises(AssertionError):
        store.download_bytes(f"gs://{_BUCKET}//{_PREFIX}/a/script.py")


class _Response:
    def __init__(self, status: int, text: str = "") -> None:
        self.status_code = status
        self.text = text
        self.content = text.encode("utf-8")


class _FakeSession:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[dict] = []

    def get(self, url: str, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        return self.response

    def post(self, url: str, headers=None, data=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        return self.response


def _store(monkeypatch, response: _Response, endpoint="https://storage.googleapis.com"):
    monkeypatch.setattr(experiment_runner, "_resolve_endpoint", lambda: (endpoint, False))
    store = experiment_runner._Store()
    session = _FakeSession(response)
    store._session = session
    store._credentials = SimpleNamespace(
        service_account_email="runner@example.iam.gserviceaccount.com"
    )
    return store, session


def test_a_404_is_reported_as_not_found(monkeypatch):
    store, _ = _store(monkeypatch, _Response(404, '{"error":{"code":404}}'))
    assert store.download(f"gs://{_BUCKET}/{_PREFIX}/e/script.py") is None


def test_a_403_names_the_status_the_uri_and_the_identity(monkeypatch):
    store, _ = _store(monkeypatch, _Response(403, '{"error":{"code":403,"message":"denied"}}'))
    with pytest.raises(experiment_runner._StageError) as caught:
        store.download(f"gs://{_BUCKET}/{_PREFIX}/e/script.py")

    exc = caught.value
    assert exc.stage == "download"
    assert exc.category == "permission"
    assert exc.status == 403
    assert "permission denied (403)" in str(exc)
    assert "gs://" + _BUCKET + f"/{_PREFIX}/e/script.py" in str(exc)
    assert "runner@example.iam.gserviceaccount.com" in str(exc)
    assert "denied" in str(exc)


def test_a_401_is_distinguished_from_403(monkeypatch):
    store, _ = _store(monkeypatch, _Response(401, "<html>unauthorized</html>"))
    with pytest.raises(experiment_runner._StageError) as caught:
        store.download(f"gs://{_BUCKET}/{_PREFIX}/e/script.py")
    assert caught.value.category == "unauthenticated"
    assert caught.value.status == 401
    assert "unauthorized" in str(caught.value)


def test_a_500_is_a_connectivity_failure_with_the_body(monkeypatch):
    store, _ = _store(monkeypatch, _Response(503, "<html>backend unavailable</html>"))
    with pytest.raises(experiment_runner._StageError) as caught:
        store.download(f"gs://{_BUCKET}/{_PREFIX}/e/script.py")
    assert caught.value.category == "connectivity"
    assert caught.value.status == 503
    assert "backend unavailable" in str(caught.value)


def test_a_non_json_body_from_the_wrong_host_is_preserved(monkeypatch):
    """A VIP/frontend HTML 404 must not be mistaken for a JSON GCS 404."""
    store, _ = _store(
        monkeypatch,
        _Response(404, "<html><title>Error 404 (Not Found)</title></html>"),
    )
    # 404 is still "absent" semantically, but a metadata GET exposes the body.
    response = store._session.response
    assert "<html>" in experiment_runner._body_excerpt(response)


class _VipSession(_FakeSession):
    def get(self, url: str, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        return _Response(200, "")


def test_private_vip_uses_the_canonical_storage_host(monkeypatch):
    monkeypatch.setattr(
        experiment_runner,
        "_resolve_endpoint",
        lambda: ("https://private.googleapis.com", False),
    )
    store = experiment_runner._Store()
    session = _VipSession(_Response(200, ""))
    store._session = session
    store._credentials = SimpleNamespace(service_account_email="runner@example")

    store.download(f"gs://{_BUCKET}/{_PREFIX}/e/script.py")

    assert store.host_override == "storage.googleapis.com"
    assert session.calls[0]["headers"]["Host"] == "storage.googleapis.com"
    # The URL still targets the VIP the firewall allows.
    assert session.calls[0]["url"].startswith("https://private.googleapis.com/")


def test_public_endpoint_sends_no_host_override(monkeypatch):
    store, session = _store(
        monkeypatch, _Response(200, ""), endpoint="https://storage.googleapis.com"
    )
    store.download(f"gs://{_BUCKET}/{_PREFIX}/e/script.py")
    assert store.host_override is None
    assert "Host" not in (session.calls[0]["headers"] or {})


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class OperationRunClient:
    def __init__(self, *, error: dict | None = None) -> None:
        self.error = error
        self.polls: list[str] = []

    def get_execution(self, name: str) -> dict:
        self.polls.append(name)
        if name.endswith("/operations/op1"):
            if len(self.polls) == 1:
                return {"name": name, "done": False}
            if self.error:
                return {"name": name, "done": True, "error": self.error}
            return {
                "name": name,
                "done": True,
                "response": {"name": "projects/p/executions/e1"},
            }
        return {
            "status": {
                "completionTime": "2026-11-28T00:00:01Z",
                "failedCount": 1,
                "conditions": [
                    {
                        "type": "Completed",
                        "state": "CONDITION_FAILED",
                        "reason": "NonZeroExitCode",
                        "message": "The container exited with an error.",
                    }
                ],
            }
        }


def test_polling_an_operation_returns_as_soon_as_the_execution_is_terminal():
    run = OperationRunClient()
    clock = FakeClock()

    execution = _poll_execution(
        run,
        "projects/p/operations/op1",
        330,
        clock=clock,
        sleep=clock.sleep,
        interval=1,
    )

    assert execution["status"]["failedCount"] == 1
    # A few seconds, not the full 330 s budget the pre-#171 code spent.
    assert clock.now < 10
    assert run.polls == [
        "projects/p/operations/op1",
        "projects/p/operations/op1",
        "projects/p/executions/e1",
    ]


def test_an_operation_error_fails_immediately():
    run = OperationRunClient(error={"code": 403, "message": "run.jobs.run denied"})
    clock = FakeClock()
    with pytest.raises(ExecutionOperationError) as caught:
        _poll_execution(
            run,
            "projects/p/operations/op1",
            330,
            clock=clock,
            sleep=clock.sleep,
            interval=1,
        )
    assert "403" in str(caught.value)
    assert "permission" in classify_sandbox_error(caught.value)


class _ClassificationRun:
    def __init__(self, store: StrictGcs) -> None:
        self.store = store

    def run_job(self, job_resource_name: str, *, args: list[str], env: dict[str, str]):
        return "projects/p/operations/op1"

    def get_execution(self, name: str) -> dict:
        if name.endswith("/operations/op1"):
            if getattr(self, "_seen", False):
                return {
                    "done": True,
                    "response": {"name": "projects/p/executions/e1"},
                }
            self._seen = True
            return {"done": False}
        return {
            "status": {
                "completionTime": "2026-11-28T00:00:01Z",
                "failedCount": 1,
                "conditions": [
                    {
                        "type": "Completed",
                        "state": "CONDITION_FAILED",
                        "reason": "NonZeroExitCode",
                        "message": "The container exited with an error.",
                    }
                ],
            }
        }


def test_a_runner_error_json_classifies_the_failure_stage(monkeypatch):
    store = StrictGcs()
    run = _ClassificationRun(store)
    clock = FakeClock()
    backend = CloudRunJobBackend(
        _config(), store=store, run_client=run, clock=clock, sleep=clock.sleep
    )

    # Simulate the runner's own error.json for a download 403.
    original_upload = store.upload_bytes

    def upload_then_error(uri, data, content_type=None):
        original_upload(uri, data, content_type)
        if uri.endswith("/inputs.json"):
            prefix = uri.rsplit("/", 1)[0]
            original_upload(
                f"{prefix}/error.json",
                json.dumps(
                    {
                        "stage": "download",
                        "category": "permission",
                        "message": (
                            f"permission denied (403) reading {prefix}/script.py "
                            "as runner@example.iam.gserviceaccount.com"
                        ),
                        "status": 403,
                        "body_excerpt": "denied",
                        "last_log": "stage=download script=...",
                    }
                ).encode(),
                "application/json",
            )

    store.upload_bytes = upload_then_error  # type: ignore[method-assign]

    result = backend.execute("print('hi')", timeout_s=30)

    assert result.success is False
    assert result.infrastructure_failure is True
    assert result.failure_stage == "download"
    assert result.failure_category == "permission"
    assert "permission denied (403)" in result.stderr
    assert "runner@example.iam.gserviceaccount.com" in result.stderr
    assert "last_log=stage=download script=..." in result.stderr
    # It returned at the terminal state, not after the 330 s budget.
    assert clock.now < 10


def test_a_non_zero_script_is_an_experiment_result(monkeypatch):
    store = StrictGcs()
    run = _ClassificationRun(store)
    original_upload = store.upload_bytes

    def upload_then_result(uri, data, content_type=None):
        original_upload(uri, data, content_type)
        if uri.endswith("/inputs.json"):
            prefix = uri.rsplit("/", 1)[0]
            original_upload(f"{prefix}/stdout.txt", b"the experiment ran", "text/plain")
            original_upload(f"{prefix}/stderr.txt", b"boom", "text/plain")
            original_upload(
                f"{prefix}/result.json",
                json.dumps({"exit_code": 1, "timed_out": False}).encode(),
                "application/json",
            )

    store.upload_bytes = upload_then_result  # type: ignore[method-assign]
    clock = FakeClock()
    backend = CloudRunJobBackend(
        _config(), store=store, run_client=run, clock=clock, sleep=clock.sleep
    )

    result = backend.execute("raise SystemExit(1)", timeout_s=30)

    assert result.success is False
    assert result.infrastructure_failure is False
    assert result.failure_category is None
    assert result.exit_code == 1
    assert "boom" in result.stderr
