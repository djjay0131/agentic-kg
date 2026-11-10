"""Guard the staging IAP Terraform wiring (ADR-0012).

Terraform's ``plan``/``validate`` in CI catch schema errors, but they run only
for PRs that touch ``infra/**`` and they depend on the remote state. These
offline checks pin the two things the live apply got wrong:

* the UI service enables IAP with the first-class ``iap_enabled`` field, not the
  ``run.googleapis.com/iap-enabled`` annotation that Cloud Run API v2 rejects;
* the allowlist uses the per-service IAP IAM resource, not a project-wide
  ``roles/iap.httpsResourceAccessor`` grant.

They also pin the google-beta minimum to the first release that has both
features (``iap_enabled`` in 6.30, the per-service IAM resource in 6.31).
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_INFRA = _REPO_ROOT / "infra"
_MAIN_TF = _INFRA / "main.tf"
_PROVIDERS_TF = _INFRA / "providers.tf"


def _resource_block(text: str, resource_type: str, name: str) -> str:
    """Return the body of ``resource "<type>" "<name>" { ... }``."""
    header = re.search(
        rf'resource\s+"{re.escape(resource_type)}"\s+"{re.escape(name)}"\s*\{{',
        text,
    )
    assert header is not None, f"resource {resource_type}.{name} not found"

    start = header.end()
    depth = 1
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index]
    raise AssertionError(f"unbalanced braces in resource {resource_type}.{name}")


def test_legacy_iap_annotation_is_gone() -> None:
    """Cloud Run API v2 rejects the system annotation."""
    for path in _INFRA.glob("*.tf"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r'"run\.googleapis\.com/iap-enabled"\s*=', text), (
            f"{path.name} still sets the rejected IAP annotation"
        )


def test_ui_service_enables_iap_with_the_field() -> None:
    block = _resource_block(
        _MAIN_TF.read_text(encoding="utf-8"), "google_cloud_run_v2_service", "ui"
    )
    assert "provider = google-beta" in block
    assert re.search(r"^\s*iap_enabled\s*=", block, re.MULTILINE)
    assert re.search(r'^\s*launch_stage\s*=.*"BETA"', block, re.MULTILINE)


def test_allowlist_is_per_service_not_project_wide() -> None:
    text = _MAIN_TF.read_text(encoding="utf-8")
    assert 'resource "google_iap_web_cloud_run_service_iam_member" "iap_web_user"' in text
    assert 'resource "google_project_iam_member" "iap_web_user"' not in text

    block = _resource_block(text, "google_iap_web_cloud_run_service_iam_member", "iap_web_user")
    assert "cloud_run_service_name = google_cloud_run_v2_service.ui.name" in block
    assert 'role                   = "roles/iap.httpsResourceAccessor"' in block


def _minor_version(constraint: str) -> tuple[int, int]:
    match = re.search(r"~>\s*(\d+)\.(\d+)", constraint)
    assert match is not None, f"cannot parse version constraint {constraint!r}"
    return int(match.group(1)), int(match.group(2))


def test_google_beta_pin_supports_both_iap_features() -> None:
    text = _PROVIDERS_TF.read_text(encoding="utf-8")
    block = re.search(r"google-beta\s*=\s*\{(?P<body>.*?)\}", text, re.DOTALL)
    assert block is not None, "google-beta provider block not found"
    constraint = re.search(r'version\s*=\s*"([^"]+)"', block.group("body"))
    assert constraint is not None
    assert _minor_version(constraint.group(1)) >= (6, 31)
