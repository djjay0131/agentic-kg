"""
Custom exceptions for the Data Acquisition module.

Provides specific exception types for API errors, rate limiting,
and data processing issues.
"""


class DataAcquisitionError(Exception):
    """Base exception for data acquisition errors."""

    pass


class APIError(DataAcquisitionError):
    """Raised when an API request fails."""

    def __init__(
        self,
        message: str,
        source: str,
        status_code: int | None = None,
        response_body: str | None = None,
    ):
        self.source = source
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(f"[{source}] {message}")


class RateLimitError(APIError):
    """Raised when rate limit is exceeded."""

    def __init__(
        self,
        source: str,
        retry_after: float | None = None,
    ):
        self.retry_after = retry_after
        message = "Rate limit exceeded"
        if retry_after:
            message += f" (retry after {retry_after:.1f}s)"
        super().__init__(message, source, status_code=429)


class NotFoundError(DataAcquisitionError):
    """Raised when a requested resource is not found."""

    def __init__(self, resource_type: str, identifier: str, source: str):
        self.resource_type = resource_type
        self.identifier = identifier
        self.source = source
        super().__init__(f"[{source}] {resource_type} not found: {identifier}")


class ValidationError(DataAcquisitionError):
    """Raised when data validation fails."""

    def __init__(self, message: str, field: str | None = None):
        self.field = field
        super().__init__(message)


class CircuitOpenError(DataAcquisitionError):
    """Raised when circuit breaker is open and requests are blocked."""

    def __init__(self, source: str, cooldown_remaining: float):
        self.source = source
        self.cooldown_remaining = cooldown_remaining
        super().__init__(
            f"[{source}] Circuit breaker open, retry in {cooldown_remaining:.1f}s"
        )


class NormalizationError(DataAcquisitionError):
    """Raised when paper metadata normalization fails."""

    def __init__(self, message: str, source: str, raw_data: dict | None = None):
        self.source = source
        self.raw_data = raw_data
        super().__init__(f"[{source}] Normalization failed: {message}")


class CacheError(DataAcquisitionError):
    """Raised when cache operations fail."""

    pass


__all__ = [
    "DataAcquisitionError",
    "APIError",
    "RateLimitError",
    "NotFoundError",
    "ValidationError",
    "CircuitOpenError",
    "NormalizationError",
    "CacheError",
]
