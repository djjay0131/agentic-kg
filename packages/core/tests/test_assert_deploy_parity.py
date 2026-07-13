"""Tests for ``scripts/assert_deploy_parity.sh``.

Covers AC-14: the post-deploy verification exits 0 when every checked
target's commit label matches EXPECTED_SHA, and exits 1 naming the first
drifted target otherwise. ``gcloud``/``curl``/``jq`` are shadowed by PATH
stubs so no cloud calls happen.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parents[3] / "scripts" / "assert_deploy_parity.sh"
)

# A stub `gcloud` that answers `run services/jobs describe` from env vars.
# STUB_<TARGET>_COMMIT drives the commit label; STUB_API_URL the service URL.
_GCLOUD_STUB = r"""#!/usr/bin/env bash
# args: run services|jobs describe agentic-kg-<name> --region=.. --format='..'
name=""
fmt=""
for a in "$@"; do
  case "$a" in
    agentic-kg-*) name="${a#agentic-kg-}" ;;
    --format=*)   fmt="${a#--format=}" ;;
  esac
done
if [[ "$fmt" == *"status.url"* ]]; then
  echo "${STUB_API_URL:-https://example.invalid}"
  exit 0
fi
case "$name" in
  api-staging)    echo "${STUB_API_COMMIT:-}" ;;
  ui-staging)     echo "${STUB_UI_COMMIT:-}" ;;
  ingest-staging) echo "${STUB_JOB_COMMIT:-}" ;;
  *)              echo "" ;;
esac
"""

# A stub `curl` that prints the canned /version JSON.
_CURL_STUB = r"""#!/usr/bin/env bash
echo "${STUB_VERSION_JSON:-{\"commit_sha\":\"\"}}"
"""

# A minimal `jq` stub: only supports `-r '.commit_sha'` over stdin JSON.
_JQ_STUB = r"""#!/usr/bin/env bash
input="$(cat)"
# crude extraction of "commit_sha":"<value>"
echo "$input" | sed -n 's/.*"commit_sha"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'
"""


def _make_stub_bin(tmp_path: Path) -> Path:
    """Create a bin dir with gcloud/curl/jq stubs and return it."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (
        ("gcloud", _GCLOUD_STUB),
        ("curl", _CURL_STUB),
        ("jq", _JQ_STUB),
    ):
        p = bin_dir / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


def _run(tmp_path: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    bin_dir = _make_stub_bin(tmp_path)
    full_env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "REGION": "us-central1",
        **env,
    }
    return subprocess.run(
        ["bash", str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=full_env,
    )


# =============================================================================
# Happy paths
# =============================================================================


def test_all_targets_match_exits_zero(tmp_path: Path) -> None:
    r = _run(
        tmp_path,
        EXPECTED_SHA="abc123",
        CHECK_API="true",
        CHECK_UI="true",
        CHECK_JOB="true",
        STUB_API_COMMIT="abc123",
        STUB_UI_COMMIT="abc123",
        STUB_JOB_COMMIT="abc123",
    )
    assert r.returncode == 0, r.stderr
    assert "SHA parity" in r.stdout
    assert "3 target(s)" in r.stdout


def test_only_checked_targets_are_verified(tmp_path: Path) -> None:
    # UI/Job carry a stale label but are NOT checked -> still passes.
    r = _run(
        tmp_path,
        EXPECTED_SHA="abc123",
        CHECK_API="true",
        CHECK_UI="false",
        CHECK_JOB="false",
        STUB_API_COMMIT="abc123",
        STUB_UI_COMMIT="stale",
        STUB_JOB_COMMIT="stale",
    )
    assert r.returncode == 0, r.stderr
    assert "1 target(s)" in r.stdout


def test_no_targets_selected_exits_zero(tmp_path: Path) -> None:
    r = _run(tmp_path, EXPECTED_SHA="abc123")
    assert r.returncode == 0, r.stderr
    assert "No targets selected" in r.stdout


# =============================================================================
# Drift detection
# =============================================================================


def test_api_drift_exits_one_and_names_target(tmp_path: Path) -> None:
    r = _run(
        tmp_path,
        EXPECTED_SHA="newsha",
        CHECK_API="true",
        STUB_API_COMMIT="oldsha",
    )
    assert r.returncode == 1
    assert "Drift on api-staging: 'oldsha' != 'newsha'" in r.stderr


def test_job_drift_exits_one_and_names_target(tmp_path: Path) -> None:
    r = _run(
        tmp_path,
        EXPECTED_SHA="newsha",
        CHECK_API="true",
        CHECK_JOB="true",
        STUB_API_COMMIT="newsha",
        STUB_JOB_COMMIT="oldsha",
    )
    assert r.returncode == 1
    assert "Drift on ingest-staging: 'oldsha' != 'newsha'" in r.stderr


def test_ui_drift_exits_one_and_names_target(tmp_path: Path) -> None:
    r = _run(
        tmp_path,
        EXPECTED_SHA="newsha",
        CHECK_UI="true",
        STUB_UI_COMMIT="",
    )
    assert r.returncode == 1
    assert "Drift on ui-staging: '' != 'newsha'" in r.stderr


# =============================================================================
# --check-version (PR-3 path, covered now)
# =============================================================================


def test_check_version_match_exits_zero(tmp_path: Path) -> None:
    r = _run(
        tmp_path,
        "--check-version",
        EXPECTED_SHA="abc123",
        CHECK_API="true",
        STUB_API_COMMIT="abc123",
        STUB_API_URL="https://api.example.test",
        STUB_VERSION_JSON='{"commit_sha":"abc123"}',
    )
    assert r.returncode == 0, r.stderr
    assert "SHA parity" in r.stdout


def test_check_version_mismatch_exits_one(tmp_path: Path) -> None:
    r = _run(
        tmp_path,
        "--check-version",
        EXPECTED_SHA="abc123",
        CHECK_API="true",
        STUB_API_COMMIT="abc123",  # label matches...
        STUB_API_URL="https://api.example.test",
        STUB_VERSION_JSON='{"commit_sha":"stale"}',  # ...but /version is stale
    )
    assert r.returncode == 1
    assert "Drift on api-staging/version: 'stale' != 'abc123'" in r.stderr


# =============================================================================
# Required-env guards
# =============================================================================


def test_missing_expected_sha_fails(tmp_path: Path) -> None:
    bin_dir = _make_stub_bin(tmp_path)
    r = subprocess.run(
        ["bash", str(_SCRIPT)],
        capture_output=True,
        text=True,
        env={"PATH": f"{bin_dir}:{os.environ['PATH']}", "REGION": "us-central1"},
    )
    assert r.returncode != 0
    assert "EXPECTED_SHA" in r.stderr
