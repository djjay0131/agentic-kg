"""Authentication routes (API key + human identity).

``GET /api/auth/check`` exists so an in-GCP Cloud Run Job can prove that the
API accepts the key currently stored in Secret Manager (ADR-0007 rule 5:
verification is by effect, not by value). It is mounted behind
``require_api_key``; PR-5 mounts ``/api/query`` and ``/api/provenance/*`` behind
the same dependency.

``GET /api/auth/me`` returns the verified human identity used by the UI header
(ADR-0012): the IAP-authenticated email in staging, or the development
``X-User-ID`` when ``AUTH_MODE=dev``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from agentic_kg_api.dependencies import current_user, require_api_key

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/check", dependencies=[Depends(require_api_key)])
def auth_check() -> dict[str, bool]:
    """Return ``{"ok": true}`` when the request carries a valid key."""
    return {"ok": True}


@router.get("/me")
def auth_me(user: str = Depends(current_user)) -> dict[str, str]:
    """Return the verified human email for the current request."""
    return {"email": user}


__all__ = ["router"]
