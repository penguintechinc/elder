"""Connector Action Executor with SSRF protection.

Executes connector actions and transforms by building HTTP requests with
n8n-style variable interpolation. Workflow authors control connector config,
so all untrusted/external endpoints are guarded against SSRF before any request
is issued. Trusted internal Elder API calls are restricted to validated
relative ``/api/v1/`` paths routed to the server-configured ``ELDER_API_URL``.

Ported from icestreams-worker; converted to Elder node contract.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any
from urllib.parse import quote, unquote, urlparse

import httpx

from apps.worker.streams.nodes.net_guard import guard_ssrf
from apps.worker.streams.nodes.template import (
    _MISSING,
    interpolate_any,
    interpolate_value,
    resolve_expression,
)

logger = logging.getLogger(__name__)

_TEMPLATE_TOKEN_RE = re.compile(r"\{\{\s*(.+?)\s*\}\}")


class ConnectorExecutionError(Exception):
    """Raised when connector action execution fails."""

    pass


def _build_template_context(
    inputs: dict[str, Any],
    variables: dict[str, Any],
    config: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble template context while stripping transient credentials."""
    ctx = dict(context) if context else {}
    ctx["config"] = config or {}
    merged_global = dict(ctx.get("global_config") or {})
    if variables:
        merged_global.update(variables)
    merged_global.pop("_elder_auth_token", None)
    ctx["global_config"] = merged_global
    return ctx


def _extract_endpoint_param_names(endpoint: str) -> set[str]:
    """Extract parameter field names referenced in an endpoint template."""
    names: set[str] = set()
    for match in _TEMPLATE_TOKEN_RE.finditer(endpoint or ""):
        expr = match.group(1).strip()
        if expr.startswith("config."):
            names.add(expr[7:].split(".", 1)[0])
        elif expr.startswith("input."):
            names.add(expr[6:].split(".", 1)[0])
        elif not expr.startswith("$"):
            names.add(expr.split(".", 1)[0])
    return names


def _validate_elder_relative_path(path: str) -> None:
    """Validate that an Elder endpoint is a safe relative ``/api/v1/`` path."""
    if not path or not path.startswith("/api/v1/"):
        raise ConnectorExecutionError(
            f"Invalid Elder API endpoint path {path!r}: must start with '/api/v1/'"
        )
    if "://" in path or path.startswith("//") or "@" in path or "\\" in path:
        raise ConnectorExecutionError(
            f"Invalid Elder API endpoint path {path!r}: disallowed scheme, host, or separator"
        )
    if any(ch.isspace() for ch in path):
        raise ConnectorExecutionError(
            f"Invalid Elder API endpoint path {path!r}: whitespace is not allowed"
        )
    decoded = unquote(path)
    if any(seg == ".." for seg in path.split("/")) or any(
        seg == ".." for seg in decoded.split("/")
    ):
        raise ConnectorExecutionError(
            f"Invalid Elder API endpoint path {path!r}: path traversal is not allowed"
        )
    parsed = urlparse(path)
    if parsed.scheme or parsed.netloc:
        raise ConnectorExecutionError(
            f"Invalid Elder API endpoint path {path!r}: scheme/host not permitted"
        )


def _coerce_field_value(
    field: Any,
    raw_val: Any,
    ctx: dict[str, Any],
    inputs: dict[str, Any],
) -> Any:
    """Interpolate and coerce a ConfigField value according to its declared type."""
    if raw_val is None:
        return None

    field_type = getattr(field, "type", "string")
    supports_vars = getattr(field, "supports_variables", False)
    required = getattr(field, "required", False)
    field_name = getattr(field, "field", "")

    if isinstance(raw_val, str) and supports_vars:
        preserve = field_type in (
            "json",
            "object",
            "array",
            "number",
            "integer",
            "boolean",
            "checkbox",
        )
        val = interpolate_value(raw_val, ctx, inputs, preserve_type=preserve)
    elif isinstance(raw_val, (dict, list)) and supports_vars:
        val = interpolate_any(raw_val, ctx, inputs, preserve_type=True)
    else:
        val = raw_val

    if field_type in ("json", "object", "array", "keyvalue"):
        if isinstance(val, str):
            stripped = val.strip()
            if not stripped:
                return None
            try:
                return json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ConnectorExecutionError(
                    f"Field '{field_name}' must be valid JSON"
                ) from exc
        return val

    if field_type == "multiselect":
        if isinstance(val, str):
            stripped = val.strip()
            if not stripped:
                return None
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                pass
            return [part.strip() for part in stripped.split(",") if part.strip()]
        if isinstance(val, list) and not val and not required:
            return None
        return val

    if field_type in ("number", "integer"):
        if isinstance(val, str):
            stripped = val.strip()
            if not stripped:
                return None
            try:
                if field_type == "integer" or "." not in stripped:
                    return int(stripped)
                return float(stripped)
            except ValueError as exc:
                raise ConnectorExecutionError(
                    f"Field '{field_name}' must be a valid number"
                ) from exc
        return val

    if field_type in ("boolean", "checkbox"):
        if isinstance(val, str):
            return val.strip().lower() in ("1", "true", "yes", "on")
        return bool(val)

    if isinstance(val, str) and val == "" and not required:
        return None

    return val


class ConnectorActionExecutor:
    """Execute connector actions by building and sending SSRF-guarded HTTP requests."""

    # Pattern for {{variable}} interpolation
    VARIABLE_PATTERN = re.compile(r"\{\{(\w+(?:\.\w+)*)\}\}")

    def _interpolate_value(
        self,
        value: Any,
        inputs: dict[str, Any],
        variables: dict[str, Any],
        config: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> Any:
        """Interpolate variables in a value using the shared Streams template engine."""
        if not isinstance(value, str):
            return value
        ctx = _build_template_context(inputs, variables, config, context)
        return interpolate_value(value, ctx, inputs, preserve_type=False)

    def _interpolate_endpoint(
        self,
        endpoint: str,
        inputs: dict[str, Any],
        variables: dict[str, Any],
        config: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> str:
        """Interpolate and URL-encode path parameters inside an endpoint template."""
        if not endpoint:
            return ""
        ctx = _build_template_context(inputs, variables, config, context)

        def _replace(match: re.Match[str]) -> str:
            expr = match.group(1).strip()
            resolved = resolve_expression(expr, ctx, inputs)
            if resolved is _MISSING or resolved is None or str(resolved) == "":
                raise ConnectorExecutionError(
                    f"Missing required path parameter '{expr}' for endpoint '{endpoint}'"
                )
            return quote(str(resolved), safe="")

        return _TEMPLATE_TOKEN_RE.sub(_replace, endpoint)

    def _interpolate_dict(
        self,
        data: dict[str, Any],
        inputs: dict[str, Any],
        variables: dict[str, Any],
        config: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Recursively interpolate variables in a dictionary."""
        ctx = _build_template_context(inputs, variables, config, context)
        return interpolate_any(data, ctx, inputs, preserve_type=True)

    def _build_request_body(
        self,
        endpoint: str,
        method: str,
        request_body_template: str,
        config_schema: tuple,
        config: dict[str, Any],
        inputs: dict[str, Any],
        variables: dict[str, Any],
        context: dict[str, Any] | None = None,
        query_params: tuple[str, ...] = (),
        path_params: tuple[str, ...] = (),
    ) -> dict[str, Any] | None:
        """Build request body from action definition and config."""
        if method.upper() in ("GET", "HEAD", "OPTIONS"):
            return None

        ctx = _build_template_context(inputs, variables, config, context)

        if request_body_template:
            try:
                interpolated = interpolate_value(
                    request_body_template, ctx, inputs, preserve_type=False
                )
                return json.loads(interpolated)
            except json.JSONDecodeError:
                logger.warning("Failed to parse body template as JSON")

        excluded = (
            set(path_params)
            | set(query_params)
            | _extract_endpoint_param_names(endpoint)
        )

        body: dict[str, Any] = {}
        for field in config_schema:
            if field.field in excluded:
                continue
            if field.field in config:
                coerced = _coerce_field_value(field, config[field.field], ctx, inputs)
                if coerced is not None:
                    body[field.field] = coerced

        if method.upper() == "DELETE" and not body:
            return None

        # Only auto-merge raw `inputs["in"]` for schemaless connectors; typed
        # connectors (e.g. Elder) must not forward unrelated upstream fields.
        if not config_schema:
            input_data = inputs.get("in")
            if input_data is not None:
                if isinstance(input_data, dict):
                    for key, val in input_data.items():
                        if key not in body and key not in excluded:
                            body[key] = val
                else:
                    body["data"] = input_data

        return body if body else None

    def _build_query_params(
        self,
        endpoint: str,
        method: str,
        config_schema: tuple,
        config: dict[str, Any],
        inputs: dict[str, Any],
        variables: dict[str, Any],
        context: dict[str, Any] | None = None,
        query_params: tuple[str, ...] = (),
        path_params: tuple[str, ...] = (),
    ) -> dict[str, Any] | None:
        """Build query string parameters while excluding path parameters."""
        ctx = _build_template_context(inputs, variables, config, context)
        excluded = set(path_params) | _extract_endpoint_param_names(endpoint)
        allowed_query = set(query_params)
        schema_by_name = {f.field: f for f in config_schema}

        params: dict[str, Any] = {}
        if allowed_query:
            for key in allowed_query:
                if key in excluded or key not in config:
                    continue
                field = schema_by_name.get(key)
                if field is not None:
                    val = _coerce_field_value(field, config[key], ctx, inputs)
                else:
                    val = interpolate_value(
                        config[key], ctx, inputs, preserve_type=True
                    )
                if val is not None:
                    params[key] = val
        elif method.upper() in ("GET", "HEAD", "DELETE"):
            for field in config_schema:
                if field.field in excluded or field.field not in config:
                    continue
                val = _coerce_field_value(field, config[field.field], ctx, inputs)
                if val is not None:
                    params[field.field] = val

        return params if params else None

    def _resolve_target_url_and_headers(
        self,
        connector_id: str,
        op_id: str,
        interpolated_endpoint: str,
        variables: dict[str, Any],
        base_url: str,
    ) -> tuple[str, dict[str, str]]:
        """Resolve full request URL and auth headers, enforcing SSRF or trusted Elder routing."""
        headers: dict[str, str] = {}
        has_scheme_or_host = (
            interpolated_endpoint.startswith(("http://", "https://"))
            or "://" in interpolated_endpoint
            or interpolated_endpoint.startswith("//")
        )

        if connector_id == "elder" and not has_scheme_or_host:
            _validate_elder_relative_path(interpolated_endpoint)
            server_base = os.environ.get("ELDER_API_URL") or base_url
            if not server_base or not server_base.startswith(("http://", "https://")):
                raise ConnectorExecutionError(
                    "ELDER_API_URL is not configured or invalid"
                )
            full_url = server_base.rstrip("/") + interpolated_endpoint

            bearer = (variables or {}).get("_elder_auth_token") or os.environ.get(
                "ELDER_TOKEN"
            )
            api_key = os.environ.get("ELDER_API_KEY")
            if bearer:
                headers["Authorization"] = f"Bearer {bearer}"
            elif api_key:
                headers["X-API-Key"] = api_key

            return full_url, headers

        if interpolated_endpoint.startswith(("http://", "https://")):
            full_url = interpolated_endpoint
        else:
            full_url = base_url.rstrip("/") + "/" + interpolated_endpoint.lstrip("/")

        # SECURITY: Guard SSRF before issuing any external/untrusted request.
        try:
            guard_ssrf(full_url)
        except ValueError as e:
            logger.error(f"SSRF guard blocked connector {connector_id}.{op_id}: {e}")
            raise ConnectorExecutionError(f"Request blocked by SSRF guard: {e}") from e

        return full_url, headers

    @staticmethod
    def _parse_response(response: httpx.Response) -> dict[str, Any] | list[Any] | Any:
        """Parse HTTP response safely, handling 204 and empty bodies."""
        if response.status_code == 204 or not getattr(response, "content", b"1"):
            return {}
        text = getattr(response, "text", "")
        if isinstance(text, str) and not text.strip():
            return {}
        try:
            return response.json()
        except ValueError:
            return {"text": text} if text else {}

    async def _execute_http(
        self,
        *,
        kind: str,
        connector_id: str,
        op_id: str,
        endpoint: str,
        method: str,
        request_body_template: str,
        config_schema: tuple,
        config: dict[str, Any],
        inputs: dict[str, Any],
        variables: dict[str, Any],
        base_url: str,
        context: dict[str, Any] | None = None,
        query_params: tuple[str, ...] = (),
        path_params: tuple[str, ...] = (),
    ) -> Any:
        """Shared HTTP execution flow for connector actions and transforms."""
        interpolated_endpoint = self._interpolate_endpoint(
            endpoint, inputs, variables, config, context
        )
        full_url, headers = self._resolve_target_url_and_headers(
            connector_id, op_id, interpolated_endpoint, variables, base_url
        )

        body = self._build_request_body(
            endpoint,
            method,
            request_body_template,
            config_schema,
            config,
            inputs,
            variables,
            context=context,
            query_params=query_params,
            path_params=path_params,
        )
        params = self._build_query_params(
            endpoint,
            method,
            config_schema,
            config,
            inputs,
            variables,
            context=context,
            query_params=query_params,
            path_params=path_params,
        )

        logger.info(
            f"Executing connector {kind} {connector_id}.{op_id}: {method} {full_url}"
        )

        try:
            async with httpx.AsyncClient(
                timeout=30.0, follow_redirects=False
            ) as client:
                req_kwargs: dict[str, Any] = {
                    "method": method,
                    "url": full_url,
                }
                if headers:
                    req_kwargs["headers"] = headers
                if params:
                    req_kwargs["params"] = params
                if body is not None:
                    req_kwargs["json"] = body
                response = await client.request(**req_kwargs)
                response.raise_for_status()
                return self._parse_response(response)
        except httpx.HTTPStatusError as e:
            logger.error(f"Connector {kind} {connector_id}.{op_id} failed: {e}")
            raise ConnectorExecutionError(
                f"{kind.title()} '{op_id}' failed: HTTP {e.response.status_code}"
            ) from e
        except ConnectorExecutionError:
            raise
        except Exception as e:
            logger.error(f"Connector {kind} {connector_id}.{op_id} failed: {e}")
            raise ConnectorExecutionError(
                f"{kind.title()} '{op_id}' failed: {str(e)}"
            ) from e

    async def execute_action(
        self,
        connector_id: str,
        action_id: str,
        endpoint: str,
        method: str,
        request_body_template: str,
        config_schema: tuple,
        config: dict[str, Any],
        inputs: dict[str, Any],
        variables: dict[str, Any],
        base_url: str,
        *,
        context: dict[str, Any] | None = None,
        query_params: tuple[str, ...] = (),
        path_params: tuple[str, ...] = (),
    ) -> Any:
        """Execute a connector action with SSRF guarding or trusted Elder routing."""
        return await self._execute_http(
            kind="action",
            connector_id=connector_id,
            op_id=action_id,
            endpoint=endpoint,
            method=method,
            request_body_template=request_body_template,
            config_schema=config_schema,
            config=config,
            inputs=inputs,
            variables=variables,
            base_url=base_url,
            context=context,
            query_params=query_params,
            path_params=path_params,
        )

    async def execute_transform(
        self,
        connector_id: str,
        transform_id: str,
        endpoint: str,
        method: str,
        config_schema: tuple,
        config: dict[str, Any],
        inputs: dict[str, Any],
        variables: dict[str, Any],
        base_url: str,
        *,
        context: dict[str, Any] | None = None,
        query_params: tuple[str, ...] = (),
        path_params: tuple[str, ...] = (),
    ) -> Any:
        """Execute a connector transform with SSRF guarding or trusted Elder routing."""
        if not endpoint:
            return inputs.get("in", {})
        return await self._execute_http(
            kind="transform",
            connector_id=connector_id,
            op_id=transform_id,
            endpoint=endpoint,
            method=method,
            request_body_template="",
            config_schema=config_schema,
            config=config,
            inputs=inputs,
            variables=variables,
            base_url=base_url,
            context=context,
            query_params=query_params,
            path_params=path_params,
        )
