"""API-key authentication check (spec R-3 C5).

``GET /api/auth/check`` exists so an in-GCP Cloud Run Job can prove that the
API accepts the key currently stored in Secret Manager (ADR-0007 rule 5:
verification is by effect, not by value). It is the first router mounted behind
``require_api_key``; PR-5 mounts ``/api/query`` and ``/api/provenance`` behind
the same dependency.

The dependency fails closed: an unset ``API_KEY`` (or the public Terraform
placeholder) yields 503 ``auth not configured``, never an open route.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from agentic_kg_api.dependencies import require_api_key

router = APIRouter(
    prefix="/api/auth",
    tags=["auth"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/check")
def auth_check() -> dict[str, bool]:
    """Return ``{"ok": true}`` when the request carries a valid key."""
    return {"ok": True}


__all__ = ["router"]
