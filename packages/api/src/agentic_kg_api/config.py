"""API configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class APIConfig:
    """API server configuration."""

    host: str = field(default_factory=lambda: os.getenv("API_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("API_PORT", "8000")))
    debug: bool = field(default_factory=lambda: os.getenv("API_DEBUG", "false").lower() == "true")
    cors_origins: list[str] = field(
        default_factory=lambda: os.getenv(
            "CORS_ORIGINS", "http://localhost:3000,http://localhost:8501"
        ).split(",")
    )
    api_key: str = field(default_factory=lambda: os.getenv("API_KEY", ""))

    @property
    def requires_auth(self) -> bool:
        """Whether API key authentication is required."""
        return bool(self.api_key)


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
