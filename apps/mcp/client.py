"""Elder API HTTP client with authentication."""

from __future__ import annotations

from typing import Optional

import httpx
from auth import ElderSession


class ElderClient:
    """Thin async HTTPX client for Elder API."""

    def __init__(self, session: ElderSession):
        """
        Initialize Elder API client.

        Args:
            session: ElderSession with valid token
        """
        self.session = session

    async def get(self, path: str, params: Optional[dict] = None) -> dict:
        """
        GET request to Elder API.

        Args:
            path: API path (e.g., '/api/v1/entities')
            params: Query parameters

        Returns:
            JSON response as dict

        Raises:
            ValueError: If session expired
            httpx.HTTPError: On API error
        """
        if not self.session.is_valid():
            raise ValueError(
                "Elder session expired. Restart the MCP server to re-authenticate."
            )

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.session.base_url}{path}",
                params=params,
                headers={"Authorization": f"Bearer {self.session.token}"},
                timeout=30.0,
            )

        return self._handle_response(response)

    async def post(self, path: str, body: dict) -> dict:
        """
        POST request to Elder API.

        Args:
            path: API path
            body: Request body

        Returns:
            JSON response as dict

        Raises:
            ValueError: If session expired
            httpx.HTTPError: On API error
        """
        if not self.session.is_valid():
            raise ValueError(
                "Elder session expired. Restart the MCP server to re-authenticate."
            )

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.session.base_url}{path}",
                json=body,
                headers={"Authorization": f"Bearer {self.session.token}"},
                timeout=30.0,
            )

        return self._handle_response(response)

    async def patch(self, path: str, body: dict) -> dict:
        """
        PATCH request to Elder API.

        Args:
            path: API path
            body: Request body (partial fields)

        Returns:
            JSON response as dict

        Raises:
            ValueError: If session expired
            httpx.HTTPError: On API error
        """
        if not self.session.is_valid():
            raise ValueError(
                "Elder session expired. Restart the MCP server to re-authenticate."
            )

        async with httpx.AsyncClient() as client:
            response = await client.patch(
                f"{self.session.base_url}{path}",
                json=body,
                headers={"Authorization": f"Bearer {self.session.token}"},
                timeout=30.0,
            )

        return self._handle_response(response)

    async def delete(self, path: str) -> None:
        """
        DELETE request to Elder API.

        Args:
            path: API path

        Raises:
            ValueError: If session expired
            httpx.HTTPError: On API error
        """
        if not self.session.is_valid():
            raise ValueError(
                "Elder session expired. Restart the MCP server to re-authenticate."
            )

        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{self.session.base_url}{path}",
                headers={"Authorization": f"Bearer {self.session.token}"},
                timeout=30.0,
            )

        self._handle_response(response)

    @staticmethod
    def _handle_response(response: httpx.Response) -> dict:
        """
        Handle HTTP response and raise appropriate errors.

        Args:
            response: httpx.Response

        Returns:
            JSON response as dict

        Raises:
            ValueError: For 4xx errors
            httpx.HTTPError: For 5xx errors
        """
        if 200 <= response.status_code < 300:
            try:
                return response.json()
            except Exception:
                return {}

        if response.status_code == 403:
            raise ValueError("Permission denied")
        elif response.status_code == 404:
            raise ValueError("Not found")
        elif 400 <= response.status_code < 500:
            try:
                error_data = response.json()
                error_msg = error_data.get("error", response.text)
            except Exception:
                error_msg = response.text
            raise ValueError(f"Client error: {error_msg}")
        else:
            try:
                error_data = response.json()
                error_msg = error_data.get("error", response.text)
            except Exception:
                error_msg = response.text
            raise httpx.HTTPError(f"Server error ({response.status_code}): {error_msg}")
