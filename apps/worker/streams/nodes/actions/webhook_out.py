"""Webhook Out Action Node for Streams Workflow.

Sends outbound webhooks to configured URLs with Bearer/Basic/API-key auth and
exponential-backoff retry.

SECURITY: SSRF-guarded — guard_ssrf() rejects non-http(s) schemes and any host
resolving to an internal/reserved address (blocks cloud metadata + internal
services); auto-redirects are disabled so a 3xx cannot bounce past the guard.
Ported from icestreams-worker; converted to the Elder node contract.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from ...executor.node_registry import register_node
from ..base import BaseNode
from ..net_guard import guard_ssrf

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class AuthConfig:
    """Authentication configuration for outbound webhooks."""

    auth_type: str = "none"  # none, bearer, basic, apikey
    bearer_token: Optional[str] = None
    basic_username: Optional[str] = None
    basic_password: Optional[str] = None
    api_key_header: Optional[str] = None
    api_key_value: Optional[str] = None

    def build_headers(self) -> Dict[str, str]:
        """Build authentication headers based on auth type."""
        headers: Dict[str, str] = {}

        if self.auth_type == "bearer" and self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        elif self.auth_type == "basic" and self.basic_username and self.basic_password:
            credentials = base64.b64encode(
                f"{self.basic_username}:{self.basic_password}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {credentials}"
        elif self.auth_type == "apikey" and self.api_key_header and self.api_key_value:
            headers[self.api_key_header] = self.api_key_value

        return headers


@register_node("action_webhook_out", "actions", "Webhook Out")
class WebhookOutAction(BaseNode):
    """Send outbound webhooks with authentication and retry logic."""

    node_type = "action_webhook_out"
    name = "Webhook Out"
    description = "Send webhooks with authentication and exponential backoff retry"
    category = "actions"

    @classmethod
    def inputs(cls) -> List[Dict[str, Any]]:
        """Define input ports for the webhook out node."""
        return [
            {
                "name": "url",
                "description": "Webhook URL to send to",
                "required": True,
                "data_type": "string",
            },
            {
                "name": "payload",
                "description": "Payload data to send in webhook",
                "required": True,
                "data_type": "any",
            },
            {
                "name": "headers",
                "description": "Custom HTTP headers (overrides auth headers)",
                "required": False,
                "data_type": "object",
            },
        ]

    @classmethod
    def outputs(cls) -> List[Dict[str, Any]]:
        """Define output ports for the webhook out node."""
        return [
            {
                "name": "success",
                "description": "Whether webhook was sent successfully",
                "data_type": "bool",
            },
            {
                "name": "status",
                "description": "HTTP status code from webhook endpoint",
                "data_type": "number",
            },
            {
                "name": "message",
                "description": "Status message or error description",
                "data_type": "string",
            },
        ]

    def _build_auth_config(self) -> AuthConfig:
        """Build authentication configuration from node config."""
        return AuthConfig(
            auth_type=self.get_config_value("authType", "none"),
            bearer_token=self.get_config_value("bearerToken"),
            basic_username=self.get_config_value("basicUsername"),
            basic_password=self.get_config_value("basicPassword"),
            api_key_header=self.get_config_value("apiKeyHeader"),
            api_key_value=self.get_config_value("apiKeyValue"),
        )

    async def _send_webhook(
        self,
        url: str,
        payload: Any,
        custom_headers: Optional[Dict[str, str]],
        timeout: float,
        max_retries: int,
    ) -> tuple[bool, int, str]:
        """Send webhook with retry logic.

        Auto-redirects are disabled so a 3xx cannot bounce the request to an
        internal address that bypassed the pre-flight SSRF guard.
        """
        auth_headers = self._build_auth_config().build_headers()

        headers = {"Content-Type": "application/json"}
        headers.update(auth_headers)
        if custom_headers:
            headers.update(custom_headers)

        if isinstance(payload, (dict, list)):
            body = json.dumps(payload)
        else:
            body = str(payload)

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            for attempt in range(max_retries + 1):
                try:
                    response = await client.post(url, content=body, headers=headers)

                    if response.status_code < 500:
                        success = 200 <= response.status_code < 300
                        return (
                            success,
                            response.status_code,
                            f"HTTP {response.status_code}",
                        )

                    if attempt < max_retries:
                        wait_time = (2**attempt) * 1.0
                        self.log_warning(
                            f"Webhook send failed with {response.status_code}, "
                            f"retrying in {wait_time:.2f}s "
                            f"(attempt {attempt + 1}/{max_retries})"
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        return (
                            False,
                            response.status_code,
                            f"HTTP {response.status_code}",
                        )

                except (asyncio.TimeoutError, httpx.TimeoutException) as e:
                    if attempt < max_retries:
                        wait_time = (2**attempt) * 1.0
                        self.log_warning(
                            f"Webhook send timeout, retrying in {wait_time:.2f}s "
                            f"(attempt {attempt + 1}/{max_retries})"
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        return False, 0, f"Timeout: {e}"

                except (httpx.ConnectError, httpx.NetworkError) as e:
                    if attempt < max_retries:
                        wait_time = (2**attempt) * 1.0
                        self.log_warning(
                            f"Webhook connect error, retrying in {wait_time:.2f}s "
                            f"(attempt {attempt + 1}/{max_retries})"
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        return False, 0, f"Connection error: {e}"

        return False, 0, "Failed after retries"

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Execute webhook send."""
        url = inputs.get("url", "")
        if not url:
            raise ValueError("Webhook URL is required")

        # SSRF guard: reject non-http(s) and internal/reserved targets before
        # issuing any request. Workflow authors supply arbitrary URLs.
        guard_ssrf(url)

        payload = inputs.get("payload", {})
        custom_headers = inputs.get("headers")

        timeout = float(self.get_config_value("timeout", 30))
        max_retries = int(self.get_config_value("maxRetries", 3))

        self.log_info(f"Sending webhook to {url}")
        success, status, message = await self._send_webhook(
            url, payload, custom_headers, timeout, max_retries
        )

        if success:
            self.log_info(f"Webhook sent successfully: {message}")

        node_id = self.context.get("node_id", "")
        return {
            "success": {"data": success, "metadata": {}, "source_node_id": node_id},
            "status": {"data": status, "metadata": {}, "source_node_id": node_id},
            "message": {"data": message, "metadata": {}, "source_node_id": node_id},
        }
