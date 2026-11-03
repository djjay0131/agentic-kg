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
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

_GS_SCHEME = "gs://"
_MAX_CAPTURE_CHARS = 1_000_000


def _split_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith(_GS_SCHEME):
        raise ValueError(f"Not a gs:// URI: {uri}")
    bucket, _, key = uri[len(_GS_SCHEME):].partition("/")
    return bucket, key


class _Store:
    def __init__(self) -> None:
        self._client = None

    def _bucket(self):
        if self._client is None:
            from google.cloud import storage

            client_options = None
            endpoint = os.environ.get("EXPERIMENT_GCS_ENDPOINT", "")
            if endpoint:
                client_options = {"api_endpoint": endpoint}
            self._client = storage.Client(client_options=client_options)
        return self._client

    def download(self, uri: str) -> bytes | None:
        bucket_name, key = _split_uri(uri)
        blob = self._bucket().bucket(bucket_name).blob(key)
        if not blob.exists():
            return None
        return blob.download_as_bytes()

    def upload(self, uri: str, data: bytes, content_type: str) -> None:
        bucket_name, key = _split_uri(uri)
        self._bucket().bucket(bucket_name).blob(key).upload_from_string(
            data, content_type=content_type
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
    store = _Store()
    prefix = script_uri.rsplit("/", 1)[0]
    code = store.download(script_uri)
    if code is None:
        raise FileNotFoundError(f"Experiment script not found: {script_uri}")

    raw_inputs = store.download(f"{prefix}/inputs.json")
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
