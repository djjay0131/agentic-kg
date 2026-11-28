"""Cloud Run Job entrypoint for one generated evaluation experiment.

The API uploads ``script.py`` and ``inputs.json`` to
``gs://<bucket>/<prefix>/<experiment-id>/`` and invokes the Job with the script
URI as its only argument. This module downloads the script into ``/tmp``, runs
it with the Job's Python, and writes the captured output back to the same
prefix. It deliberately has no network, database, secret or application imports
beyond the GCS client.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_GS_SCHEME = "gs://"
_MAX_CAPTURE_CHARS = 1_000_000

#: The public GCS endpoint. The experiment Job normally uses the Private Google
#: APIs VIP (``private.googleapis.com``) through its dedicated VPC; a runtime
#: that cannot reach it falls back here and, if that is also blocked, fails fast
#: with the real connectivity error instead of hanging to the platform timeout.
_DEFAULT_GCS_ENDPOINT = "https://storage.googleapis.com"


def _log(message: str) -> None:
    """Write one diagnostic line to stderr immediately (Cloud Logging captures it)."""
    sys.stderr.write(f"experiment-runner: {message}\n")
    sys.stderr.flush()


def _probe_endpoint(endpoint: str, timeout: float) -> bool:
    """Return whether a TCP connect to ``endpoint`` succeeds quickly."""
    parsed = urlparse(endpoint)
    host = parsed.hostname
    if not host:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError as exc:
        _log(
            f"endpoint {endpoint} is not reachable within {timeout:.1f}s: {exc}"
        )
        return False


def _resolve_endpoint() -> tuple[str, bool]:
    """Pick a GCS endpoint this Job can actually reach; never guess.

    Returns ``(endpoint, used_fallback)``. This mirrors the worker's probe so a
    missing Private Google Access path fails in seconds with a named
    connectivity error rather than blocking the execution for its whole
    platform timeout.
    """
    configured = (
        os.environ.get("EXPERIMENT_GCS_ENDPOINT") or _DEFAULT_GCS_ENDPOINT
    ).rstrip("/")
    fallback = (
        os.environ.get("EXPERIMENT_GCS_ENDPOINT_FALLBACK") or _DEFAULT_GCS_ENDPOINT
    ).rstrip("/")
    try:
        timeout = float(os.environ.get("EXPERIMENT_GCS_CONNECT_TIMEOUT_SECONDS", "5"))
    except ValueError:
        timeout = 5.0
    if configured == fallback or configured == _DEFAULT_GCS_ENDPOINT:
        return configured, False
    if _probe_endpoint(configured, timeout):
        return configured, False
    _log(f"falling back from {configured} to {fallback}")
    if fallback == configured:
        return fallback, True
    if _probe_endpoint(fallback, timeout):
        return fallback, True
    raise RuntimeError(
        "no reachable GCS endpoint: "
        f"{configured} and {fallback} both failed a TCP connect "
        f"within {timeout:.1f}s (check the Job's VPC, Private Google Access and "
        "firewall allow-list)"
    )


def _split_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith(_GS_SCHEME):
        raise ValueError(f"Not a gs:// URI: {uri}")
    bucket, _, key = uri[len(_GS_SCHEME):].partition("/")
    return bucket, key


class _Store:
    def __init__(self) -> None:
        self._client = None
        self._timeout = float(
            os.environ.get("EXPERIMENT_GCS_REQUEST_TIMEOUT_SECONDS", "30")
        )
        self.endpoint, self.used_fallback = _resolve_endpoint()
        _log(
            "using GCS endpoint "
            f"{self.endpoint}{' (fallback)' if self.used_fallback else ''}"
        )

    def _bucket(self):
        if self._client is None:
            from google.cloud import storage

            client_options = {"api_endpoint": self.endpoint}
            self._client = storage.Client(client_options=client_options)
        return self._client

    def download(self, uri: str) -> bytes | None:
        bucket_name, key = _split_uri(uri)
        blob = self._bucket().bucket(bucket_name).blob(key)
        if not blob.exists(timeout=self._timeout, retry=None):
            return None
        return blob.download_as_bytes(timeout=self._timeout, retry=None)

    def upload(self, uri: str, data: bytes, content_type: str) -> None:
        bucket_name, key = _split_uri(uri)
        self._bucket().bucket(bucket_name).blob(key).upload_from_string(
            data,
            content_type=content_type,
            timeout=self._timeout,
            retry=None,
        )


def parse_metrics(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.strip().splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _truncate(text: str) -> str:
    if len(text) <= _MAX_CAPTURE_CHARS:
        return text
    return text[:_MAX_CAPTURE_CHARS] + "\n... (truncated)"


def run(script_uri: str) -> int:
    _log(f"start uri={script_uri} pid={os.getpid()}")
    store = _Store()
    prefix = script_uri.rsplit("/", 1)[0]

    _log(f"stage=download script={script_uri}")
    try:
        code = store.download(script_uri)
    except Exception as exc:  # noqa: BLE001 - name the failing stage
        raise RuntimeError(
            f"download stage failed for {script_uri}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if code is None:
        raise FileNotFoundError(f"Experiment script not found: {script_uri}")

    _log(f"stage=download inputs={prefix}/inputs.json")
    try:
        raw_inputs = store.download(f"{prefix}/inputs.json")
    except Exception as exc:  # noqa: BLE001 - name the failing stage
        raise RuntimeError(
            f"download stage failed for {prefix}/inputs.json: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    inputs = json.loads(raw_inputs.decode("utf-8")) if raw_inputs else {}

    workdir = Path(tempfile.mkdtemp(prefix="experiment-", dir="/tmp"))
    script_path = workdir / "script.py"
    script_path.write_bytes(code)

    input_dir = workdir / "inputs"
    input_dir.mkdir()
    for name, value in (inputs or {}).items():
        target = input_dir / Path(name).name
        if isinstance(value, (dict, list)):
            target.write_text(json.dumps(value), encoding="utf-8")
        elif isinstance(value, bytes):
            target.write_bytes(value)
        else:
            target.write_text(str(value), encoding="utf-8")

    timeout_s = int(os.environ.get("EXPERIMENT_TIMEOUT_S", "300"))
    env = {
        **os.environ,
        "EXPERIMENT_INPUT_DIR": str(input_dir),
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    _log(f"stage=exec script={script_path} timeout_s={timeout_s}")
    timed_out = False
    try:
        completed = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        exit_code = completed.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = _as_text(exc.stdout)
        stderr = _as_text(exc.stderr) + f"\nExecution exceeded {timeout_s}s"
        exit_code = -1

    stdout = _truncate(stdout)
    stderr = _truncate(stderr)

    _log(f"stage=upload prefix={prefix} exit_code={exit_code} timed_out={timed_out}")
    try:
        store.upload(
            f"{prefix}/stdout.txt", stdout.encode("utf-8"), "text/plain; charset=utf-8"
        )
        store.upload(
            f"{prefix}/stderr.txt", stderr.encode("utf-8"), "text/plain; charset=utf-8"
        )
        store.upload(
            f"{prefix}/metrics.json",
            json.dumps(parse_metrics(stdout)).encode("utf-8"),
            "application/json",
        )
        store.upload(
            f"{prefix}/result.json",
            json.dumps(
                {
                    "exit_code": exit_code,
                    "timed_out": timed_out,
                    "experiment_id": os.environ.get("EXPERIMENT_ID", ""),
                }
            ).encode("utf-8"),
            "application/json",
        )
    except Exception as exc:  # noqa: BLE001 - name the failing stage
        raise RuntimeError(
            f"upload stage failed for {prefix}: {type(exc).__name__}: {exc}"
        ) from exc
    _log(f"done exit_code={exit_code} timed_out={timed_out}")
    return exit_code


def _as_text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        sys.stderr.write("usage: experiment_runner.py gs://bucket/prefix/script.py\n")
        return 2
    try:
        return run(args[0])
    except Exception as exc:  # noqa: BLE001 - the Job must exit non-zero, not traceback-only
        sys.stderr.write(f"experiment runner failed: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
