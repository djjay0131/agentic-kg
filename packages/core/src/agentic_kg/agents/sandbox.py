"""
Sandboxed code execution backends for the Evaluation Agent.

``DockerSandbox`` runs generated scripts in a local Docker container and is the
development default. ``CloudRunJobBackend`` runs them in a dedicated Cloud Run
Job, because a Cloud Run service has no Docker daemon.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable
from urllib.parse import urlparse

from agentic_kg.agents.config import SandboxConfig, get_agent_config

logger = logging.getLogger(__name__)

_GS_SCHEME = "gs://"

#: The public GCS endpoint. Cloud Run managed egress reaches it even when the
#: service/Job uses Direct VPC egress with ``PRIVATE_RANGES_ONLY``; the private
#: VIP (``private.googleapis.com``) is only reachable from a network with a
#: Private Google Access path, which the application VPC does not provide here.
_DEFAULT_GCS_ENDPOINT = "https://storage.googleapis.com"

#: A tiny script the deploy-time sandbox check executes end to end.
_SANDBOX_CHECK_SCRIPT = (
    "import json\n"
    "print(json.dumps({'metrics': {'sandbox_check': 1}}))\n"
)

#: Extra time the API polls past the per-run budget. The Job's own platform
#: timeout is the same budget, so polling exactly to the deadline can observe
#: "not finished" a moment before the platform records the execution as
#: finished and hides the real ``timed_out`` result.
_JOB_POLL_MARGIN_SECONDS = 30


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _normalise_endpoint(endpoint: str | None) -> str:
    return (endpoint or _DEFAULT_GCS_ENDPOINT).rstrip("/")


def _probe_tcp(endpoint: str, timeout: float) -> bool:
    """Return whether a TCP connection to ``endpoint`` succeeds quickly.

    A blocked egress path manifests as a connect timeout, so a plain TCP probe
    is enough to tell "this runtime cannot reach this endpoint" apart from an
    application-level 403/404. TLS and HTTP are left to the real request.
    """
    parsed = urlparse(endpoint)
    host = parsed.hostname
    if not host:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError as exc:
        logger.warning(
            "GCS endpoint %s is not reachable from this runtime within %.1fs: %s",
            endpoint,
            timeout,
            exc,
        )
        return False


def resolve_gcs_endpoint(
    config: SandboxConfig,
    *,
    probe: Any = None,
) -> tuple[str, bool]:
    """Pick the GCS endpoint this *process* can reach.

    Returns ``(endpoint, used_fallback)``. The configured endpoint is used when
    it is the public default or the probe succeeds. Otherwise the backend logs
    the failed probe and falls back to the public endpoint, which Cloud Run
    managed egress reaches even on the application VPC. This is why a value
    persisted by the API must never be forced onto a worker Job whose network
    and identity differ.
    """
    configured = _normalise_endpoint(config.gcs_endpoint)
    fallback = _normalise_endpoint(config.gcs_endpoint_fallback)
    if configured == fallback or configured == _DEFAULT_GCS_ENDPOINT:
        return configured, False
    probe = probe or _probe_tcp
    if probe(configured, config.gcs_connect_timeout_seconds):
        return configured, False
    logger.warning(
        "Falling back from GCS endpoint %s to %s for this runtime",
        configured,
        fallback,
    )
    return fallback, True


def _job_resource_name(config: SandboxConfig) -> str:
    job = config.job_name or f"agentic-kg-experiment-{_env_name()}"
    return (
        f"projects/{config.project_id}/locations/{config.region}"
        f"/jobs/{job}"
    )


def _prefix_for(config: SandboxConfig, experiment_id: str) -> str:
    prefix = config.gcs_prefix.strip("/")
    parts = [part for part in (prefix, experiment_id) if part]
    return f"{_GS_SCHEME}{config.gcs_bucket}/{'/'.join(parts)}"


@dataclass
class SandboxResult:
    """Result of a sandboxed code execution."""

    success: bool
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    metrics: Optional[dict] = None

    #: Whether the *experiment infrastructure* failed to run the script, as
    #: opposed to the experiment running and producing a negative result. This
    #: is what lets the review gate say "the science was never tested" instead
    #: of reporting a misleading ``not_viable`` verdict.
    infrastructure_failure: bool = False
    #: Stage the failure happened in: ``start`` (dispatch/backend), ``poll``
    #: (execution never completed), ``download``/``exec``/``upload`` (runner),
    #: or ``collect`` (reading results back).
    failure_stage: Optional[str] = None
    #: Deploy-readable failure class (``connectivity``, ``permission``,
    #: ``not_found``, ``job_timeout``, ``job_failed`` or ``error``).
    failure_category: Optional[str] = None

    # Reproducibility provenance (null when a backend cannot supply it).
    experiment_id: Optional[str] = None
    result_uri: Optional[str] = None
    code_hash: Optional[str] = None
    image_digest: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    backend: Optional[str] = None

    def parse_metrics(self) -> dict:
        """Try to parse JSON metrics from stdout."""
        if self.metrics:
            return self.metrics
        for line in reversed(self.stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                    self.metrics = data
                    return data
                except json.JSONDecodeError:
                    continue
        return {}


@runtime_checkable
class ExecutionBackend(Protocol):
    """A backend that can execute generated Python code in isolation."""

    def execute(
        self,
        code: str,
        *,
        timeout_s: int | None = None,
        mem_mb: int | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> SandboxResult:
        """Execute ``code`` and return captured output and provenance."""


def _write_inputs(inputs: dict[str, Any] | None, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, value in (inputs or {}).items():
        target = directory / Path(name).name
        if isinstance(value, (dict, list)):
            target.write_text(json.dumps(value), encoding="utf-8")
        elif isinstance(value, bytes):
            target.write_bytes(value)
        else:
            target.write_text(str(value), encoding="utf-8")


class DockerSandbox:
    """
    Executes Python scripts in isolated Docker containers.

    Security measures:
    - No network access (network_disabled=True)
    - Memory limit (default 2GB)
    - CPU limit (default 1 core)
    - Time limit (default 5 minutes)
    - Read-only root filesystem
    - Non-root user
    - Only /tmp is writable
    """

    def __init__(self, config: Optional[SandboxConfig] = None) -> None:
        self.config = config or get_agent_config().sandbox
        self._client = None

    def _get_client(self):
        """Lazy-load Docker client."""
        if self._client is None:
            try:
                import docker

                self._client = docker.from_env()
            except ImportError:
                raise RuntimeError(
                    "docker package required for sandbox execution. "
                    "Install with: pip install docker"
                )
            except Exception as e:
                raise RuntimeError(f"Failed to connect to Docker: {e}")
        return self._client

    def execute(
        self,
        code: str,
        pip_packages: Optional[list[str]] = None,
        *,
        timeout_s: int | None = None,
        mem_mb: int | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> SandboxResult:
        """
        Execute a Python script in a sandboxed container.

        Args:
            code: Python source code to execute.
            pip_packages: Additional pip packages to install (beyond defaults).
            timeout_s: Per-run timeout override.
            mem_mb: Per-run memory limit override, in MiB.
            inputs: Files exposed read-only at ``/mnt/inputs``.

        Returns:
            SandboxResult with stdout, stderr, exit code, and parsed metrics.
        """
        client = self._get_client()
        packages = list(self.config.pip_packages)
        if pip_packages:
            packages.extend(pip_packages)

        setup_script = ""
        if packages:
            pkg_str = " ".join(packages)
            setup_script = f"pip install --quiet {pkg_str} 2>/dev/null && "

        timeout = timeout_s or self.config.timeout_seconds
        memory = f"{mem_mb}m" if mem_mb else self.config.memory_limit
        experiment_id = uuid.uuid4().hex
        started_at = _utc_now()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, prefix="sandbox_"
        ) as f:
            f.write(code)
            script_path = f.name

        input_dir = Path(tempfile.mkdtemp(prefix="sandbox_inputs_"))
        _write_inputs(inputs, input_dir)
        container = None

        try:
            volumes = {
                script_path: {"bind": "/tmp/script.py", "mode": "ro"},
                str(input_dir): {"bind": "/mnt/inputs", "mode": "ro"},
            }
            container = client.containers.run(
                image=self.config.image,
                command=f"/bin/sh -c '{setup_script}python /tmp/script.py'",
                volumes=volumes,
                environment={"EXPERIMENT_INPUT_DIR": "/mnt/inputs"},
                network_disabled=self.config.network_disabled,
                mem_limit=memory,
                cpu_quota=int(self.config.cpu_limit * 100000),
                read_only=self.config.read_only_rootfs,
                tmpfs={"/tmp": "size=256m"},
                user="nobody",
                detach=True,
                remove=False,
            )

            result = container.wait(timeout=timeout)
            exit_code = result.get("StatusCode", -1)

            stdout = container.logs(stdout=True, stderr=False).decode(
                "utf-8", errors="replace"
            )
            stderr = container.logs(stdout=False, stderr=True).decode(
                "utf-8", errors="replace"
            )

            max_output = self.config.max_output_chars
            if len(stdout) > max_output:
                stdout = stdout[:max_output] + "\n... (truncated)"
            if len(stderr) > max_output:
                stderr = stderr[:max_output] + "\n... (truncated)"

            container.remove(force=True)
            container = None

            sandbox_result = SandboxResult(
                success=exit_code == 0,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                experiment_id=experiment_id,
                code_hash=_code_hash(code),
                image_digest=_digest_or_none(self.config.image),
                started_at=started_at,
                finished_at=_utc_now(),
                backend="docker",
            )
            sandbox_result.parse_metrics()
            return sandbox_result

        except Exception as e:
            error_msg = str(e)
            timed_out = "timed out" in error_msg.lower() or "timeout" in error_msg.lower()

            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass

            logger.error(f"Sandbox execution failed: {e}")
            return SandboxResult(
                success=False,
                stdout="",
                stderr=error_msg,
                exit_code=-1,
                timed_out=timed_out,
                experiment_id=experiment_id,
                code_hash=_code_hash(code),
                image_digest=_digest_or_none(self.config.image),
                started_at=started_at,
                finished_at=_utc_now(),
                backend="docker",
            )
        finally:
            try:
                Path(script_path).unlink(missing_ok=True)
            except Exception:
                pass
            try:
                for item in input_dir.iterdir():
                    item.unlink(missing_ok=True)
                input_dir.rmdir()
            except Exception:
                pass


def _digest_or_none(image: str | None) -> str | None:
    if image and "@sha256:" in image:
        return image.split("@", 1)[1]
    return None


class CloudRunJobBackend:
    """Execute generated code in a dedicated Cloud Run Job via GCS.

    The script and inputs are uploaded to a per-experiment GCS prefix. The Job
    receives that ``gs://`` path as its only argument, runs the script, and
    writes ``stdout.txt`` / ``stderr.txt`` / ``metrics.json`` / ``result.json``
    back to the same prefix. The API never mounts the bucket and never passes
    code through command-line arguments or environment variables.
    """

    def __init__(
        self,
        config: Optional[SandboxConfig] = None,
        *,
        store: Any = None,
        run_client: Any = None,
        sleep: Any = time.sleep,
        clock: Any = time.monotonic,
    ) -> None:
        self.config = config or get_agent_config().sandbox
        if not self.config.gcs_bucket:
            raise ValueError(
                "EXPERIMENT_GCS_BUCKET is required for the cloudrun_job backend"
            )
        self._store = store or GcsObjectStore(self.config)
        self._run = run_client or CloudRunJobClient(self.config)
        self._sleep = sleep
        self._clock = clock

    @property
    def job_resource_name(self) -> str:
        return _job_resource_name(self.config)

    def execute(
        self,
        code: str,
        *,
        timeout_s: int | None = None,
        mem_mb: int | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> SandboxResult:
        del mem_mb  # CPU/memory are Job-level caps, not per-execution overrides.
        timeout = timeout_s or self.config.timeout_seconds
        experiment_id = uuid.uuid4().hex
        prefix = self._prefix(experiment_id)
        script_uri = f"{prefix}/script.py"
        inputs_uri = f"{prefix}/inputs.json"
        result_uri = prefix
        started_at = _utc_now()

        try:
            # The upload runs in the process that owns the sandbox (the API or
            # the workflow worker Job). Its endpoint was resolved for *this*
            # runtime, and the storage client uses a short timeout with no
            # retry, so a blocked path fails fast instead of a 120 s RetryError.
            self._store.upload_bytes(
                script_uri, code.encode("utf-8"), content_type="text/x-python"
            )
            self._store.upload_bytes(
                inputs_uri,
                json.dumps(inputs or {}, default=str).encode("utf-8"),
                content_type="application/json",
            )
            execution_name = self._run.run_job(
                self.job_resource_name,
                args=[script_uri],
                env={
                    "EXPERIMENT_ID": experiment_id,
                    "EXPERIMENT_RESULT_URI": result_uri,
                    # Leave a small margin so the runner can upload partial
                    # output before the Job-level cap kills the container.
                    "EXPERIMENT_TIMEOUT_S": str(max(1, timeout - 5)),
                },
            )
            execution = self._poll(
                execution_name,
                timeout + _JOB_POLL_MARGIN_SECONDS,
                result_uri=result_uri,
            )
        except TimeoutError as exc:
            try:
                partial_stdout = self._read_text(f"{prefix}/stdout.txt")
            except Exception:  # noqa: BLE001 - the timeout is the result
                partial_stdout = ""
            status_note = self._status_note(execution_name)
            # A result object means the experiment script finished; a missing
            # one means the Job never produced output (schedule, image pull,
            # GCS/network), which is an infrastructure failure, not a result.
            job_result = self._result_json(result_uri)
            if job_result:
                return SandboxResult(
                    success=False,
                    stdout=partial_stdout,
                    stderr=f"{exc}{status_note}",
                    exit_code=int(job_result.get("exit_code", -1)),
                    timed_out=bool(job_result.get("timed_out", False)),
                    experiment_id=experiment_id,
                    result_uri=result_uri,
                    code_hash=_code_hash(code),
                    image_digest=self.config.image_digest,
                    started_at=started_at,
                    finished_at=_utc_now(),
                    backend="cloudrun_job",
                )
            # A runner-written error.json is the cause even if the platform
            # operation never reports terminal (it normally does now).
            runner_error = self._error_json(result_uri)
            if runner_error:
                detail = self._runner_error_detail(runner_error)
                return SandboxResult(
                    success=False,
                    stdout=partial_stdout,
                    stderr=f"{exc}{status_note} {detail}".strip(),
                    exit_code=-1,
                    timed_out=False,
                    infrastructure_failure=True,
                    failure_stage=str(runner_error.get("stage") or "runner"),
                    failure_category=str(
                        runner_error.get("category") or "job_failed"
                    ),
                    experiment_id=experiment_id,
                    result_uri=result_uri,
                    code_hash=_code_hash(code),
                    image_digest=self.config.image_digest,
                    started_at=started_at,
                    finished_at=_utc_now(),
                    backend="cloudrun_job",
                )
            return SandboxResult(
                success=False,
                stdout=partial_stdout,
                stderr=f"{exc}{status_note}",
                exit_code=-1,
                timed_out=True,
                infrastructure_failure=True,
                failure_stage="poll",
                failure_category="job_timeout",
                experiment_id=experiment_id,
                result_uri=result_uri,
                code_hash=_code_hash(code),
                image_digest=self.config.image_digest,
                started_at=started_at,
                finished_at=_utc_now(),
                backend="cloudrun_job",
            )
        except Exception as exc:
            logger.error("Cloud Run Job execution could not start: %s", exc)
            return SandboxResult(
                success=False,
                stdout="",
                stderr=str(exc),
                exit_code=-1,
                infrastructure_failure=True,
                failure_stage="start",
                failure_category=classify_sandbox_error(exc),
                experiment_id=experiment_id,
                result_uri=result_uri,
                code_hash=_code_hash(code),
                image_digest=self.config.image_digest,
                started_at=started_at,
                finished_at=_utc_now(),
                backend="cloudrun_job",
            )

        return self._collect(
            execution,
            experiment_id=experiment_id,
            result_uri=result_uri,
            code=code,
            started_at=started_at,
        )

    def _prefix(self, experiment_id: str) -> str:
        return _prefix_for(self.config, experiment_id)

    def _poll(
        self, execution_name: str, timeout: float, *, result_uri: str | None = None
    ) -> dict:
        return _poll_execution(
            self._run,
            execution_name,
            timeout,
            clock=self._clock,
            sleep=self._sleep,
            interval=self.config.poll_interval_seconds,
            object_store=self._store,
            result_uri=result_uri,
        )

    def _read_text(self, uri: str) -> str:
        data = self._store.download_bytes(uri)
        return data.decode("utf-8", errors="replace") if data is not None else ""

    def _status_note(self, execution_name: str) -> str:
        """A short execution-status summary for a timeout message; best effort."""
        try:
            status = _execution_view(self._run.get_execution(execution_name) or {})
        except Exception:  # noqa: BLE001 - the timeout is still the result
            return ""
        parts = [
            f"start={status.get('startTime') or 'unknown'}",
            f"completion={status.get('completionTime') or 'unfinished'}",
            f"succeeded={status.get('succeededCount') or 0}",
            f"failed={status.get('failedCount') or 0}",
            f"cancelled={status.get('cancelledCount') or 0}",
            f"running={status.get('runningCount') or 0}",
        ]
        for condition in status.get("conditions") or []:
            if not isinstance(condition, dict):
                continue
            parts.append(
                f"{condition.get('type')}="
                f"{condition.get('state') or condition.get('status')}"
                f"({condition.get('message') or ''})"
            )
        return " (" + ", ".join(parts) + ")"

    def _result_json(self, result_uri: str) -> dict:
        """Read ``result.json`` or return ``{}``; never raises."""
        return self._read_json_object(f"{result_uri}/result.json")

    def _error_json(self, result_uri: str) -> dict:
        """Read the runner's ``error.json`` or return ``{}``; never raises."""
        return self._read_json_object(f"{result_uri}/error.json")

    def _read_json_object(self, uri: str) -> dict:
        try:
            raw = self._store.download_bytes(uri)
        except Exception:  # noqa: BLE001 - an unreadable object is no object
            return {}
        if not raw:
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _runner_error_detail(error: dict) -> str:
        """One readable line from the runner's ``error.json``."""
        parts = [str(error.get("message") or "").strip()]
        status = error.get("status")
        if status:
            parts.append(f"status={status}")
        body = str(error.get("body_excerpt") or "").strip()
        if body:
            parts.append(f"body={body}")
        last_log = str(error.get("last_log") or "").strip()
        if last_log:
            parts.append(f"last_log={last_log}")
        return " | ".join(part for part in parts if part)

    def _collect(
        self,
        execution: dict,
        *,
        experiment_id: str,
        result_uri: str,
        code: str,
        started_at: datetime,
    ) -> SandboxResult:
        stdout = self._read_text(f"{result_uri}/stdout.txt")
        stderr = self._read_text(f"{result_uri}/stderr.txt")
        raw_result = self._store.download_bytes(f"{result_uri}/result.json")
        job_result: dict = {}
        if raw_result:
            try:
                job_result = json.loads(raw_result.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                logger.warning("Could not parse experiment result.json")

        succeeded = execution_succeeded(execution)
        exit_code = int(job_result.get("exit_code", 0 if succeeded else 1))
        timed_out = bool(job_result.get("timed_out", False))
        # The runner writes result.json itself, so an object that says exit 0
        # is the outcome. Requiring the control-plane status to agree is what
        # turned #172's finished execution into a 330 s timeout; the status is
        # a hint, the object store is the contract.
        success = bool(raw_result) and exit_code == 0 and not timed_out

        # No result object at all means the runner never reached its upload
        # stage: the platform or the network failed before the experiment could
        # run (or the result could be read back). Report that as infrastructure,
        # not as science. If the runner wrote error.json, its named stage and
        # category are the cause (e.g. download/permission); otherwise the
        # execution's own Completed condition is the best evidence available.
        infrastructure_failure = not raw_result
        failure_category: Optional[str] = "job_failed" if infrastructure_failure else None
        failure_stage: Optional[str] = "collect" if infrastructure_failure else None
        if infrastructure_failure:
            runner_error = self._error_json(result_uri)
            if runner_error:
                stage = str(runner_error.get("stage") or "collect")
                failure_stage = stage
                failure_category = str(
                    runner_error.get("category") or "job_failed"
                )
                detail = self._runner_error_detail(runner_error)
                if detail and detail not in stderr:
                    stderr = (stderr + "\n" if stderr else "") + detail
            else:
                detail = execution_condition_message(execution)
                if detail and detail not in stderr:
                    stderr = (stderr + "\n" if stderr else "") + detail
                if not stderr:
                    stderr = (
                        "experiment Job finished without writing result.json "
                        f"(execution status: {_execution_view(execution)})"
                    )

        max_output = self.config.max_output_chars
        if len(stdout) > max_output:
            stdout = stdout[:max_output] + "\n... (truncated)"
        if len(stderr) > max_output:
            stderr = stderr[:max_output] + "\n... (truncated)"

        metrics = None
        raw_metrics = self._store.download_bytes(f"{result_uri}/metrics.json")
        if raw_metrics:
            try:
                metrics = json.loads(raw_metrics.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                logger.warning("Could not parse experiment metrics.json")

        result = SandboxResult(
            success=success,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            timed_out=timed_out,
            metrics=metrics,
            infrastructure_failure=infrastructure_failure,
            failure_stage=failure_stage,
            failure_category=failure_category,
            experiment_id=experiment_id,
            result_uri=result_uri,
            code_hash=_code_hash(code),
            image_digest=self.config.image_digest or image_digest_from_execution(execution),
            started_at=started_at,
            finished_at=_utc_now(),
            backend="cloudrun_job",
        )
        result.parse_metrics()
        return result


class GcsObjectStore:
    """Small GCS wrapper so the backend can be unit-tested with a fake."""

    def __init__(self, config: SandboxConfig, *, probe: Any = None) -> None:
        self._config = config
        self._client = None
        self._probe = probe
        self._endpoint: str | None = None
        self._used_fallback = False

    @property
    def endpoint(self) -> str:
        """The endpoint this process resolved (probing if not done yet)."""
        if self._endpoint is None:
            self._endpoint, self._used_fallback = resolve_gcs_endpoint(
                self._config, probe=self._probe
            )
        return self._endpoint

    @property
    def used_fallback(self) -> bool:
        _ = self.endpoint
        return self._used_fallback

    @property
    def _timeout(self) -> float:
        return self._config.gcs_request_timeout_seconds

    def _client_or_create(self):
        if self._client is None:
            from google.cloud import storage

            options = {}
            if self._config.project_id:
                options["project"] = self._config.project_id
            endpoint = self.endpoint
            if endpoint:
                options["client_options"] = {"api_endpoint": endpoint}
            self._client = storage.Client(**options)
        return self._client

    def _blob(self, uri: str):
        if not uri.startswith(_GS_SCHEME):
            raise ValueError(f"Not a gs:// URI: {uri}")
        bucket_name, _, key = uri[len(_GS_SCHEME):].partition("/")
        bucket = self._client_or_create().bucket(bucket_name)
        return bucket.blob(key)

    def upload_bytes(
        self, uri: str, data: bytes, content_type: str | None = None
    ) -> None:
        # retry=None: do not hide a blocked route behind a long client-side
        # retry loop. The endpoint probe already chose a reachable endpoint.
        self._blob(uri).upload_from_string(
            data,
            content_type=content_type,
            timeout=self._timeout,
            retry=None,
        )

    def download_bytes(self, uri: str) -> bytes | None:
        blob = self._blob(uri)
        if not blob.exists(timeout=self._timeout, retry=None):
            return None
        return blob.download_as_bytes(timeout=self._timeout, retry=None)

    def delete_bytes(self, uri: str) -> bool:
        """Delete one object. Returns False when it was already absent.

        Missing ``storage.objects.delete`` permission raises ``Forbidden``; the
        caller decides whether that is fatal. Provenance objects have a 30-day
        lifecycle rule, so a skipped cleanup is harmless.
        """
        from google.api_core.exceptions import NotFound

        blob = self._blob(uri)
        try:
            blob.delete(timeout=self._timeout, retry=None)
            return True
        except NotFound:
            return False


class CloudRunJobClient:
    """Minimal Run Admin v2 client (``:run`` + executions get)."""

    def __init__(self, config: SandboxConfig) -> None:
        self._config = config

    def _base_url(self) -> str:
        return f"https://{self._config.region}-run.googleapis.com/v2"

    def _token(self) -> str:
        import google.auth
        from google.auth.transport.requests import Request

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        credentials.refresh(Request())
        return credentials.token

    def run_job(
        self, job_resource_name: str, *, args: list[str], env: dict[str, str]
    ) -> str:
        import httpx

        body = {
            "overrides": {
                "containerOverrides": [
                    {
                        "args": args,
                        "env": [
                            {"name": name, "value": value}
                            for name, value in env.items()
                        ],
                    }
                ]
            }
        }
        response = httpx.post(
            f"{self._base_url()}/{job_resource_name}:run",
            headers={"Authorization": f"Bearer {self._token()}"},
            json=body,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()["name"]

    def get_execution(self, execution_name: str) -> dict:
        import httpx

        response = httpx.get(
            f"{self._base_url()}/{execution_name}",
            headers={"Authorization": f"Bearer {self._token()}"},
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


#: Marker set on an execution payload when the experiment objects themselves
#: (``result.json`` / ``error.json``) prove completion. The control-plane status
#: can lag or be shaped differently; the object store is the contract.
_OBJECT_COMPLETE_KEY = "_sandbox_object_complete"

#: Execution states the Run Admin v2 API may report at the top level.
_TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}
_SUCCESS_STATES = {"SUCCEEDED"}
_FAILURE_STATES = {"FAILED", "CANCELLED"}

#: Condition states in the v2 REST representation and the v1 ``status`` field.
_CONDITION_TERMINAL_STATES = {
    "CONDITION_SUCCEEDED",
    "CONDITION_FAILED",
    "CONDITION_CANCELLED",
}
_CONDITION_SUCCESS_STATES = {"CONDITION_SUCCEEDED"}
_CONDITION_FAILURE_STATES = {"CONDITION_FAILED", "CONDITION_CANCELLED"}


def _as_count(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _execution_view(execution: dict) -> dict[str, Any]:
    """Flatten the v1 ``status`` wrapper and the v2 top-level resource.

    The Run Admin **v2 REST** Execution resource carries ``completionTime``,
    ``startTime``, ``succeededCount``, ``failedCount``, ``cancelledCount``,
    ``runningCount``, ``conditions`` and ``reconciling`` at the top level.
    ``gcloud`` (a v1 view) prints the same fields under ``status``. The
    backend must recognise both: #172 polled a finished v2 execution for 115
    GETs because it only read ``status.completionTime``.
    """
    if not isinstance(execution, dict):
        return {}
    view: dict[str, Any] = {}
    status = execution.get("status")
    if isinstance(status, dict):
        view.update(status)
    for key in (
        "completionTime",
        "startTime",
        "updateTime",
        "succeededCount",
        "failedCount",
        "cancelledCount",
        "runningCount",
        "retriedCount",
        "conditions",
        "reconciling",
        "state",
    ):
        if key in execution and execution[key] is not None:
            view[key] = execution[key]
    return view


def _condition_succeeded(condition: dict) -> bool | None:
    """Return success for a v2 ``state`` or v1 ``status`` condition, or None."""
    state = condition.get("state")
    if isinstance(state, str) and state:
        if state in _CONDITION_SUCCESS_STATES:
            return True
        if state in _CONDITION_FAILURE_STATES:
            return False
        return None
    status = condition.get("status")
    if isinstance(status, bool):
        return status
    if isinstance(status, str) and status:
        lowered = status.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    return None


def _condition_is_terminal(condition: dict) -> bool:
    if condition.get("type") not in {"Completed", "Succeeded"}:
        return False
    state = condition.get("state")
    if isinstance(state, str) and state in _CONDITION_TERMINAL_STATES:
        return True
    return _condition_succeeded(condition) is not None


def is_execution_terminal(execution: dict) -> bool:
    """Return whether a Cloud Run execution has completed (v1 or v2 shape)."""
    view = _execution_view(execution)
    if view.get("completionTime"):
        return True
    state = view.get("state")
    if isinstance(state, str) and state.upper() in _TERMINAL_STATES:
        return True
    for condition in view.get("conditions") or []:
        if isinstance(condition, dict) and _condition_is_terminal(condition):
            return True
    finished = (
        _as_count(view.get("succeededCount"))
        + _as_count(view.get("failedCount"))
        + _as_count(view.get("cancelledCount"))
    )
    running = _as_count(view.get("runningCount"))
    return finished > 0 and running == 0


def is_operation(obj: dict) -> bool:
    """Return whether ``obj`` is a long-running Operation, not an Execution.

    ``POST .../jobs/<job>:run`` returns a ``GoogleLongRunningOperation``. The
    operation is terminal when ``done`` is true; it carries either ``error`` or
    a ``response`` that names the Execution. Polling its ``status`` like an
    Execution is why #171 waited the full 330 s for an execution that had
    already failed (#171's timeout message named ``.../operations/...``).

    An in-flight operation may omit ``done`` entirely in proto3 JSON, so the
    resource name (``.../operations/...``) is also an operation signal.
    """
    if not isinstance(obj, dict):
        return False
    if "done" in obj:
        return True
    name = obj.get("name")
    if isinstance(name, str) and "/operations/" in name:
        return True
    return False


def operation_execution_name(operation: dict) -> str | None:
    """Extract the Execution resource name from a completed run operation."""
    response = operation.get("response")
    if isinstance(response, dict):
        name = response.get("name")
        if isinstance(name, str) and name:
            return name
    # Some LRO shapes put the created resource in metadata instead.
    metadata = operation.get("metadata")
    if isinstance(metadata, dict):
        name = metadata.get("name")
        if isinstance(name, str) and "/executions/" in name:
            return name
    return None


def _object_store_complete(store: Any, result_uri: str) -> bool:
    """Whether the experiment's own result/error object exists yet."""
    for name in ("result.json", "error.json"):
        try:
            data = store.download_bytes(f"{result_uri}/{name}")
        except Exception:  # noqa: BLE001 - a probe must never fail the poll
            continue
        if data is not None:
            return True
    return False


class ExecutionOperationError(RuntimeError):
    """A Cloud Run ``:run`` operation finished with an error (dispatch failure)."""

    def __init__(self, operation: dict) -> None:
        self.operation = operation
        error = operation.get("error") or {}
        message = ""
        code = None
        if isinstance(error, dict):
            message = str(error.get("message") or "")
            code = error.get("code")
        elif error:
            message = str(error)
        if not message:
            message = (
                "the run operation finished without creating an execution "
                f"(status: {operation.get('status') or 'unknown'})"
            )
        if code:
            message = f"Cloud Run Job start failed ({code}): {message}"
        else:
            message = f"Cloud Run Job start failed: {message}"
        super().__init__(message)


def execution_condition_message(execution: dict) -> str:
    """Return the Completed/Succeeded condition message, never a guess."""
    view = _execution_view(execution)
    for condition in view.get("conditions") or []:
        if not isinstance(condition, dict):
            continue
        if condition.get("type") in {"Completed", "Succeeded"}:
            message = condition.get("message") or ""
            reason = condition.get("reason") or ""
            if message and reason:
                return f"{reason}: {message}"
            if message:
                return str(message)
    return ""


def execution_succeeded(execution: dict) -> bool:
    """Return whether a completed Cloud Run execution succeeded (v1 or v2)."""
    view = _execution_view(execution)
    if not is_execution_terminal(execution):
        return False
    if _as_count(view.get("cancelledCount")) or _as_count(
        view.get("failedCount")
    ):
        return False
    state = view.get("state")
    if isinstance(state, str):
        upper = state.upper()
        if upper in _SUCCESS_STATES:
            return True
        if upper in _FAILURE_STATES:
            return False
    for condition in view.get("conditions") or []:
        if not isinstance(condition, dict):
            continue
        if condition.get("type") in {"Completed", "Succeeded"}:
            succeeded = _condition_succeeded(condition)
            if succeeded is not None:
                return succeeded
    return _as_count(view.get("succeededCount")) > 0


def image_digest_from_execution(execution: dict) -> str | None:
    """Best-effort image digest read from a run response, never guessed."""
    for key in ("image", "containerImage"):
        value = execution.get(key)
        if isinstance(value, str) and "@sha256:" in value:
            return value.split("@", 1)[1]
    return None


def _poll_execution(
    run: Any,
    execution_name: str,
    timeout: float,
    *,
    clock: Any,
    sleep: Any,
    interval: float,
    object_store: Any = None,
    result_uri: str | None = None,
    object_probe_every: int = 3,
) -> dict:
    """Poll a run operation / execution until it is terminal.

    ``:run`` returns an operation name. The operation finishes as soon as the
    platform has created the execution (or failed to), so poll ``done`` first,
    then poll the named Execution for a terminal state. Both the v2 REST shape
    (top-level ``completionTime``/``conditions``) and the v1 ``status`` view are
    accepted (#172 polled a finished v2 execution for 115 GETs).

    When ``object_store`` and ``result_uri`` are supplied, the appearance of
    ``result.json`` or ``error.json`` is also completion: the generated script
    writes the outcome itself, so the object store is the contract and the
    control-plane status is only a hint. The probe runs every few polls.
    """
    deadline = clock() + timeout
    target = execution_name
    polls = 0
    while True:
        current = run.get_execution(target)
        if is_operation(current):
            if not current.get("done"):
                if clock() >= deadline:
                    raise TimeoutError(
                        f"Cloud Run Job operation was not done within {timeout}s: "
                        f"{target}"
                    )
                sleep(interval)
                continue
            if current.get("error"):
                raise ExecutionOperationError(current)
            resolved = operation_execution_name(current)
            if not resolved:
                raise ExecutionOperationError(current)
            if resolved != target:
                target = resolved
                continue
        if is_execution_terminal(current):
            return current
        polls += 1
        if (
            object_store is not None
            and result_uri
            and object_probe_every > 0
            and polls % object_probe_every == 0
            and _object_store_complete(object_store, result_uri)
        ):
            marked = dict(current)
            marked[_OBJECT_COMPLETE_KEY] = True
            return marked
        if clock() >= deadline:
            raise TimeoutError(
                f"Cloud Run Job execution exceeded {timeout}s: {target}"
            )
        sleep(interval)
        if clock() >= deadline:
            raise TimeoutError(
                f"Cloud Run Job execution exceeded {timeout}s: {target}"
            )


def classify_sandbox_error(exc: BaseException) -> str:
    """Map a sandbox failure to a deploy-readable category.

    The three classes the deploy needs to tell apart are connectivity (the
    runtime cannot reach the endpoint), permission (403/IAM) and everything
    else. HTTP status codes are checked before connectivity so a 403 that a
    client retried is still reported as a permission problem.
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    if any(
        token in text
        for token in ("403", "forbidden", "permission", "access denied")
    ):
        return "permission"
    if any(token in text for token in ("404", "not found", "no such bucket")):
        return "not_found"
    if any(
        token in text
        for token in (
            "timed out",
            "timeout",
            "max retries",
            "retryerror",
            "connection",
            "name resolution",
            "network is unreachable",
            "temporary failure",
            "failed to establish",
            "ssl",
        )
    ):
        return "connectivity"
    return "error"


@dataclass
class SandboxCheckReport:
    """Outcome of the deploy-time ``sandbox-check`` end-to-end probe."""

    ok: bool
    category: str
    message: str
    endpoint: str
    used_fallback: bool
    job: str
    experiment_id: str = ""
    result_uri: str = ""
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    cleanup: str = "not attempted"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "category": self.category,
            "message": self.message,
            "endpoint": self.endpoint,
            "used_fallback": self.used_fallback,
            "job": self.job,
            "experiment_id": self.experiment_id,
            "result_uri": self.result_uri,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "cleanup": self.cleanup,
        }


def _cleanup_objects(store: Any, result_uri: str) -> str:
    """Best-effort delete of the check's own objects.

    The *experiment* runtime service account has ``objectViewer`` +
    ``objectCreator`` but not ``objectDeleter`` (ADR-0009 names only the first
    two), so a cleanup 403 is reported, not raised. The *worker* that runs this
    check uses the ingest Job's default compute identity, which can delete, so
    the value semantics differ by identity and a successful delete here is not
    evidence that #170's claim was wrong. The bucket's 30-day lifecycle rule
    removes anything left behind.
    """
    deleter = getattr(store, "delete_bytes", None)
    if deleter is None:
        return "skipped (store cannot delete)"
    names = (
        "script.py",
        "inputs.json",
        "stdout.txt",
        "stderr.txt",
        "metrics.json",
        "result.json",
        "error.json",
    )
    deleted = 0
    for name in names:
        try:
            if deleter(f"{result_uri}/{name}"):
                deleted += 1
        except Exception as exc:  # noqa: BLE001 - cleanup never fails the check
            if classify_sandbox_error(exc) == "permission":
                return (
                    "skipped (no storage.objects.delete permission; "
                    "lifecycle rule will remove the objects)"
                )
            return f"failed: {type(exc).__name__}: {exc}"
    return f"deleted {deleted} object(s)"


def run_sandbox_check(
    config: SandboxConfig,
    *,
    store: Any = None,
    run_client: Any = None,
    clock: Any = time.monotonic,
    sleep: Any = time.sleep,
    timeout_s: int | None = None,
) -> SandboxCheckReport:
    """Prove this process can use the experiment sandbox end to end.

    Uploads a tiny script to the experiment bucket, runs the experiment Job on
    it, polls the execution, reads the result back and cleans up. Every phase
    returns a categorised failure instead of a generic exception so the deploy
    can say connectivity vs 403 vs job failure. It uses the *caller's*
    resolved endpoint, which is the whole point: the process that runs the
    sandbox must be able to reach the endpoint it will actually use.
    """
    if not config.gcs_bucket:
        raise ValueError(
            "EXPERIMENT_GCS_BUCKET is required for the sandbox check"
        )

    store = store or GcsObjectStore(config)
    run = run_client or CloudRunJobClient(config)
    endpoint = getattr(store, "endpoint", None) or _normalise_endpoint(
        config.gcs_endpoint
    )
    used_fallback = bool(getattr(store, "used_fallback", False))
    job = _job_resource_name(config)
    experiment_id = f"sandbox-check-{uuid.uuid4().hex}"
    result_uri = _prefix_for(config, experiment_id)
    script_uri = f"{result_uri}/script.py"
    timeout = timeout_s or config.timeout_seconds

    report = SandboxCheckReport(
        ok=False,
        category="error",
        message="",
        endpoint=endpoint,
        used_fallback=used_fallback,
        job=job,
        experiment_id=experiment_id,
        result_uri=result_uri,
    )

    try:
        store.upload_bytes(
            script_uri,
            _SANDBOX_CHECK_SCRIPT.encode("utf-8"),
            content_type="text/x-python",
        )
        store.upload_bytes(
            f"{result_uri}/inputs.json", b"{}", content_type="application/json"
        )
    except Exception as exc:  # noqa: BLE001 - categorised, not swallowed
        report.category = classify_sandbox_error(exc)
        report.message = f"upload failed: {type(exc).__name__}: {exc}"
        return report

    # Read the object name back from the caller's own store. A mismatch between
    # the name this process writes and the name the runner derives from the URI
    # is invisible to the upload call; this catches it before a Job is started.
    try:
        uploaded_script = store.download_bytes(script_uri)
    except Exception as exc:  # noqa: BLE001 - the deploy should see the reason
        report.category = classify_sandbox_error(exc)
        report.message = (
            "uploaded script could not be read back with the same URI: "
            f"{type(exc).__name__}: {exc}"
        )
        report.cleanup = _cleanup_objects(store, result_uri)
        return report
    if uploaded_script != _SANDBOX_CHECK_SCRIPT.encode("utf-8"):
        report.category = "upload_unverified"
        report.message = (
            f"uploaded script read back as {uploaded_script!r} from {script_uri}"
        )
        report.cleanup = _cleanup_objects(store, result_uri)
        return report

    try:
        execution_name = run.run_job(
            job,
            args=[script_uri],
            env={
                "EXPERIMENT_ID": experiment_id,
                "EXPERIMENT_RESULT_URI": result_uri,
                "EXPERIMENT_TIMEOUT_S": str(max(1, timeout - 5)),
            },
        )
    except Exception as exc:  # noqa: BLE001
        report.category = classify_sandbox_error(exc)
        report.message = f"starting the experiment Job failed: {type(exc).__name__}: {exc}"
        report.cleanup = _cleanup_objects(store, result_uri)
        return report

    try:
        execution = _poll_execution(
            run,
            execution_name,
            timeout + _JOB_POLL_MARGIN_SECONDS,
            clock=clock,
            sleep=sleep,
            interval=config.poll_interval_seconds,
            object_store=store,
            result_uri=result_uri,
        )
    except TimeoutError as exc:
        report.category = "job_timeout"
        report.message = str(exc)
        report.cleanup = _cleanup_objects(store, result_uri)
        return report
    except Exception as exc:  # noqa: BLE001
        report.category = classify_sandbox_error(exc)
        report.message = f"polling the experiment Job failed: {type(exc).__name__}: {exc}"
        report.cleanup = _cleanup_objects(store, result_uri)
        return report

    stdout = _store_text(store, f"{result_uri}/stdout.txt")
    stderr = _store_text(store, f"{result_uri}/stderr.txt")
    raw_result = store.download_bytes(f"{result_uri}/result.json")
    job_result: dict = {}
    if raw_result:
        try:
            job_result = json.loads(raw_result.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            job_result = {}
    runner_error = _store_json_object(store, f"{result_uri}/error.json")
    exit_code = int(
        job_result.get(
            "exit_code", 0 if execution_succeeded(execution) else 1
        )
    )
    report.stdout = stdout[: config.max_output_chars]
    report.stderr = stderr[: config.max_output_chars]
    report.exit_code = exit_code
    report.cleanup = _cleanup_objects(store, result_uri)

    completed = execution_succeeded(execution) or bool(
        execution.get(_OBJECT_COMPLETE_KEY)
    )
    if completed and exit_code == 0 and not runner_error:
        report.ok = True
        report.category = "ok"
        report.message = "upload, experiment Job execution, poll and read-back succeeded"
        return report

    if runner_error:
        # The runner named the stage and HTTP status; report that instead of a
        # generic "job failed". This is the evidence that distinguishes a
        # routing/Host 404 from an IAM 403 from a missing object.
        stage = str(runner_error.get("stage") or "runner")
        report.category = str(runner_error.get("category") or "job_failed")
        detail = CloudRunJobBackend._runner_error_detail(runner_error)
        report.message = (
            f"experiment runner failed at stage={stage} "
            f"[{report.category}]: {detail[:500]}"
        )
        return report

    report.category = "job_failed"
    detail = (stderr or stdout or "").strip()
    if not detail:
        # The reason is usually only in the experiment Job's own logs, which
        # the deploy step prints separately. At least name the execution state.
        condition = execution_condition_message(execution)
        detail = f"no output; execution status: {_execution_view(execution)}"
        if condition:
            detail = f"{detail}; {condition}"
    report.message = (
        f"experiment Job finished but the result was not successful "
        f"(exit_code={exit_code}): {detail[:500]}"
    )
    return report


def _store_text(store: Any, uri: str) -> str:
    data = store.download_bytes(uri)
    return data.decode("utf-8", errors="replace") if data else ""


def _store_json_object(store: Any, uri: str) -> dict:
    data = store.download_bytes(uri)
    if not data:
        return {}
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _env_name() -> str:
    return os.getenv("GCP_ENV", "staging")


def create_execution_backend(
    config: Optional[SandboxConfig] = None,
) -> ExecutionBackend:
    """Select the configured execution backend (``docker`` by default)."""
    config = config or get_agent_config().sandbox
    backend = (config.backend or "docker").strip().lower()
    if backend == "docker":
        return DockerSandbox(config=config)
    if backend in {"cloudrun_job", "cloudrun-job", "cloudrun"}:
        return CloudRunJobBackend(config=config)
    raise ValueError(
        f"Unknown EXPERIMENT_BACKEND={config.backend!r}; "
        "expected 'docker' or 'cloudrun_job'"
    )
