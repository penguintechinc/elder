"""Authentication and session management for Elder MCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from jose import jwt
from jose.exceptions import JWTError, ExpiredSignatureError


@dataclass(slots=True)
class ElderSession:
    """Manages Elder API session with OAuth2 token."""

    base_url: str
    token: str
    expires_at: datetime

    def is_valid(self) -> bool:
        """Check if session token is still valid."""
        now = datetime.now(timezone.utc)
        return now < self.expires_at

    @classmethod
    async def from_credentials(
        cls, base_url: str, username: str, password: str
    ) -> "ElderSession":
        """
        Authenticate with Elder API and create a session.

        Args:
            base_url: Elder API base URL
            username: Username/email
            password: Password

        Returns:
            ElderSession with valid token

        Raises:
            httpx.HTTPError: If login request fails
            ValueError: If response doesn't contain token
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{base_url}/api/v1/auth/login",
                json={"username": username, "password": password},
                timeout=10.0,
            )
            response.raise_for_status()

        data = response.json()
        if "access_token" not in data:
            raise ValueError("No access_token in login response")

        token = data["access_token"]
        expires_at = cls._get_expiry_from_token(token, base_url)

        return cls(base_url=base_url, token=token, expires_at=expires_at)

    @classmethod
    def from_token(cls, base_url: str, token: str) -> "ElderSession":
        """
        Create a session from an existing token.

        Args:
            base_url: Elder API base URL
            token: Bearer token

        Returns:
            ElderSession with provided token

        Raises:
            ValueError: If token is invalid or expired
        """
        expires_at = cls._get_expiry_from_token(token, base_url)
        return cls(base_url=base_url, token=token, expires_at=expires_at)

    @staticmethod
    def _get_expiry_from_token(token: str, base_url: str) -> datetime:
        """
        Extract expiry time from JWT token.

        Args:
            token: JWT token
            base_url: Used for error messages only

        Returns:
            datetime of token expiry (capped at 24h from now)

        Raises:
            ValueError: If token is invalid
        """
        try:
            # Decode without verification (we only need exp claim)
            payload = jwt.get_unverified_claims(token)
        except (JWTError, ExpiredSignatureError) as e:
            raise ValueError(f"Invalid JWT token: {e}")

        if "exp" not in payload:
            raise ValueError("Token missing 'exp' claim")

        exp_timestamp = payload["exp"]
        exp_from_token = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)

        # Cap at 24h from now
        max_expiry = datetime.now(timezone.utc) + timedelta(hours=24)
        expires_at = min(exp_from_token, max_expiry)

        return expires_at
