"""Cloud Run Job entrypoint for one generated evaluation experiment.

The API uploads ``script.py`` and ``inputs.json`` to
``gs://<bucket>/<prefix>/<experiment-id>/`` and invokes the Job with the script
URI as its only argument. This module downloads the script into ``/tmp``, runs
it with the Job's Python, and writes the captured output back to the same
prefix. It deliberately has no network, database, secret or application imports
beyond the GCS client.

The runner talks to the GCS *JSON API* directly instead of the storage client's
``blob.exists()`` helper. The helper maps every HTTP ``404`` to a boolean and
hides the response body, so a routing or permission failure looked exactly like
"the object is not there" (#171). A direct call lets the runner report the
status, a body excerpt and the identity it used, and distinguishes 401 / 403 /
404 / other. It also lets the runner send the canonical
``Host: storage.googleapis.com`` header when it reaches GCS through the Private
Google APIs VIP: the VIP is reached by IP but routes on the canonical API host,
so using ``private.googleapis.com`` as the Host returns a 404 for an object
that exists.
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
from urllib.parse import quote, urlparse

_GS_SCHEME = "gs://"
_MAX_CAPTURE_CHARS = 1_000_000
_MAX_BODY_CHARS = 400

#: The public GCS endpoint. The experiment Job normally uses the Private Google
#: APIs VIP (``private.googleapis.com``) through its dedicated VPC; a runtime
#: that cannot reach it falls back here and, if that is also blocked, fails fast
#: with the real connectivity error instead of hanging to the platform timeout.
_DEFAULT_GCS_ENDPOINT = "https://storage.googleapis.com"

#: Private Google APIs VIPs. Requests to these hosts must carry the canonical
#: GCS ``Host`` header; the VIP is reached by address but routed by API host.
_PRIVATE_VIPS = {"private.googleapis.com", "restricted.googleapis.com"}
_CANONICAL_STORAGE_HOST = "storage.googleapis.com"
_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

#: The last diagnostic line written. It is included in ``error.json`` so the
#: backend can show the runner's own reason without reading Cloud Logging.
_LAST_LOG = ""


def _log(message: str) -> None:
    """Write one diagnostic line to stderr immediately (Cloud Logging captures it)."""
    global _LAST_LOG
    _LAST_LOG = message
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


def classify_http_status(status: int | None) -> str:
    """Map an HTTP status from a GCS read/write to a deploy-readable category."""
    if status == 401:
        return "unauthenticated"
    if status == 403:
        return "permission"
    if status == 404:
        return "not_found"
    if status in (408, 429) or (status is not None and 500 <= status <= 599):
        return "connectivity"
    return "error"


def _body_excerpt(response: Any) -> str:
    """Return a short, printable excerpt of an HTTP response body (never secrets)."""
    text = getattr(response, "text", "") or ""
    text = " ".join(str(text).split())
    return text[:_MAX_BODY_CHARS]


def _identity(credentials: Any = None) -> str:
    """Best-effort identity of this process, for a permission message.

    Never reads or prints a secret: only the service-account email already
    attached to the metadata credentials.
    """
    email = getattr(credentials, "service_account_email", None)
    if email and email != "default":
        return str(email)
    env_email = os.environ.get("GOOGLE_SERVICE_ACCOUNT_EMAIL")
    if env_email:
        return env_email
    return "unknown identity"


class _StageError(RuntimeError):
    """A runner stage failed with a classified reason.

    ``stage`` is ``download`` / ``exec`` / ``upload``; ``category`` is
    ``not_found`` / ``permission`` / ``unauthenticated`` / ``connectivity`` /
    ``error``. The backend reads the same fields from ``error.json``.
    """

    def __init__(
        self,
        stage: str,
        category: str,
        message: str,
        *,
        status: int | None = None,
        body_excerpt: str = "",
    ) -> None:
        self.stage = stage
        self.category = category
        self.status = status
        self.body_excerpt = body_excerpt
        super().__init__(message)


class _Store:
    """Minimal authenticated GCS JSON-API client with visible errors."""

    def __init__(self) -> None:
        self._session = None
        self._credentials = None
        try:
            self._timeout = float(
                os.environ.get("EXPERIMENT_GCS_REQUEST_TIMEOUT_SECONDS", "30")
            )
        except ValueError:
            self._timeout = 30.0
        self.endpoint, self.used_fallback = _resolve_endpoint()
        parsed = urlparse(self.endpoint)
        self.host_override = (
            _CANONICAL_STORAGE_HOST
            if (parsed.hostname or "").lower() in _PRIVATE_VIPS
            else None
        )
        suffix = " (fallback)" if self.used_fallback else ""
        host_note = (
            f", Host {self.host_override}" if self.host_override else ""
        )
        _log(f"using GCS endpoint {self.endpoint}{suffix}{host_note}")

    @property
    def identity(self) -> str:
        self._session_or_create()
        return _identity(self._credentials)

    def _session_or_create(self):
        if self._session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credentials, _ = google.auth.default(scopes=[_CLOUD_PLATFORM_SCOPE])
            self._credentials = credentials
            self._session = AuthorizedSession(credentials)
        return self._session

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = dict(extra or {})
        if self.host_override:
            headers.setdefault("Host", self.host_override)
        return headers

    def _object_url(self, bucket_name: str, key: str, *, media: bool) -> str:
        quoted_bucket = quote(bucket_name, safe="")
        quoted_key = quote(key, safe="")
        suffix = "?alt=media" if media else ""
        return (
            f"{self.endpoint}/storage/v1/b/{quoted_bucket}/o/{quoted_key}{suffix}"
        )

    def _upload_url(self, bucket_name: str, key: str) -> str:
        quoted_bucket = quote(bucket_name, safe="")
        quoted_key = quote(key, safe="")
        return (
            f"{self.endpoint}/upload/storage/v1/b/{quoted_bucket}/o"
            f"?uploadType=media&name={quoted_key}"
        )

    def download(self, uri: str) -> bytes | None:
        bucket_name, key = _split_uri(uri)
        url = self._object_url(bucket_name, key, media=True)
        try:
            response = self._session_or_create().get(
                url, headers=self._headers(), timeout=self._timeout
            )
        except Exception as exc:  # noqa: BLE001 - name the real network error
            raise _StageError(
                "download",
                "connectivity",
                f"could not reach GCS for {uri} at {self.endpoint}: "
                f"{type(exc).__name__}: {exc}",
            ) from exc
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            self._raise_http_error("download", uri, response)
        return response.content

    def upload(self, uri: str, data: bytes, content_type: str) -> None:
        bucket_name, key = _split_uri(uri)
        url = self._upload_url(bucket_name, key)
        try:
            response = self._session_or_create().post(
                url,
                headers=self._headers({"Content-Type": content_type}),
                data=data,
                timeout=self._timeout,
            )
        except Exception as exc:  # noqa: BLE001 - name the real network error
            raise _StageError(
                "upload",
                "connectivity",
                f"could not reach GCS for {uri} at {self.endpoint}: "
                f"{type(exc).__name__}: {exc}",
            ) from exc
        if response.status_code >= 400:
            self._raise_http_error("upload", uri, response, writing=True)

    def _raise_http_error(
        self,
        stage: str,
        uri: str,
        response: Any,
        *,
        writing: bool = False,
    ) -> None:
        status = int(getattr(response, "status_code", 0) or 0)
        category = classify_http_status(status)
        body = _body_excerpt(response)
        verb = "writing" if writing else "reading"
        if category == "permission" or category == "unauthenticated":
            message = (
                f"permission denied ({status}) {verb} {uri} as {self.identity}"
            )
        elif category == "not_found":
            message = f"object not found (404) {verb} {uri}"
        else:
            message = f"unexpected HTTP {status} {verb} {uri}"
        if body:
            message = f"{message}: {body}"
        raise _StageError(
            stage,
            category,
            message,
            status=status,
            body_excerpt=body,
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


def _write_error(store: _Store, prefix: str, exc: BaseException) -> None:
    """Best-effort ``error.json`` next to the script, so the backend can read it."""
    payload = {
        "stage": getattr(exc, "stage", "unknown"),
        "category": getattr(exc, "category", "error"),
        "message": str(exc),
        "status": getattr(exc, "status", None),
        "body_excerpt": getattr(exc, "body_excerpt", ""),
        "last_log": _LAST_LOG,
    }
    try:
        store.upload(
            f"{prefix}/error.json",
            json.dumps(payload).encode("utf-8"),
            "application/json",
        )
    except Exception as upload_exc:  # noqa: BLE001 - the original error is the result
        _log(
            "could not write error.json: "
            f"{type(upload_exc).__name__}: {upload_exc}"
        )


def _run(store: Any, script_uri: str, prefix: str) -> int:
    _log(f"stage=download script={script_uri}")
    try:
        code = store.download(script_uri)
    except _StageError:
        raise
    except Exception as exc:  # noqa: BLE001 - name the failing stage
        raise _StageError(
            "download",
            "error",
            f"download stage failed for {script_uri}: "
            f"{type(exc).__name__}: {exc}",
        ) from exc
    if code is None:
        raise _StageError(
            "download",
            "not_found",
            f"Experiment script not found: {script_uri}",
        )

    _log(f"stage=download inputs={prefix}/inputs.json")
    try:
        raw_inputs = store.download(f"{prefix}/inputs.json")
    except _StageError:
        raise
    except Exception as exc:  # noqa: BLE001 - name the failing stage
        raise _StageError(
            "download",
            "error",
            f"download stage failed for {prefix}/inputs.json: "
            f"{type(exc).__name__}: {exc}",
        ) from exc
    try:
        inputs = json.loads(raw_inputs.decode("utf-8")) if raw_inputs else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _StageError(
            "download",
            "error",
            f"inputs.json is not valid JSON: {type(exc).__name__}: {exc}",
        ) from exc

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
    except OSError as exc:
        raise _StageError(
            "exec",
            "error",
            f"exec stage failed starting {sys.executable}: "
            f"{type(exc).__name__}: {exc}",
        ) from exc

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
                    "last_log": _LAST_LOG,
                }
            ).encode("utf-8"),
            "application/json",
        )
    except Exception as exc:  # noqa: BLE001 - name the failing stage
        raise _StageError(
            "upload",
            getattr(exc, "category", "error"),
            f"upload stage failed for {prefix}: {type(exc).__name__}: {exc}",
            status=getattr(exc, "status", None),
            body_excerpt=getattr(exc, "body_excerpt", ""),
        ) from exc
    _log(f"done exit_code={exit_code} timed_out={timed_out}")
    return exit_code


def run(script_uri: str) -> int:
    _log(f"start uri={script_uri} pid={os.getpid()}")
    store = _Store()
    prefix = script_uri.rsplit("/", 1)[0]
    try:
        return _run(store, script_uri, prefix)
    except Exception as exc:
        # Every failure leaves a small error.json beside the script, so the
        # backend can classify the failure without Cloud Logging.
        _write_error(store, prefix, exc)
        raise


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
