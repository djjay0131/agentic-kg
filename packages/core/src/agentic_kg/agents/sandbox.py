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
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

from agentic_kg.agents.config import SandboxConfig, get_agent_config

logger = logging.getLogger(__name__)

_GS_SCHEME = "gs://"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


@dataclass
class SandboxResult:
    """Result of a sandboxed code execution."""

    success: bool
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    metrics: Optional[dict] = None

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
        job = self.config.job_name or f"agentic-kg-experiment-{_env_name()}"
        return (
            f"projects/{self.config.project_id}/locations/{self.config.region}"
            f"/jobs/{job}"
        )

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

        self._store.upload_bytes(
            script_uri, code.encode("utf-8"), content_type="text/x-python"
        )
        self._store.upload_bytes(
            inputs_uri,
            json.dumps(inputs or {}, default=str).encode("utf-8"),
            content_type="application/json",
        )

        try:
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
            execution = self._poll(execution_name, timeout)
        except TimeoutError as exc:
            return SandboxResult(
                success=False,
                stdout=self._read_text(f"{prefix}/stdout.txt"),
                stderr=str(exc),
                exit_code=-1,
                timed_out=True,
                experiment_id=experiment_id,
                result_uri=result_uri,
                code_hash=_code_hash(code),
                image_digest=self.config.image_digest,
                started_at=started_at,
                finished_at=_utc_now(),
                backend="cloudrun_job",
            )
        except Exception as exc:
            logger.error("Cloud Run Job execution failed: %s", exc)
            return SandboxResult(
                success=False,
                stdout="",
                stderr=str(exc),
                exit_code=-1,
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
        prefix = self.config.gcs_prefix.strip("/")
        parts = [p for p in (prefix, experiment_id) if p]
        return f"{_GS_SCHEME}{self.config.gcs_bucket}/{'/'.join(parts)}"

    def _poll(self, execution_name: str, timeout: float) -> dict:
        deadline = self._clock() + timeout
        while True:
            execution = self._run.get_execution(execution_name)
            if is_execution_terminal(execution):
                return execution
            if self._clock() >= deadline:
                raise TimeoutError(
                    f"Cloud Run Job execution exceeded {timeout}s: {execution_name}"
                )
            self._sleep(self.config.poll_interval_seconds)
            if self._clock() >= deadline:
                raise TimeoutError(
                    f"Cloud Run Job execution exceeded {timeout}s: {execution_name}"
                )

    def _read_text(self, uri: str) -> str:
        data = self._store.download_bytes(uri)
        return data.decode("utf-8", errors="replace") if data is not None else ""

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

        exit_code = int(job_result.get("exit_code", 0 if execution_succeeded(execution) else 1))
        timed_out = bool(job_result.get("timed_out", False))
        success = execution_succeeded(execution) and exit_code == 0

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

    def __init__(self, config: SandboxConfig) -> None:
        self._config = config
        self._client = None

    def _client_or_create(self):
        if self._client is None:
            from google.cloud import storage

            options = {}
            if self._config.project_id:
                options["project"] = self._config.project_id
            if self._config.gcs_endpoint:
                options["client_options"] = {
                    "api_endpoint": self._config.gcs_endpoint
                }
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
        self._blob(uri).upload_from_string(data, content_type=content_type)

    def download_bytes(self, uri: str) -> bytes | None:
        blob = self._blob(uri)
        if not blob.exists():
            return None
        return blob.download_as_bytes()


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


def is_execution_terminal(execution: dict) -> bool:
    """Return whether a Cloud Run v2 execution has completed."""
    status = execution.get("status") or {}
    return bool(status.get("completionTime"))


def execution_succeeded(execution: dict) -> bool:
    """Return whether a completed Cloud Run v2 execution succeeded."""
    status = execution.get("status") or {}
    if not status.get("completionTime"):
        return False
    if status.get("cancelledCount") or status.get("failedCount"):
        return False
    for condition in status.get("conditions") or []:
        if condition.get("type") in {"Completed", "Succeeded"}:
            return condition.get("state") == "CONDITION_SUCCEEDED"
    return bool(status.get("succeededCount"))


def image_digest_from_execution(execution: dict) -> str | None:
    """Best-effort image digest read from a run response, never guessed."""
    for key in ("image", "containerImage"):
        value = execution.get(key)
        if isinstance(value, str) and "@sha256:" in value:
            return value.split("@", 1)[1]
    return None


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
