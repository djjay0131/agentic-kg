"""API configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

#: The non-secret version Terraform seeds into the ``API_KEY`` Secret Manager
#: secret so Cloud Run can create the API revision before the first rotation.
#: It is public (it is in this repository), so treating it as a valid key would
#: be an open door. It means "auth not configured", exactly like an empty value.
API_KEY_PLACEHOLDER = "unset-seed-not-an-api-key"

AUTH_MODE_DEV = "dev"
AUTH_MODE_IAP = "iap"


def _auth_mode() -> str:
    """Return the configured human-auth mode, failing closed on typos."""
    value = os.getenv("AUTH_MODE", AUTH_MODE_DEV).strip().lower()
    if value == AUTH_MODE_DEV:
        return AUTH_MODE_DEV
    if value == AUTH_MODE_IAP:
        return AUTH_MODE_IAP
    return AUTH_MODE_IAP


@dataclass
class APIConfig:
    """API server configuration."""

    host: str = field(default_factory=lambda: os.getenv("API_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("API_PORT", "8000")))
    debug: bool = field(default_factory=lambda: os.getenv("API_DEBUG", "false").lower() == "true")
    cors_origins: list[str] = field(
        default_factory=lambda: (
            ["*"]
            if os.getenv("CORS_ORIGINS", "").strip() == "*"
            else [
                origin.strip()
                for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000 http://localhost:8501").split()
                if origin.strip()
            ]
        )
    )
    api_key: str = field(default_factory=lambda: os.getenv("API_KEY", ""))
    # Human identity (ADR-0012). ``dev`` accepts the development-only
    # ``X-User-ID`` header; ``iap`` requires a verified IAP signed assertion.
    # Anything unrecognised is treated as ``iap`` so a typo fails closed.
    auth_mode: str = field(default_factory=lambda: _auth_mode())
    # The signed-header audience IAP stamps into the assertion. On staging this
    # is the UI Cloud Run service's IAP audience; the UI server forwards the
    # user's assertion to the private API.
    iap_audience: str = field(default_factory=lambda: os.getenv("IAP_AUDIENCE", "").strip())
    # Read-only canonical projection router. Off by default: every
    # /api/canonical/* route returns 404 unless this is enabled (ADR-0005).
    canonical_api_enabled: bool = field(
        default_factory=lambda: (
            os.getenv("CANONICAL_API_ENABLED", "").strip().lower()
            in {"1", "true", "yes", "on"}
        )
    )

    @property
    def auth_configured(self) -> bool:
        """Whether a real API key is installed.

        Empty and the public Terraform placeholder both mean "no key". A gated
        router must fail closed (503) rather than compare against either.
        """
        return bool(self.api_key) and self.api_key != API_KEY_PLACEHOLDER

    @property
    def requires_auth(self) -> bool:
        """Whether API key authentication is required."""
        return self.auth_configured


_config: APIConfig | None = None


def get_api_config() -> APIConfig:
    """Get API configuration singleton."""
    global _config
    if _config is None:
        _config = APIConfig()
    return _config


def reset_api_config() -> None:
    """Reset config singleton (for testing)."""
    global _config
    _config = None
