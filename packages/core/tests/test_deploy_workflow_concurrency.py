"""Pin the serial, newest-wins contract for staging deploys.

Two pushes to master a few seconds apart (runs 46040927025 / 46040957438) used
to deploy concurrently; the older commit's run finished last, so staging ran
the older SHA and the newer run failed its drift check. These offline tests
parse the workflows on disk (no network, no Docker, no gcloud) and fail if the
fix regresses:

* deploy-master serialises every run in one non-cancelling staging group;
* a head guard gates *build and deploy* so a superseded run cannot roll the
  older image (and cannot overwrite the newer ``latest`` tag either);
* the guard treats a docs-only advance of master as non-superseding;
* a manual deploy-branch run targeting staging joins the same group.

The guard's decision logic is exercised against a real temporary git repo, so
the shell below the workflow is tested rather than just grepped.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"
_DEPLOY_MASTER = _WORKFLOWS / "deploy-master.yml"
_DEPLOY_BRANCH = _WORKFLOWS / "deploy-branch.yml"
_GUARD = _REPO_ROOT / "scripts" / "staging_deploy_guard.sh"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    # YAML turns the top-level ``on`` key into True on some PyYAML versions.
    # ruff: noqa: E712 — comparing to True is intentional here
    return workflow.get("on") or workflow.get(True)


def test_deploy_master_serialises_all_runs_without_cancelling() -> None:
    concurrency = _load(_DEPLOY_MASTER)["concurrency"]
    assert concurrency["group"] == "deploy-staging"
    # A queued run waits; it must never cancel an in-flight deploy, or a
    # cancellation could leave staging half-rolled.
    assert concurrency["cancel-in-progress"] is False


def test_head_guard_runs_before_build_and_deploy() -> None:
    workflow = _load(_DEPLOY_MASTER)
    guard = workflow["jobs"]["staging-head-guard"]
    assert guard["needs"] == ["changes"]
    assert guard["outputs"]["superseded"] == "${{ steps.guard.outputs.superseded }}"

    checkout, step = guard["steps"]
    # Full history: the guard diffs this commit against the branch tip.
    assert checkout["with"]["fetch-depth"] == 0
    assert step["id"] == "guard"
    assert step["run"] == "./scripts/staging_deploy_guard.sh"

    for job_name in ("build", "deploy-staging"):
        job = workflow["jobs"][job_name]
        assert "staging-head-guard" in job["needs"], job_name
        assert "needs.staging-head-guard.outputs.superseded != 'true'" in job["if"], job_name


def test_deploy_branch_staging_shares_the_master_group() -> None:
    concurrency = _load(_DEPLOY_BRANCH)["jobs"]["deploy"]["concurrency"]
    # ``inputs.environment`` is ``staging`` for the shared group; dev gets its
    # own group. This must equal deploy-master's literal group for staging.
    assert concurrency["group"] == "deploy-${{ inputs.environment }}"
    assert concurrency["cancel-in-progress"] is False


def test_guard_is_executable() -> None:
    assert os.access(_GUARD, os.X_OK), "staging_deploy_guard.sh must be executable"


def test_guard_paths_cover_every_deploy_trigger_path() -> None:
    """The guard's supersede check must look at every path deploy-master runs on."""
    trigger_paths = _triggers(_load(_DEPLOY_MASTER))["push"]["paths"]
    guard_text = _GUARD.read_text(encoding="utf-8")
    for trigger_path in trigger_paths:
        root = trigger_path.split("/", 1)[0]
        assert root in guard_text, (
            f"staging_deploy_guard.sh does not consider deploy trigger path "
            f"{trigger_path!r}; update its deploy_paths list"
        )


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _init_repo(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    subprocess.run(
        ["git", "-c", "init.defaultBranch=master", "init", "--bare", str(remote)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-c", "init.defaultBranch=master", "init", str(repo)],
        check=True,
        capture_output=True,
    )
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "remote", "add", "origin", str(remote))
    return repo, remote


def _commit(repo: Path, relative_path: str, message: str) -> str:
    target = repo / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"{message}\n", encoding="utf-8")
    _git(repo, "add", relative_path)
    _git(repo, "commit", "-m", message)
    _git(repo, "push", "-q", "origin", "master")
    return _git(repo, "rev-parse", "HEAD")


def _run_guard(repo: Path, sha: str, tmp_path: Path) -> str:
    output = tmp_path / "github-output"
    output.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "GITHUB_OUTPUT": str(output),
        "GITHUB_SHA": sha,
        "GITHUB_REF_NAME": "master",
    }
    subprocess.run(
        [str(_GUARD), sha, "master", "origin"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    match = re.search(r"^superseded=(true|false)$", output.read_text(encoding="utf-8"), re.M)
    assert match is not None, output.read_text(encoding="utf-8")
    return match.group(1)


def test_guard_allows_current_tip(tmp_path: Path) -> None:
    repo, _remote = _init_repo(tmp_path)
    sha = _commit(repo, "packages/core/file.txt", "code")
    assert _run_guard(repo, sha, tmp_path) == "false"


def test_guard_allows_a_docs_only_advance(tmp_path: Path) -> None:
    repo, _remote = _init_repo(tmp_path)
    code_sha = _commit(repo, "packages/core/file.txt", "code")
    _commit(repo, "docs/operations/note.md", "docs")

    # master moved on, but only in a path that does not trigger deploy-master.
    # Skipping here would strand staging on the previous image forever, because
    # the docs commit has no deploy run of its own.
    assert _run_guard(repo, code_sha, tmp_path) == "false"


def test_guard_supersedes_when_a_newer_deployable_commit_lands(tmp_path: Path) -> None:
    repo, _remote = _init_repo(tmp_path)
    old_sha = _commit(repo, "packages/core/file.txt", "first")
    new_sha = _commit(repo, "packages/core/file.txt", "second")

    assert _run_guard(repo, old_sha, tmp_path) == "true"
    assert _run_guard(repo, new_sha, tmp_path) == "false"


def test_guard_supersedes_on_a_workflow_change(tmp_path: Path) -> None:
    repo, _remote = _init_repo(tmp_path)
    old_sha = _commit(repo, "packages/core/file.txt", "first")
    _commit(repo, ".github/workflows/deploy-example.yml", "ci")

    # `.github/workflows/deploy-*.yml` is itself a deploy trigger.
    assert _run_guard(repo, old_sha, tmp_path) == "true"


@pytest.mark.parametrize("path", [_DEPLOY_MASTER, _DEPLOY_BRANCH])
def test_nothing_cancels_an_in_flight_deploy(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert "cancel-in-progress: true" not in text
