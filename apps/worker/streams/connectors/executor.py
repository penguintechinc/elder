"""Connector Action Executor with SSRF protection.

Executes connector actions and transforms by building HTTP requests with
variable interpolation. Workflow authors control connector config, so all
templated endpoints are guarded against SSRF before any request is issued.

Ported from icestreams-worker; converted to Elder node contract.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict

import httpx

from apps.worker.streams.nodes.net_guard import guard_ssrf

logger = logging.getLogger(__name__)


class ConnectorExecutionError(Exception):
    """Raised when connector action execution fails."""

    pass


class ConnectorActionExecutor:
    """Execute connector actions by building and sending SSRF-guarded HTTP requests."""

    # Pattern for {{variable}} interpolation
    VARIABLE_PATTERN = re.compile(r"\{\{(\w+(?:\.\w+)*)\}\}")

    def _interpolate_value(
        self,
        value: Any,
        inputs: Dict[str, Any],
        variables: Dict[str, Any],
        config: Dict[str, Any],
    ) -> Any:
        """Interpolate variables in a value.

        Supports {{variable}}, {{input.field}}, {{config.field}} syntax.
        """
        if not isinstance(value, str):
            return value

        def replace_match(match: re.Match) -> str:
            path = match.group(1)
            parts = path.split(".")

            # Determine which dict to look in
            if parts[0] == "input" and len(parts) > 1:
                data = inputs
                parts = parts[1:]
            elif parts[0] == "config" and len(parts) > 1:
                data = config
                parts = parts[1:]
            elif parts[0] in inputs:
                data = inputs
            elif parts[0] in variables:
                data = variables
            elif parts[0] in config:
                data = config
            else:
                # Check nested in input data
                input_data = inputs.get("in", {})
                if isinstance(input_data, dict) and parts[0] in input_data:
                    data = input_data
                else:
                    return match.group(0)  # Keep original if not found

            # Navigate nested path
            result = data
            for part in parts:
                if isinstance(result, dict) and part in result:
                    result = result[part]
                else:
                    return match.group(0)  # Keep original if not found

            return str(result) if result is not None else ""

        return self.VARIABLE_PATTERN.sub(replace_match, value)

    def _interpolate_dict(
        self,
        data: Dict[str, Any],
        inputs: Dict[str, Any],
        variables: Dict[str, Any],
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Recursively interpolate variables in a dictionary."""
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                result[key] = self._interpolate_value(value, inputs, variables, config)
            elif isinstance(value, dict):
                result[key] = self._interpolate_dict(value, inputs, variables, config)
            elif isinstance(value, list):
                result[key] = [
                    (
                        self._interpolate_value(item, inputs, variables, config)
                        if isinstance(item, str)
                        else (
                            self._interpolate_dict(item, inputs, variables, config)
                            if isinstance(item, dict)
                            else item
                        )
                    )
                    for item in value
                ]
            else:
                result[key] = value
        return result

    def _build_request_body(
        self,
        endpoint: str,
        method: str,
        request_body_template: str,
        config_schema: tuple,
        config: Dict[str, Any],
        inputs: Dict[str, Any],
        variables: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        """Build request body from action definition and config."""
        if method.upper() in ("GET", "HEAD", "OPTIONS"):
            return None

        # If action has a body template, use it
        if request_body_template:
            try:
                interpolated = self._interpolate_value(
                    request_body_template, inputs, variables, config
                )
                return json.loads(interpolated)
            except json.JSONDecodeError:
                logger.warning("Failed to parse body template as JSON")

        # Otherwise, build from config schema
        body = {}
        for field in config_schema:
            if field.field in config:
                value = config[field.field]
                # Interpolate if supports variables
                if field.supports_variables and isinstance(value, str):
                    value = self._interpolate_value(value, inputs, variables, config)
                body[field.field] = value

        # Include input data if available
        input_data = inputs.get("in")
        if input_data is not None:
            if isinstance(input_data, dict):
                # Merge input data into body
                for key, val in input_data.items():
                    if key not in body:
                        body[key] = val
            else:
                body["data"] = input_data

        return body if body else None

    async def execute_action(
        self,
        connector_id: str,
        action_id: str,
        endpoint: str,
        method: str,
        request_body_template: str,
        config_schema: tuple,
        config: Dict[str, Any],
        inputs: Dict[str, Any],
        variables: Dict[str, Any],
        base_url: str,
    ) -> Dict[str, Any]:
        """Execute a connector action with SSRF guarding.

        Args:
            connector_id: Connector identifier (for logging).
            action_id: Action identifier (for logging).
            endpoint: API endpoint path (may contain {{vars}}).
            method: HTTP method.
            request_body_template: Optional Jinja2-style body template.
            config_schema: Tuple of ConfigField definitions.
            config: Node configuration.
            inputs: Node inputs.
            variables: Workflow variables.
            base_url: Base URL for the connector (e.g., http://localhost:5000).

        Returns:
            Action result data.

        Raises:
            ConnectorExecutionError: If action fails or SSRF guard blocks URL.
        """
        # Interpolate endpoint with variables
        interpolated_endpoint = self._interpolate_value(
            endpoint, inputs, variables, config
        )

        # Construct full URL
        if interpolated_endpoint.startswith(
            "http://"
        ) or interpolated_endpoint.startswith("https://"):
            full_url = interpolated_endpoint
        else:
            full_url = base_url.rstrip("/") + "/" + interpolated_endpoint.lstrip("/")

        # SECURITY: Guard SSRF before issuing any request
        # Workflow authors control connector config, so templated endpoints are
        # attacker-influenced. Reject non-http(s) schemes and internal/reserved IPs.
        try:
            guard_ssrf(full_url)
        except ValueError as e:
            logger.error(
                f"SSRF guard blocked connector action {connector_id}.{action_id}: {e}"
            )
            raise ConnectorExecutionError(f"Request blocked by SSRF guard: {e}") from e

        # Build request body
        body = self._build_request_body(
            interpolated_endpoint,
            method,
            request_body_template,
            config_schema,
            config,
            inputs,
            variables,
        )

        logger.info(
            f"Executing connector action {connector_id}.{action_id}: {method} {full_url}"
        )

        try:
            # Issue request with follow_redirects=False so a 3xx cannot bounce past the guard
            async with httpx.AsyncClient(
                timeout=30.0, follow_redirects=False
            ) as client:
                response = await client.request(
                    method=method,
                    url=full_url,
                    json=body,
                )
                response.raise_for_status()
                return response.json() if response.text else {}

        except httpx.HTTPStatusError as e:
            logger.error(f"Connector action {connector_id}.{action_id} failed: {e}")
            raise ConnectorExecutionError(
                f"Action '{action_id}' failed: HTTP {e.response.status_code}"
            ) from e
        except Exception as e:
            logger.error(f"Connector action {connector_id}.{action_id} failed: {e}")
            raise ConnectorExecutionError(
                f"Action '{action_id}' failed: {str(e)}"
            ) from e

    async def execute_transform(
        self,
        connector_id: str,
        transform_id: str,
        endpoint: str,
        method: str,
        config_schema: tuple,
        config: Dict[str, Any],
        inputs: Dict[str, Any],
        variables: Dict[str, Any],
        base_url: str,
    ) -> Dict[str, Any]:
        """Execute a connector transform with SSRF guarding.

        Args:
            connector_id: Connector identifier (for logging).
            transform_id: Transform identifier (for logging).
            endpoint: API endpoint path (may contain {{vars}}).
            method: HTTP method.
            config_schema: Tuple of ConfigField definitions.
            config: Node configuration.
            inputs: Node inputs.
            variables: Workflow variables.
            base_url: Base URL for the connector.

        Returns:
            Transform result data.

        Raises:
            ConnectorExecutionError: If transform fails or SSRF guard blocks URL.
        """
        if not endpoint:
            # No API call needed, just pass through
            return inputs.get("in", {})

        # Interpolate endpoint
        interpolated_endpoint = self._interpolate_value(
            endpoint, inputs, variables, config
        )

        # Construct full URL
        if interpolated_endpoint.startswith(
            "http://"
        ) or interpolated_endpoint.startswith("https://"):
            full_url = interpolated_endpoint
        else:
            full_url = base_url.rstrip("/") + "/" + interpolated_endpoint.lstrip("/")

        # SECURITY: Guard SSRF before issuing any request
        try:
            guard_ssrf(full_url)
        except ValueError as e:
            logger.error(
                f"SSRF guard blocked connector transform {connector_id}.{transform_id}: {e}"
            )
            raise ConnectorExecutionError(f"Request blocked by SSRF guard: {e}") from e

        # Build query params from config
        params = {}
        for field in config_schema:
            if field.field in config:
                value = config[field.field]
                if field.supports_variables and isinstance(value, str):
                    value = self._interpolate_value(value, inputs, variables, config)
                params[field.field] = value

        logger.info(
            f"Executing connector transform {connector_id}.{transform_id}: {method} {full_url}"
        )

        try:
            # Issue request with follow_redirects=False
            async with httpx.AsyncClient(
                timeout=30.0, follow_redirects=False
            ) as client:
                response = await client.request(
                    method=method,
                    url=full_url,
                    params=params if params else None,
                )
                response.raise_for_status()
                return response.json() if response.text else {}

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Connector transform {connector_id}.{transform_id} failed: {e}"
            )
            raise ConnectorExecutionError(
                f"Transform '{transform_id}' failed: HTTP {e.response.status_code}"
            ) from e
        except Exception as e:
            logger.error(
                f"Connector transform {connector_id}.{transform_id} failed: {e}"
            )
            raise ConnectorExecutionError(
                f"Transform '{transform_id}' failed: {str(e)}"
            ) from e
