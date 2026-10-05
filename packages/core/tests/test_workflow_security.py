"""Structural assertions for the #79/#80 CI-security hardening.

Parses the workflow YAML on disk (no network, no Docker) and fails if the
hardening regresses:

- every workflow declares a least-privilege top-level ``permissions`` block
- the PR-triggered workflows grant only ``contents: read``
- no workflow uses ``secrets: inherit``
- no action is pinned to a mutable ``@master`` / ``@main`` ref
- the reusable-workflow callers pass the two GCP secrets explicitly, and the
  callee declares exactly those two secrets.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_WORKFLOWS_DIR = Path(__file__).resolve().parents[3] / ".github" / "workflows"


def _load(name: str) -> dict:
    return yaml.safe_load((_WORKFLOWS_DIR / name).read_text(encoding="utf-8"))


def _all_workflows() -> list[Path]:
    return sorted(_WORKFLOWS_DIR.glob("*.yml"))


def _triggers(workflow: dict) -> dict:
    """Return the trigger block, handling YAML's ``on`` -> ``True`` key."""
    # ruff: noqa: E712 — comparing to True is intentional here
    return workflow.get("on") or workflow.get(True)


@pytest.mark.parametrize("path", _all_workflows(), ids=lambda p: p.name)
def test_every_workflow_has_top_level_permissions(path: Path) -> None:
    wf = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "permissions" in wf, f"{path.name}: missing top-level permissions block"


@pytest.mark.parametrize(
    "name",
    ["test.yml", "code-review.yml", "integration-tests.yml", "smoke-ingest.yml"],
)
def test_pr_workflows_are_contents_read_only(name: str) -> None:
    wf = _load(name)
    assert wf["permissions"] == {"contents": "read"}, name


@pytest.mark.parametrize("path", _all_workflows(), ids=lambda p: p.name)
def test_no_secrets_inherit(path: Path) -> None:
    assert "secrets: inherit" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", _all_workflows(), ids=lambda p: p.name)
def test_no_mutable_master_or_main_refs(path: Path) -> None:
    bad = re.findall(
        r"uses:\s*\S+@(master|main)\b", path.read_text(encoding="utf-8")
    )
    assert not bad, f"{path.name}: mutable action refs: {bad}"


def test_reusable_callers_pass_gcp_secrets_explicitly() -> None:
    """#80: the deploy callers must not hand every repo secret to build-images."""
    expected = {
        "GCP_WORKLOAD_IDENTITY_PROVIDER": (
            "${{ secrets.GCP_WORKLOAD_IDENTITY_PROVIDER }}"
        ),
        "GCP_SERVICE_ACCOUNT": "${{ secrets.GCP_SERVICE_ACCOUNT }}",
    }
    for name in ("deploy-branch.yml", "deploy-master.yml"):
        build = _load(name)["jobs"]["build"]
        assert build["secrets"] == expected, name


def test_build_images_declares_exactly_the_gcp_secrets() -> None:
    call = _triggers(_load("build-images.yml"))["workflow_call"]
    assert set(call["secrets"]) == {
        "GCP_WORKLOAD_IDENTITY_PROVIDER",
        "GCP_SERVICE_ACCOUNT",
    }
