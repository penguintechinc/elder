"""HTTP Request Action Node for Streams Workflow.

Executes async HTTP requests with configurable methods, headers, retries.
SECURITY: SSRF-guarded — _guard_ssrf() rejects non-http(s) schemes and any
host resolving to an internal/reserved address; auto-redirects are disabled.
Ported from icestreams-worker.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from ...executor.node_registry import register_node
from ..base import BaseNode

# Re-exported under the historical private names so existing importers keep
# working; the canonical home for the SSRF guard is nodes/net_guard.py.
from ..net_guard import guard_ssrf as _guard_ssrf  # noqa: F401
from ..net_guard import is_blocked_ip as _is_blocked_ip  # noqa: F401

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class RetryConfig:
    """Retry configuration for HTTP requests."""

    max_retries: int = 3
    backoff_factor: float = 1.0
    backoff_multiplier: float = 2.0
    jitter: bool = True


@register_node("action_http_request", "actions", "HTTP Request")
class HttpRequestAction(BaseNode):
    """Execute async HTTP requests with configurable methods and parameters."""

    node_type = "action_http_request"
    name = "HTTP Request"
    description = "Execute HTTP requests with configurable methods and parameters"
    category = "actions"

    @classmethod
    def inputs(cls) -> List[Dict[str, Any]]:
        """Define input ports for the HTTP request node."""
        return [
            {
                "name": "url",
                "description": "Request URL",
                "required": True,
                "data_type": "string",
            },
            {
                "name": "method",
                "description": "HTTP method (GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS)",
                "required": False,
                "data_type": "string",
            },
            {
                "name": "body",
                "description": "Request body data (for POST/PUT/PATCH)",
                "required": False,
                "data_type": "any",
            },
            {
                "name": "headers",
                "description": "Custom HTTP headers as dictionary",
                "required": False,
                "data_type": "object",
            },
            {
                "name": "params",
                "description": "Query parameters as dictionary",
                "required": False,
                "data_type": "object",
            },
        ]

    @classmethod
    def outputs(cls) -> List[Dict[str, Any]]:
        """Define output ports for the HTTP request node."""
        return [
            {
                "name": "response",
                "description": "HTTP response data",
                "data_type": "object",
            },
            {
                "name": "status",
                "description": "HTTP status code",
                "data_type": "number",
            },
            {
                "name": "headers",
                "description": "Response headers",
                "data_type": "object",
            },
            {
                "name": "body",
                "description": "Response body",
                "data_type": "any",
            },
        ]

    async def _make_request(
        self,
        url: str,
        method: str,
        headers: Optional[Dict[str, str]],
        params: Optional[Dict[str, str]],
        body: Optional[Any],
        timeout: float,
        retry_config: RetryConfig,
    ) -> httpx.Response:
        """Make HTTP request with retry logic.

        Auto-redirects are disabled so a 3xx cannot bounce the request to an
        internal address that bypassed the pre-flight SSRF guard.
        """
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            for attempt in range(retry_config.max_retries + 1):
                try:
                    request_kwargs = {
                        "url": url,
                        "method": method,
                        "headers": headers or {},
                        "params": params or {},
                    }

                    if body is not None and method not in {"GET", "HEAD", "OPTIONS"}:
                        if isinstance(body, (dict, list)):
                            request_kwargs["json"] = body
                        else:
                            request_kwargs["content"] = str(body)

                    response = await client.request(**request_kwargs)

                    if response.status_code < 500:
                        return response

                    if attempt < retry_config.max_retries:
                        wait_time = retry_config.backoff_factor * (
                            retry_config.backoff_multiplier**attempt
                        )
                        if retry_config.jitter:
                            wait_time *= random.uniform(0.5, 1.5)
                        self.log_warning(
                            f"HTTP request failed with {response.status_code}, "
                            f"retrying in {wait_time:.2f}s (attempt {attempt + 1}/{retry_config.max_retries})"
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        return response

                except httpx.TimeoutException as e:
                    if attempt < retry_config.max_retries:
                        wait_time = retry_config.backoff_factor * (
                            retry_config.backoff_multiplier**attempt
                        )
                        self.log_warning(
                            f"HTTP request timeout, retrying in {wait_time:.2f}s "
                            f"(attempt {attempt + 1}/{retry_config.max_retries})"
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        raise

                except (httpx.ConnectError, httpx.NetworkError) as e:
                    if attempt < retry_config.max_retries:
                        wait_time = retry_config.backoff_factor * (
                            retry_config.backoff_multiplier**attempt
                        )
                        self.log_warning(
                            f"HTTP connection error, retrying in {wait_time:.2f}s "
                            f"(attempt {attempt + 1}/{retry_config.max_retries})"
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        raise

            raise RuntimeError("Failed to complete HTTP request after retries")

    def _parse_response_body(self, response: httpx.Response, content_type: str) -> Any:
        """Parse response body based on content type."""
        if not response.content:
            return None

        if "application/json" in content_type:
            try:
                return response.json()
            except json.JSONDecodeError:
                return response.text

        if content_type.startswith("text/"):
            return response.text

        if content_type.startswith("application/"):
            try:
                return response.json()
            except json.JSONDecodeError:
                return response.text

        return response.text

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Execute HTTP request."""
        start_time = time.perf_counter()

        url = inputs.get("url", "")
        if not url:
            raise ValueError("URL is required")

        # SSRF guard: reject non-http(s) and internal/reserved targets before
        # issuing any request. Workflow authors supply arbitrary URLs, so this
        # protects cloud metadata endpoints and internal services.
        _guard_ssrf(url)

        method = inputs.get("method") or self.get_config_value("method", "GET")
        method = method.upper()
        body = inputs.get("body")
        headers = inputs.get("headers") or self.get_config_value("headers", {})
        params = inputs.get("params") or self.get_config_value("params", {})

        timeout = float(self.get_config_value("timeout", 30))
        max_retries = int(self.get_config_value("maxRetries", 3))

        retry_config = RetryConfig(
            max_retries=max_retries,
            backoff_factor=float(self.get_config_value("backoffFactor", 1.0)),
            backoff_multiplier=float(self.get_config_value("backoffMultiplier", 2.0)),
            jitter=self.get_config_value("jitter", True),
        )

        try:
            self.log_info(f"HTTP {method} request to {url}")
            response = await self._make_request(
                url, method, headers, params, body, timeout, retry_config
            )

            content_type = response.headers.get("content-type", "text/plain")
            response_body = self._parse_response_body(response, content_type)

            self.log_info(f"HTTP response: {response.status_code}")

            response_obj = {
                "status": response.status_code,
                "headers": dict(response.headers),
                "body": response_body,
            }

            return {
                "response": {
                    "data": response_obj,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "status": {
                    "data": response.status_code,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "headers": {
                    "data": dict(response.headers),
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "body": {
                    "data": response_body,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
            }

        except asyncio.TimeoutError as e:
            self.log_error(f"HTTP request timeout: {e}")
            raise

        except httpx.NetworkError as e:
            self.log_error(f"HTTP network error: {e}")
            raise

        except Exception as e:
            self.log_error(f"HTTP request failed: {e}")
            raise
