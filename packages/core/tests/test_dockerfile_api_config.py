"""Structural guard: the API image must carry the versioned autonomy policy.

Root cause (F9, 2026-11-11): ``ResearchCycleService`` loaded
``config/research-autonomy.yaml`` while the API was being constructed. The API
image installed ``packages/core`` but never copied the repo-root ``config/``
directory, so ``GET /api/research/cycles`` and ``GET /api/research/gates`` both
returned 500 ``FileNotFoundError``.

The service now loads the policy lazily, so reads work even without the file;
this test additionally pins the image shape so gate decisions keep working.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_API_DOCKERFILE = _REPO_ROOT / "docker" / "Dockerfile.api"


def test_api_image_copies_the_autonomy_policy_directory() -> None:
    text = _API_DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY config/ /app/config/" in text
