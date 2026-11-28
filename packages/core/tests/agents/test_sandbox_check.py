"""The deploy-time ``sandbox-check`` probe.

No network and no GCP: the object store and the Run Admin client are fakes.
The probe is the deploy's early warning for the #169 failure class - the
process that runs the sandbox cannot reach the experiment bucket/Job it was
handed. The tests pin the category reported for each phase.
"""

from __future__ import annotations

import json

import pytest
from agentic_kg.agents.config import SandboxConfig
from agentic_kg.agents.sandbox import (
    SandboxCheckReport,
    classify_sandbox_error,
    resolve_gcs_endpoint,
    run_sandbox_check,
)
from google.api_core.exceptions import Forbidden, NotFound


class CheckStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_upload: BaseException | None = None
        self.fail_delete: BaseException | None = None
        self.deleted: list[str] = []

    def upload_bytes(
        self, uri: str, data: bytes, content_type: str | None = None
    ) -> None:
        if self.fail_upload is not None:
            raise self.fail_upload
        self.objects[uri] = data

    def download_bytes(self, uri: str) -> bytes | None:
        return self.objects.get(uri)

    def delete_bytes(self, uri: str) -> bool:
        if self.fail_delete is not None:
            raise self.fail_delete
        if uri in self.objects:
            del self.objects[uri]
            self.deleted.append(uri)
            return True
        return False


class CheckRun:
    def __init__(self, store: CheckStore, *, status: str = "success") -> None:
        self.store = store
        self.status = status
        self.calls: list[dict] = []
        self.raise_on_run: BaseException | None = None

    def run_job(
        self, job_resource_name: str, *, args: list[str], env: dict[str, str]
    ) -> str:
        self.calls.append({"job": job_resource_name, "args": args, "env": env})
        if self.raise_on_run is not None:
            raise self.raise_on_run
        prefix = args[0].rsplit("/", 1)[0]
        exit_code = 0 if self.status == "success" else 1
        self.store.objects[f"{prefix}/stdout.txt"] = b'{"metrics": {"sandbox_check": 1}}'
        self.store.objects[f"{prefix}/stderr.txt"] = b"" if exit_code == 0 else b"boom"
        self.store.objects[f"{prefix}/metrics.json"] = b'{"metrics": {"sandbox_check": 1}}'
        self.store.objects[f"{prefix}/result.json"] = json.dumps(
            {"exit_code": exit_code, "timed_out": False}
        ).encode()
        return "projects/p/locations/r/executions/e1"

    def get_execution(self, execution_name: str) -> dict:
        if self.status == "success":
            return {
                "status": {"completionTime": "x", "succeededCount": 1}
            }
        if self.status == "failure":
            return {
                "status": {
                    "completionTime": "x",
                    "failedCount": 1,
                    "conditions": [
                        {"type": "Completed", "state": "CONDITION_FAILED"}
                    ],
                }
            }
        return {"status": {"startTime": "x"}}


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
        "poll_interval_seconds": 1,
        "gcs_endpoint": "https://storage.googleapis.com",
        "gcs_endpoint_fallback": "https://storage.googleapis.com",
    }
    values.update(overrides)
    return SandboxConfig(**values)


def test_success_uploads_runs_polls_reads_back_and_cleans_up():
    store = CheckStore()
    run = CheckRun(store, status="success")

    report = run_sandbox_check(
        _config(), store=store, run_client=run, timeout_s=30
    )

    assert report.ok is True
    assert report.category == "ok"
    assert report.exit_code == 0
    assert "sandbox_check" in report.stdout
    assert report.result_uri.endswith(report.experiment_id)
    assert report.cleanup.startswith("deleted 6 object(s)")
    assert store.objects == {}
    assert run.calls[0]["job"].endswith("/jobs/agentic-kg-experiment-staging")
    assert run.calls[0]["env"]["EXPERIMENT_ID"] == report.experiment_id
    assert run.calls[0]["env"]["EXPERIMENT_TIMEOUT_S"] == "25"


def test_upload_connectivity_failure_is_reported_as_connectivity():
    store = CheckStore()
    store.fail_upload = TimeoutError("timed out")
    report = run_sandbox_check(
        _config(), store=store, run_client=CheckRun(store)
    )
    assert report.ok is False
    assert report.category == "connectivity"
    assert "upload failed" in report.message


def test_upload_permission_failure_is_reported_as_permission():
    store = CheckStore()
    store.fail_upload = Forbidden("403 caller does not have storage.objects.create")
    report = run_sandbox_check(
        _config(), store=store, run_client=CheckRun(store)
    )
    assert report.ok is False
    assert report.category == "permission"


def test_starting_the_job_can_fail_with_permission():
    store = CheckStore()
    run = CheckRun(store)
    run.raise_on_run = Forbidden("403 run.jobs.run denied")
    report = run_sandbox_check(_config(), store=store, run_client=run)
    assert report.ok is False
    assert report.category == "permission"
    assert report.cleanup.startswith("deleted 2 object(s)")


def test_a_failed_experiment_is_reported_as_job_failed():
    store = CheckStore()
    run = CheckRun(store, status="failure")
    report = run_sandbox_check(_config(), store=store, run_client=run)
    assert report.ok is False
    assert report.category == "job_failed"
    assert report.exit_code == 1
    assert "boom" in report.message


def test_a_poll_timeout_is_reported_as_job_timeout():
    store = CheckStore()
    run = CheckRun(store, status="running")
    clock = FakeClock()
    report = run_sandbox_check(
        _config(),
        store=store,
        run_client=run,
        clock=clock,
        sleep=clock.sleep,
        timeout_s=2,
    )
    assert report.ok is False
    assert report.category == "job_timeout"


def test_cleanup_without_delete_permission_is_reported_not_raised():
    store = CheckStore()
    store.fail_delete = Forbidden("403 storage.objects.delete denied")
    report = run_sandbox_check(
        _config(), store=store, run_client=CheckRun(store), timeout_s=30
    )
    assert report.ok is True
    assert "no storage.objects.delete permission" in report.cleanup


class TestEndpointResolution:
    def test_default_endpoint_skips_the_probe(self):
        calls: list[tuple[str, float]] = []

        endpoint, used_fallback = resolve_gcs_endpoint(
            _config(gcs_endpoint="https://storage.googleapis.com"),
            probe=lambda e, t: calls.append((e, t)) or False,
        )

        assert endpoint == "https://storage.googleapis.com"
        assert used_fallback is False
        assert calls == []

    def test_a_reachable_private_endpoint_is_used(self):
        endpoint, used_fallback = resolve_gcs_endpoint(
            _config(gcs_endpoint="https://private.googleapis.com"),
            probe=lambda e, t: True,
        )
        assert endpoint == "https://private.googleapis.com"
        assert used_fallback is False

    def test_an_unreachable_private_endpoint_falls_back_to_public(self):
        endpoint, used_fallback = resolve_gcs_endpoint(
            _config(gcs_endpoint="https://private.googleapis.com"),
            probe=lambda e, t: False,
        )
        assert endpoint == "https://storage.googleapis.com"
        assert used_fallback is True

    @pytest.mark.parametrize(
        ("exc", "category"),
        [
            (Forbidden("403"), "permission"),
            (NotFound("404"), "not_found"),
            (TimeoutError("timed out"), "connectivity"),
            (
                RuntimeError(
                    "RetryError: Timeout of 120.0s exceeded, last exception: "
                    "Max retries exceeded with url"
                ),
                "connectivity",
            ),
            (RuntimeError("boom"), "error"),
        ],
    )
    def test_error_classification(self, exc, category):
        assert classify_sandbox_error(exc) == category


def test_cli_prints_a_compact_one_line_verdict(monkeypatch, capsys):
    """The deploy step greps one stable line; it must not carry stdout/stderr."""
    from types import SimpleNamespace

    from agentic_kg import cli

    report = SandboxCheckReport(
        ok=False,
        category="job_timeout",
        message="Cloud Run Job execution exceeded 330s",
        endpoint="https://private.googleapis.com",
        used_fallback=False,
        job="projects/p/locations/r/jobs/j",
        stdout="a very large stdout should not be in the line",
        stderr="a very large stderr should not be in the line",
    )

    monkeypatch.setattr(
        "agentic_kg.agents.sandbox.run_sandbox_check",
        lambda config, timeout_s=None: report,
    )
    args = SimpleNamespace(
        backend="cloudrun_job",
        bucket="bucket",
        prefix="staging",
        job="j",
        project="project",
        region="us-central1",
        timeout=None,
        json_output=False,
    )

    with pytest.raises(SystemExit):
        cli.run_sandbox_check(args)

    out = capsys.readouterr().out
    line = next(
        line for line in out.splitlines() if line.startswith("SANDBOX_CHECK_VERDICT ")
    )
    payload = json.loads(line[len("SANDBOX_CHECK_VERDICT "):])
    assert payload["ok"] is False
    assert payload["category"] == "job_timeout"
    assert "stdout" not in payload
    assert "stderr" not in payload
