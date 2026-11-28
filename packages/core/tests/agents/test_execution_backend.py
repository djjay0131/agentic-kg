"""Unit tests for the pluggable experiment execution backend.

No network and no Docker: the Cloud Run Run-Admin client and the GCS object
store are fakes. DockerSandbox itself is covered by ``test_sandbox.py``.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from agentic_kg.agents import experiment_runner
from agentic_kg.agents.config import SandboxConfig, reset_agent_config
from agentic_kg.agents.sandbox import (
    CloudRunJobBackend,
    DockerSandbox,
    create_execution_backend,
    execution_succeeded,
    is_execution_terminal,
)


class FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.uploads: list[tuple[str, bytes]] = []

    def upload_bytes(self, uri: str, data: bytes, content_type: str | None = None) -> None:
        self.objects[uri] = data
        self.uploads.append((uri, data))

    def download_bytes(self, uri: str) -> bytes | None:
        return self.objects.get(uri)

    def download(self, uri: str) -> bytes | None:
        return self.objects.get(uri)

    def upload(self, uri: str, data: bytes, content_type: str) -> None:
        self.objects[uri] = data
        self.uploads.append((uri, data))


class FakeRunClient:
    def __init__(self, store: FakeStore, *, status: str = "success") -> None:
        self.store = store
        self.status = status
        self.calls: list[dict] = []
        self.polled = 0

    def run_job(self, job_resource_name: str, *, args: list[str], env: dict[str, str]) -> str:
        self.calls.append({"job": job_resource_name, "args": args, "env": env})
        prefix = args[0].rsplit("/", 1)[0]
        if self.status == "success":
            self.store.upload_bytes(
                f"{prefix}/stdout.txt",
                b'{"metrics": {"accuracy": 0.93}}',
            )
            self.store.upload_bytes(f"{prefix}/stderr.txt", b"")
            self.store.upload_bytes(
                f"{prefix}/metrics.json", b'{"metrics": {"accuracy": 0.93}}'
            )
            self.store.upload_bytes(
                f"{prefix}/result.json",
                json.dumps({"exit_code": 0, "timed_out": False}).encode(),
            )
        elif self.status == "failure":
            self.store.upload_bytes(f"{prefix}/stdout.txt", b"")
            self.store.upload_bytes(
                f"{prefix}/stderr.txt", b"Traceback: boom"
            )
            self.store.upload_bytes(
                f"{prefix}/result.json",
                json.dumps({"exit_code": 1, "timed_out": False}).encode(),
            )
        elif self.status == "timeout_with_result":
            self.store.upload_bytes(f"{prefix}/stdout.txt", b"")
            self.store.upload_bytes(f"{prefix}/stderr.txt", b"exceeded 5s")
            self.store.upload_bytes(
                f"{prefix}/result.json",
                json.dumps({"exit_code": -1, "timed_out": True}).encode(),
            )
        return "projects/p/locations/r/executions/e1"

    def get_execution(self, execution_name: str) -> dict:
        self.polled += 1
        if self.status in ("success", "success_no_result"):
            return {
                "status": {
                    "completionTime": "2026-10-26T00:00:01Z",
                    "succeededCount": 1,
                }
            }
        if self.status == "failure":
            return {
                "status": {
                    "completionTime": "2026-10-26T00:00:01Z",
                    "failedCount": 1,
                    "conditions": [
                        {"type": "Completed", "state": "CONDITION_FAILED"}
                    ],
                }
            }
        return {"status": {"startTime": "2026-10-26T00:00:00Z"}}


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _config(**overrides) -> SandboxConfig:
    values = {
        "backend": "cloudrun_job",
        "gcs_bucket": "vt-gcp-00042-agentic-kg-experiments",
        "gcs_prefix": "staging",
        "project_id": "vt-gcp-00042",
        "region": "us-central1",
        "job_name": "agentic-kg-experiment-staging",
        "image_digest": "sha256:abc123",
        "poll_interval_seconds": 1,
    }
    values.update(overrides)
    return SandboxConfig(**values)


class TestBackendSelection:
    @pytest.fixture(autouse=True)
    def _reset(self):
        reset_agent_config()
        yield
        reset_agent_config()

    def test_default_is_docker(self, monkeypatch):
        monkeypatch.delenv("EXPERIMENT_BACKEND", raising=False)
        assert isinstance(create_execution_backend(SandboxConfig()), DockerSandbox)

    def test_cloudrun_job_selected(self):
        backend = create_execution_backend(_config())
        assert isinstance(backend, CloudRunJobBackend)

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown EXPERIMENT_BACKEND"):
            create_execution_backend(_config(backend="kubernetes"))

    def test_cloudrun_requires_bucket(self):
        with pytest.raises(ValueError, match="EXPERIMENT_GCS_BUCKET"):
            CloudRunJobBackend(_config(gcs_bucket=""), store=FakeStore())

    def test_sandbox_timeout_env_is_the_per_run_budget(self, monkeypatch):
        monkeypatch.setenv("SANDBOX_TIMEOUT", "42")
        assert SandboxConfig().timeout_seconds == 42


class TestCloudRunJobBackend:
    def test_success_round_trip_and_provenance(self):
        store = FakeStore()
        run = FakeRunClient(store, status="success")
        backend = CloudRunJobBackend(_config(), store=store, run_client=run)

        result = backend.execute("print('hi')", inputs={"seed": 7}, timeout_s=30)

        assert result.success is True
        assert result.exit_code == 0
        assert result.backend == "cloudrun_job"
        assert result.metrics == {"metrics": {"accuracy": 0.93}}
        assert result.experiment_id
        assert result.result_uri.startswith(
            "gs://vt-gcp-00042-agentic-kg-experiments/staging/"
        )
        assert result.code_hash == hashlib.sha256(b"print('hi')").hexdigest()
        assert result.image_digest == "sha256:abc123"
        assert result.started_at and result.finished_at

        call = run.calls[0]
        assert call["job"] == (
            "projects/vt-gcp-00042/locations/us-central1/jobs/"
            "agentic-kg-experiment-staging"
        )
        assert call["args"][0].endswith("/script.py")
        assert call["args"][0].startswith("gs://")
        assert result.experiment_id == call["env"]["EXPERIMENT_ID"]
        # The runner's own cap is the per-run budget minus a small margin, so
        # it can upload partial output before the Job platform timeout.
        assert call["env"]["EXPERIMENT_TIMEOUT_S"] == "25"

        uploaded = {uri: data for uri, data in store.uploads}
        script_uri = call["args"][0]
        assert uploaded[script_uri] == b"print('hi')"
        inputs_uri = script_uri.rsplit("/", 1)[0] + "/inputs.json"
        assert json.loads(uploaded[inputs_uri]) == {"seed": 7}

    def test_failure_reads_back_outputs(self):
        store = FakeStore()
        run = FakeRunClient(store, status="failure")
        backend = CloudRunJobBackend(_config(), store=store, run_client=run)

        result = backend.execute("raise SystemExit(1)")

        assert result.success is False
        assert result.exit_code == 1
        assert "Traceback" in result.stderr
        assert result.backend == "cloudrun_job"

    def test_poll_timeout(self):
        store = FakeStore()
        run = FakeRunClient(store, status="running")
        clock = FakeClock()
        backend = CloudRunJobBackend(
            _config(poll_interval_seconds=2),
            store=store,
            run_client=run,
            sleep=clock.sleep,
            clock=clock,
        )

        result = backend.execute("while True: pass", timeout_s=5)

        assert result.success is False
        assert result.timed_out is True
        assert result.exit_code == -1
        # No result.json was written: the Job never produced output, so this
        # is an infrastructure failure, not a scientific timeout.
        assert result.infrastructure_failure is True
        assert result.failure_category == "job_timeout"
        assert result.failure_stage == "poll"

    def test_missing_result_object_is_infrastructure_even_if_execution_succeeds(self):
        """A successful execution with no result.json is not a scientific result."""
        store = FakeStore()
        run = FakeRunClient(store, status="success_no_result")
        backend = CloudRunJobBackend(_config(), store=store, run_client=run)

        result = backend.execute("print('hi')", timeout_s=30)

        assert result.success is False
        assert result.infrastructure_failure is True
        assert result.failure_category == "job_failed"
        assert "without writing result.json" in result.stderr

    def test_poll_timeout_with_a_result_object_is_an_experiment_timeout(self):
        """A script that wrote its own timed_out result is a scientific result."""
        store = FakeStore()
        run = FakeRunClient(store, status="timeout_with_result")
        clock = FakeClock()
        backend = CloudRunJobBackend(
            _config(poll_interval_seconds=2),
            store=store,
            run_client=run,
            sleep=clock.sleep,
            clock=clock,
        )

        result = backend.execute("while True: pass", timeout_s=5)

        assert result.timed_out is True
        assert result.infrastructure_failure is False
        assert result.failure_category is None


class TestExecutionStatus:
    def test_terminal_detection(self):
        assert is_execution_terminal({"status": {"completionTime": "x"}}) is True
        assert is_execution_terminal({"status": {"startTime": "x"}}) is False

    def test_succeeded(self):
        assert execution_succeeded(
            {"status": {"completionTime": "x", "succeededCount": 1}}
        ) is True
        assert execution_succeeded(
            {"status": {"completionTime": "x", "failedCount": 1}}
        ) is False


class TestExperimentRunner:
    def test_parse_metrics(self):
        assert experiment_runner.parse_metrics(
            "noise\n{\"metrics\": {\"acc\": 1}}\n"
        ) == {"metrics": {"acc": 1}}
        assert experiment_runner.parse_metrics("plain") == {}

    def test_run_executes_script_and_writes_results(self, monkeypatch):
        store = FakeStore()
        prefix = "gs://bucket/staging/exp1"
        store.objects[f"{prefix}/script.py"] = (
            b"import json\nprint(json.dumps({'metrics': {'accuracy': 0.5}}))\n"
        )
        store.objects[f"{prefix}/inputs.json"] = b'{"seed": 3}'
        monkeypatch.setattr(experiment_runner, "_Store", lambda: store)
        monkeypatch.setenv("EXPERIMENT_TIMEOUT_S", "30")

        exit_code = experiment_runner.run(f"{prefix}/script.py")

        assert exit_code == 0
        assert json.loads(store.objects[f"{prefix}/result.json"])["exit_code"] == 0
        assert json.loads(store.objects[f"{prefix}/metrics.json"]) == {
            "metrics": {"accuracy": 0.5}
        }
        assert b"accuracy" in store.objects[f"{prefix}/stdout.txt"]

    def test_run_prints_a_start_line_immediately(self, monkeypatch, capsys):
        store = FakeStore()
        prefix = "gs://bucket/staging/exp1"
        store.objects[f"{prefix}/script.py"] = b"print('hi')\n"
        monkeypatch.setattr(experiment_runner, "_Store", lambda: store)
        monkeypatch.setenv("EXPERIMENT_TIMEOUT_S", "30")

        experiment_runner.run(f"{prefix}/script.py")

        err = capsys.readouterr().err
        assert "experiment-runner: start uri=gs://bucket/staging/exp1/script.py" in err
        assert "stage=download" in err
        assert "stage=exec" in err
        assert "stage=upload" in err
        assert "done exit_code=0" in err

    def test_download_failure_names_the_stage(self, monkeypatch):
        class _FailingDownload(FakeStore):
            def download(self, uri: str):
                raise TimeoutError("connect timed out")

        import pytest

        monkeypatch.setattr(experiment_runner, "_Store", lambda: _FailingDownload())

        with pytest.raises(RuntimeError, match="download stage failed"):
            experiment_runner.run("gs://bucket/staging/exp1/script.py")

    def test_upload_failure_names_the_stage(self, monkeypatch):
        class _FailingUpload(FakeStore):
            def upload(self, uri: str, data: bytes, content_type: str) -> None:
                raise TimeoutError("write timed out")

        import pytest

        store = _FailingUpload()
        prefix = "gs://bucket/staging/exp1"
        store.objects[f"{prefix}/script.py"] = b"print('hi')\n"
        monkeypatch.setattr(experiment_runner, "_Store", lambda: store)
        monkeypatch.setenv("EXPERIMENT_TIMEOUT_S", "30")

        with pytest.raises(RuntimeError, match="upload stage failed"):
            experiment_runner.run(f"{prefix}/script.py")

    def test_missing_script_is_not_a_stage_failure(self, monkeypatch):
        import pytest

        store = FakeStore()
        monkeypatch.setattr(experiment_runner, "_Store", lambda: store)

        with pytest.raises(FileNotFoundError, match="script not found"):
            experiment_runner.run("gs://bucket/staging/missing/script.py")


class TestRunnerEndpointResolution:
    """The experiment Job's private path is probed, never assumed."""

    def test_default_endpoint_skips_the_probe(self, monkeypatch):
        monkeypatch.delenv("EXPERIMENT_GCS_ENDPOINT", raising=False)

        def _boom(endpoint, timeout):
            raise AssertionError("the public default must not be probed")

        monkeypatch.setattr(experiment_runner, "_probe_endpoint", _boom)
        endpoint, used_fallback = experiment_runner._resolve_endpoint()
        assert endpoint == "https://storage.googleapis.com"
        assert used_fallback is False

    def test_reachable_private_endpoint_is_used(self, monkeypatch):
        monkeypatch.setenv(
            "EXPERIMENT_GCS_ENDPOINT", "https://private.googleapis.com"
        )
        monkeypatch.setattr(
            experiment_runner, "_probe_endpoint", lambda endpoint, timeout: True
        )
        endpoint, used_fallback = experiment_runner._resolve_endpoint()
        assert endpoint == "https://private.googleapis.com"
        assert used_fallback is False

    def test_unreachable_private_endpoint_falls_back_to_public(self, monkeypatch):
        monkeypatch.setenv(
            "EXPERIMENT_GCS_ENDPOINT", "https://private.googleapis.com"
        )
        monkeypatch.setenv(
            "EXPERIMENT_GCS_ENDPOINT_FALLBACK", "https://storage.googleapis.com"
        )
        monkeypatch.setattr(
            experiment_runner,
            "_probe_endpoint",
            lambda endpoint, timeout: endpoint
            == "https://storage.googleapis.com",
        )
        endpoint, used_fallback = experiment_runner._resolve_endpoint()
        assert endpoint == "https://storage.googleapis.com"
        assert used_fallback is True

    def test_both_endpoints_unreachable_fails_fast(self, monkeypatch):
        import pytest

        monkeypatch.setenv(
            "EXPERIMENT_GCS_ENDPOINT", "https://private.googleapis.com"
        )
        monkeypatch.setenv(
            "EXPERIMENT_GCS_ENDPOINT_FALLBACK", "https://storage.googleapis.com"
        )
        monkeypatch.setattr(
            experiment_runner, "_probe_endpoint", lambda endpoint, timeout: False
        )
        with pytest.raises(RuntimeError, match="no reachable GCS endpoint"):
            experiment_runner._resolve_endpoint()
