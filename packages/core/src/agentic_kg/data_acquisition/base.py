"""
Base API client infrastructure for Data Acquisition.

Provides abstract base class and common functionality for all API clients.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

from agentic_kg.data_acquisition.exceptions import APIError, NotFoundError

logger = logging.getLogger(__name__)


class BaseAPIClient(ABC):
    """
    Abstract base class for API clients.

    Provides common HTTP client setup, request logging, and error handling.
    Subclasses must implement source-specific methods.
    """

    # Source identifier (e.g., "semantic_scholar", "arxiv", "openalex")
    SOURCE: str = ""

    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
    ):
        """
        Initialize the base API client.

        Args:
            base_url: Base URL for API requests
            timeout: Request timeout in seconds
            headers: Default headers for all requests
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._default_headers = headers or {}

        # Create async HTTP client
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout),
                headers=self._default_headers,
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "BaseAPIClient":
        """Async context manager entry."""
        await self._get_client()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """
        Make an HTTP request with logging.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (appended to base_url)
            params: Query parameters
            headers: Additional headers
            json: JSON body for POST requests

        Returns:
            httpx.Response object

        Raises:
            APIError: If request fails
            NotFoundError: If resource not found (404)
        """
        client = await self._get_client()
        url = endpoint if endpoint.startswith("http") else f"{self.base_url}/{endpoint.lstrip('/')}"

        logger.debug(
            "API request: %s %s params=%s",
            method,
            url,
            params,
        )

        try:
            response = await client.request(
                method=method,
                url=url,
                params=params,
                headers=headers,
                json=json,
            )

            logger.debug(
                "API response: %s %s status=%d",
                method,
                url,
                response.status_code,
            )

            return response

        except httpx.TimeoutException as e:
            logger.warning("API timeout: %s %s", method, url)
            raise APIError(
                message=f"Request timed out after {self.timeout}s",
                source=self.SOURCE,
            ) from e

        except httpx.RequestError as e:
            logger.warning("API request error: %s %s - %s", method, url, str(e))
            raise APIError(
                message=f"Request failed: {str(e)}",
                source=self.SOURCE,
            ) from e

    def _handle_response(self, response: httpx.Response) -> dict[str, Any]:
        """
        Handle API response and check for errors.

        Args:
            response: HTTP response object

        Returns:
            Parsed JSON response

        Raises:
            NotFoundError: If 404 response
            APIError: If other error response
        """
        if response.status_code == 404:
            raise NotFoundError(
                resource_type="resource",
                identifier=str(response.url),
                source=self.SOURCE,
            )

        if response.status_code >= 400:
            raise APIError(
                message=f"Request failed with status {response.status_code}",
                source=self.SOURCE,
                status_code=response.status_code,
                response_body=response.text[:500] if response.text else None,
            )

        try:
            return response.json()
        except Exception as e:
            raise APIError(
                message=f"Failed to parse JSON response: {str(e)}",
                source=self.SOURCE,
                status_code=response.status_code,
                response_body=response.text[:500] if response.text else None,
            ) from e

    async def get(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Make a GET request.

        Args:
            endpoint: API endpoint
            params: Query parameters
            headers: Additional headers

        Returns:
            Parsed JSON response
        """
        response = await self._request("GET", endpoint, params=params, headers=headers)
        return self._handle_response(response)

    async def post(
        self,
        endpoint: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Make a POST request.

        Args:
            endpoint: API endpoint
            json: JSON body
            params: Query parameters
            headers: Additional headers

        Returns:
            Parsed JSON response
        """
        response = await self._request(
            "POST", endpoint, params=params, headers=headers, json=json
        )
        return self._handle_response(response)

    @abstractmethod
    async def get_paper(self, identifier: str) -> dict[str, Any]:
        """
        Get paper by identifier.

        Args:
            identifier: Paper identifier (DOI, arXiv ID, etc.)

        Returns:
            Raw paper data from the API
        """
        pass

    @abstractmethod
    async def search_papers(
        self,
        query: str,
        limit: int = 10,
        offset: int = 0,
    ) -> dict[str, Any]:
        """
        Search for papers.

        Args:
            query: Search query
            limit: Maximum results to return
            offset: Offset for pagination

        Returns:
            Search results from the API
        """
        pass


__all__ = ["BaseAPIClient"]
