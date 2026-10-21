"""API configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

#: The non-secret version Terraform seeds into the ``API_KEY`` Secret Manager
#: secret so Cloud Run can create the API revision before the first rotation.
#: It is public (it is in this repository), so treating it as a valid key would
#: be an open door. It means "auth not configured", exactly like an empty value.
API_KEY_PLACEHOLDER = "unset-seed-not-an-api-key"


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
