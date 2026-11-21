"""Lightweight Quart HTTP/ASGI service for Elder MCP (``hub-mcp``).

Translates MCP Streamable HTTP JSON-RPC requests thinly into authenticated
``hub-api`` calls using strictly per-caller credentials (zero DB, zero Redis).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from quart import Quart, Response, g, jsonify, request
from quart_cors import cors
from werkzeug.exceptions import RequestEntityTooLarge

# Ensure `apps.mcp` and `shared` resolve even when executed from `apps/mcp/`.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.logging import configure_logging_from_env  # noqa: E402
from shared.observability import (  # noqa: E402
    auto_instrument_app,
    get_meter,
    get_tracer,
    init_telemetry,
    setup_structlog_otel_bridge,
)

if TYPE_CHECKING:
    from apps.mcp.auth import (
        ElderAuthError,
        ElderSession,
        extract_caller_credentials,
        reset_caller_context,
        resolve_elder_base_url,
        set_caller_context,
    )
    from apps.mcp.catalog import list_services, load_catalog
    from apps.mcp.client import (
        ElderAPIError,
        ElderClient,
        ElderResponseTooLargeError,
        create_pooled_http_client,
        mask_secrets,
        public_error_message,
    )
    from apps.mcp.envutil import env_float, env_int
    from apps.mcp.limits import BodyReadLimiter, ConcurrencyLimiter, client_ip
    from apps.mcp.registry import TOOL_BY_NAME, TOOL_REGISTRY, execute_tool
else:
    try:
        from apps.mcp.auth import (
            ElderAuthError,
            ElderSession,
            extract_caller_credentials,
            reset_caller_context,
            resolve_elder_base_url,
            set_caller_context,
        )
        from apps.mcp.catalog import list_services, load_catalog
        from apps.mcp.client import (
            ElderAPIError,
            ElderClient,
            ElderResponseTooLargeError,
            create_pooled_http_client,
            mask_secrets,
            public_error_message,
        )
        from apps.mcp.envutil import env_float, env_int
        from apps.mcp.limits import BodyReadLimiter, ConcurrencyLimiter, client_ip
        from apps.mcp.registry import TOOL_BY_NAME, TOOL_REGISTRY, execute_tool
    except ImportError:
        from auth import (
            ElderAuthError,
            ElderSession,
            extract_caller_credentials,
            reset_caller_context,
            resolve_elder_base_url,
            set_caller_context,
        )
        from catalog import list_services, load_catalog
        from client import (
            ElderAPIError,
            ElderClient,
            ElderResponseTooLargeError,
            create_pooled_http_client,
            mask_secrets,
            public_error_message,
        )
        from envutil import env_float, env_int
        from limits import BodyReadLimiter, ConcurrencyLimiter, client_ip
        from registry import TOOL_BY_NAME, TOOL_REGISTRY, execute_tool

SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = (
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)
DEFAULT_PROTOCOL_VERSION = "2025-03-26"

_AUTH_EXEMPT_PATHS = frozenset({"/healthz", "/readyz"})
# 1 MiB: a 5-call tools/call batch or catalog invoke body is a few KiB, so >100x headroom.
DEFAULT_MAX_REQUEST_BYTES = 1024 * 1024
MAX_REQUEST_BYTES = env_int("MCP_MAX_REQUEST_BYTES", DEFAULT_MAX_REQUEST_BYTES)
MAX_BATCH_SIZE = env_int("MCP_MAX_BATCH_SIZE", 50)
_env_int = env_int
_env_float = env_float


def _resolve_app_version() -> str:
    env_ver = (os.getenv("APP_VERSION") or "").strip()
    if env_ver and env_ver not in ("0.0.0", "0.1.0"):
        return env_ver
    for candidate in (Path("/app/.version"), _REPO_ROOT / ".version"):
        try:
            text = candidate.read_text(encoding="utf-8").strip()
            if text:
                return text
        except OSError:
            continue
    return env_ver or "0.0.0"


def _cors_origins() -> list[str]:
    raw = (os.getenv("MCP_CORS_ORIGINS") or os.getenv("CORS_ORIGINS") or "").strip()
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8080",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8080",
    ]


def _attach_otel_log_handler(logger_provider: Any) -> logging.Logger:
    otel_py_logger = logging.getLogger("hub-mcp.otel")
    otel_py_logger.setLevel(logging.INFO)
    if logger_provider is None:
        return otel_py_logger
    try:
        from opentelemetry.sdk._logs import LoggingHandler

        already = any(
            isinstance(h, LoggingHandler)
            and getattr(h, "_logger_provider", None) is logger_provider
            for h in otel_py_logger.handlers
        )
        if not already:
            otel_py_logger.handlers = [
                h for h in otel_py_logger.handlers if not isinstance(h, LoggingHandler)
            ]
            otel_py_logger.addHandler(
                LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
            )
    except Exception:
        pass
    return otel_py_logger


def _negotiate_protocol_version(requested: Any = None) -> str:
    if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    header_ver = request.headers.get("MCP-Protocol-Version")
    if isinstance(header_ver, str) and header_ver in SUPPORTED_PROTOCOL_VERSIONS:
        return header_ver
    return DEFAULT_PROTOCOL_VERSION


def _jsonrpc_result(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(
    req_id: Any,
    code: int,
    message: str,
    data: Any = None,
) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def _tool_annotations(tool: Any) -> dict[str, bool]:
    """Build MCP tool annotations; destructive tools declare ``destructiveHint`` true."""
    annotations: dict[str, bool] = {"readOnlyHint": bool(tool.read_only)}
    if getattr(tool, "destructive", False):
        annotations["destructiveHint"] = True
        annotations["readOnlyHint"] = False
    return annotations


def _mcp_tool_error_result(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, default=str)}],
        "isError": True,
    }


def _mcp_tool_success_result(result: Any) -> dict[str, Any]:
    text = json.dumps(result, default=str)
    out: dict[str, Any] = {
        "content": [{"type": "text", "text": text}],
        "isError": False,
    }
    if isinstance(result, dict):
        out["structuredContent"] = result
    return out


def create_app(
    elder_api_url: str | None = None,
    elder_transport: httpx.AsyncBaseTransport | None = None,
) -> Quart:
    """Create and configure the ``hub-mcp`` Quart ASGI application."""
    configure_logging_from_env("hub-mcp")
    log = structlog.get_logger("hub-mcp")

    service_name = os.getenv("OTEL_SERVICE_NAME", "hub-mcp").strip() or "hub-mcp"
    otel = init_telemetry(service_name)
    setup_structlog_otel_bridge(otel.get("logger_provider"))
    otel_logger = _attach_otel_log_handler(otel.get("logger_provider"))

    meter = get_meter("hub-mcp")
    tracer = get_tracer("hub-mcp")
    req_counter = meter.create_counter(
        name="mcp.requests.total",
        description="Total HTTP requests served by hub-mcp",
    )
    tool_counter = meter.create_counter(
        name="mcp.tool_calls.total",
        description="Total MCP tool invocations executed by hub-mcp",
    )
    req_latency = meter.create_histogram(
        name="mcp.request.duration_ms",
        unit="ms",
        description="HTTP request latency for hub-mcp",
    )
    tool_latency = meter.create_histogram(
        name="mcp.tool.duration_ms",
        unit="ms",
        description="MCP tool execution latency for hub-mcp",
    )

    app = Quart(__name__)
    app.config["APP_VERSION"] = _resolve_app_version()
    app.config["MAX_CONTENT_LENGTH"] = _env_int(
        "MCP_MAX_REQUEST_BYTES", DEFAULT_MAX_REQUEST_BYTES
    )
    app.config["ELDER_API_URL"] = resolve_elder_base_url(elder_api_url)

    cors(
        app,
        allow_origin=_cors_origins(),
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Authorization",
            "X-API-Key",
            "Accept",
            "Origin",
            "MCP-Protocol-Version",
            "Mcp-Session-Id",
            "X-Request-ID",
            "traceparent",
            "tracestate",
        ],
        expose_headers=["Content-Type", "MCP-Protocol-Version", "Mcp-Session-Id"],
    )

    # Pre-load catalog so readiness and discovery calls stay zero-I/O per request.
    catalog_ops = load_catalog()
    app.extensions["mcp_otel"] = otel
    app.extensions["mcp_elder_transport"] = elder_transport
    app.extensions["mcp_http_client"] = None

    def _get_pooled_http_client() -> httpx.AsyncClient:
        existing: httpx.AsyncClient | None = app.extensions.get("mcp_http_client")
        if existing is None or existing.is_closed:
            existing = create_pooled_http_client(
                app.config["ELDER_API_URL"],
                transport=app.extensions.get("mcp_elder_transport"),
            )
            app.extensions["mcp_http_client"] = existing
        return existing

    @app.before_serving
    async def _startup() -> None:
        _get_pooled_http_client()
        log.info(
            "hub_mcp_started",
            service=service_name,
            version=app.config["APP_VERSION"],
            elder_api_url=app.config["ELDER_API_URL"],
            tool_count=len(TOOL_REGISTRY),
            catalog_operations=len(catalog_ops),
        )
        otel_logger.info(
            "hub_mcp_started version=%s tools=%d catalog_ops=%d",
            app.config["APP_VERSION"],
            len(TOOL_REGISTRY),
            len(catalog_ops),
        )

    @app.after_serving
    async def _shutdown() -> None:
        client: httpx.AsyncClient | None = app.extensions.get("mcp_http_client")
        if client is not None and not client.is_closed:
            await client.aclose()
        app.extensions["mcp_http_client"] = None
        log.info("hub_mcp_stopped", service=service_name)

    @app.before_request
    async def _authenticate_caller() -> tuple[Response, int] | None:
        g._mcp_started_at = time.perf_counter()
        if request.path in _AUTH_EXEMPT_PATHS or request.method == "OPTIONS":
            return None

        try:
            creds = extract_caller_credentials(request.headers)
        except ElderAuthError:
            log.warning(
                "mcp_unauthenticated_request_rejected",
                method=request.method,
                path=request.path,
            )
            otel_logger.warning(
                "mcp_unauthenticated_request_rejected method=%s path=%s",
                request.method,
                request.path,
            )
            return jsonify({"error": "Authentication required"}), 401

        g.caller_credentials = creds
        g._caller_ctx_tokens = set_caller_context(creds, hosted=True)
        session = ElderSession.from_caller(app.config["ELDER_API_URL"], creds)
        g.elder_client = ElderClient(
            session=session,
            http_client=_get_pooled_http_client(),
            caller_credentials=creds,
        )
        return None

    @app.after_request
    async def _record_request_metrics(response: Response) -> Response:
        started = getattr(g, "_mcp_started_at", None)
        elapsed_ms = (
            (time.perf_counter() - started) * 1000.0 if started is not None else 0.0
        )
        rule = getattr(request.url_rule, "rule", None)
        attrs = {
            "method": request.method,
            "path": rule if isinstance(rule, str) and rule else "unmatched",
            "status_code": response.status_code,
        }
        req_counter.add(1, attrs)
        req_latency.record(elapsed_ms, attrs)
        return response

    @app.teardown_request
    async def _cleanup_caller_context(_exc: BaseException | None) -> None:
        tokens = getattr(g, "_caller_ctx_tokens", None)
        if tokens is not None:
            reset_caller_context(tokens)
            g._caller_ctx_tokens = None

    @app.route("/healthz", methods=["GET"])
    async def healthz() -> tuple[Response, int]:
        """Liveness probe (auth-exempt); returns no version, tool or catalog details."""
        otel_logger.info("healthz_checked")
        return jsonify({"status": "ok"}), 200

    @app.route("/readyz", methods=["GET"])
    async def readyz() -> tuple[Response, int]:
        """Readiness probe (auth-exempt); returns only a boolean readiness flag."""
        return jsonify({"status": "ok", "ready": bool(catalog_ops)}), 200

    async def _handle_tool_call(
        params: Any,
        elder_client: ElderClient,
    ) -> dict[str, Any]:
        if not isinstance(params, dict):
            return {"_jsonrpc_error": (-32602, "Tool call params must be an object")}
        tool_name = params.get("name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            return {"_jsonrpc_error": (-32602, "Tool name must be a non-empty string")}
        tool_name = tool_name.strip()

        raw_args = params.get("arguments")
        if raw_args is None:
            arguments: dict[str, Any] = {}
        elif isinstance(raw_args, dict):
            arguments = raw_args
        else:
            return {"_jsonrpc_error": (-32602, "Tool arguments must be an object")}

        if tool_name not in TOOL_BY_NAME:
            return _mcp_tool_error_result({"error": f"Unknown MCP tool: {tool_name!r}"})

        t_start = time.perf_counter()
        status_label = "ok"
        with tracer.start_as_current_span(
            f"mcp.tool.{tool_name}",
            attributes={"mcp.tool.name": tool_name},
        ):
            try:
                result = await execute_tool(
                    tool_name,
                    elder_client,
                    arguments,
                    read_only_only=False,
                )
                otel_logger.info("mcp_tool_succeeded tool=%s", tool_name)
                return _mcp_tool_success_result(result)
            except ElderAPIError as exc:
                status_label = f"upstream_{exc.status_code}"
                log.warning(
                    "mcp_tool_upstream_error",
                    tool=tool_name,
                    status_code=exc.status_code,
                    message=mask_secrets(exc.message)[:300],
                )
                otel_logger.warning(
                    "mcp_tool_upstream_error tool=%s status=%d",
                    tool_name,
                    exc.status_code,
                )
                err_payload: dict[str, Any] = {
                    "error": (
                        exc.message
                        if isinstance(exc, ElderResponseTooLargeError)
                        else public_error_message(exc.status_code)
                    ),
                    "status_code": exc.status_code,
                }
                return _mcp_tool_error_result(err_payload)
            except (ValueError, PermissionError, ElderAuthError) as exc:
                status_label = "validation_error"
                log.warning(
                    "mcp_tool_validation_error",
                    tool=tool_name,
                    error=mask_secrets(str(exc))[:300],
                )
                return _mcp_tool_error_result({"error": str(exc)})
            except Exception:
                status_label = "internal_error"
                log.exception("mcp_tool_internal_error", tool=tool_name)
                return _mcp_tool_error_result(
                    {"error": "Internal tool execution error"}
                )
            finally:
                elapsed = (time.perf_counter() - t_start) * 1000.0
                tool_counter.add(1, {"tool": tool_name, "status": status_label})
                tool_latency.record(
                    elapsed, {"tool": tool_name, "status": status_label}
                )

    async def _dispatch_jsonrpc(
        payload: Any,
        elder_client: ElderClient,
    ) -> tuple[dict[str, Any] | None, str]:
        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
            return (
                _jsonrpc_error(None, -32600, "Invalid Request"),
                _negotiate_protocol_version(),
            )

        has_id = "id" in payload
        req_id = payload.get("id")
        method = payload.get("method")
        params = payload.get("params")

        if not isinstance(method, str) or not method.strip():
            return (
                _jsonrpc_error(req_id if has_id else None, -32600, "Invalid Request"),
                _negotiate_protocol_version(),
            )

        if method == "notifications/initialized" or method.startswith("notifications/"):
            if not has_id:
                return None, _negotiate_protocol_version()
            return _jsonrpc_result(req_id, {}), _negotiate_protocol_version()

        if not has_id:
            return None, _negotiate_protocol_version()

        if method == "initialize":
            req_proto = (
                params.get("protocolVersion") if isinstance(params, dict) else None
            )
            negotiated = _negotiate_protocol_version(req_proto)
            return (
                _jsonrpc_result(
                    req_id,
                    {
                        "protocolVersion": negotiated,
                        "capabilities": {
                            "tools": {"listChanged": False},
                            "resources": {"subscribe": False, "listChanged": False},
                            "prompts": {"listChanged": False},
                        },
                        "serverInfo": {
                            "name": "elder",
                            "version": app.config["APP_VERSION"],
                        },
                    },
                ),
                negotiated,
            )

        proto = _negotiate_protocol_version()
        if method == "ping":
            return _jsonrpc_result(req_id, {}), proto

        if method == "tools/list":
            tools_payload = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.parameters_schema,
                    "annotations": _tool_annotations(tool),
                }
                for tool in TOOL_REGISTRY
            ]
            return _jsonrpc_result(req_id, {"tools": tools_payload}), proto

        if method == "tools/call":
            outcome = await _handle_tool_call(params, elder_client)
            if "_jsonrpc_error" in outcome:
                code, msg = outcome["_jsonrpc_error"]
                return _jsonrpc_error(req_id, code, msg), proto
            return _jsonrpc_result(req_id, outcome), proto

        if method == "resources/list":
            return (
                _jsonrpc_result(
                    req_id,
                    {
                        "resources": [
                            {
                                "uri": "elder://api/catalog",
                                "name": "Elder API Catalog",
                                "description": (
                                    "Catalog of all non-internal hub-api REST services and operations"
                                ),
                                "mimeType": "application/json",
                            }
                        ]
                    },
                ),
                proto,
            )

        if method == "resources/read":
            uri = params.get("uri") if isinstance(params, dict) else None
            if uri == "elder://api/catalog":
                services = list_services(catalog_ops)
                return (
                    _jsonrpc_result(
                        req_id,
                        {
                            "contents": [
                                {
                                    "uri": uri,
                                    "mimeType": "application/json",
                                    "text": json.dumps(
                                        {
                                            "total_services": len(services),
                                            "total_operations": len(catalog_ops),
                                            "services": services,
                                        }
                                    ),
                                }
                            ]
                        },
                    ),
                    proto,
                )
            return _jsonrpc_error(req_id, -32002, f"Resource not found: {uri!r}"), proto

        if method == "prompts/list":
            return _jsonrpc_result(req_id, {"prompts": []}), proto

        return _jsonrpc_error(req_id, -32601, f"Method not found: {method}"), proto

    limiter = ConcurrencyLimiter()
    app.extensions["mcp_limiter"] = limiter
    body_limiter = BodyReadLimiter()
    app.extensions["mcp_body_limiter"] = body_limiter

    def _protocol_error(code: int, message: str, status: int) -> tuple[Response, int]:
        """Build a JSON-RPC error response carrying the negotiated protocol header."""
        resp = jsonify(_jsonrpc_error(None, code, message))
        resp.headers["MCP-Protocol-Version"] = _negotiate_protocol_version()
        return resp, status

    @app.route("/mcp", methods=["POST"])
    async def mcp_post() -> Response | tuple[Response, int]:
        """Handle single and batch MCP Streamable HTTP JSON-RPC 2.0 requests.

        Enforces a per-request deadline, a per-batch tools/call cap and a
        per-caller in-flight concurrency limit so one caller cannot exhaust
        hub-mcp or hub-api.
        """
        caller_key = g.caller_credentials.credential_fingerprint()
        ip_key = client_ip(request.remote_addr, request.headers)
        # Reject an oversized declared length before buffering anything.
        max_bytes = int(app.config["MAX_CONTENT_LENGTH"])
        declared = request.content_length
        if declared is not None and declared > max_bytes:
            return _protocol_error(-32600, "Request body too large", 413)
        # Bound the body-read phase (IP-keyed: credentials are not validated yet).
        body_refusal = body_limiter.try_acquire(
            ip_key,
            max_total=_env_int("MCP_MAX_CONCURRENT_BODY_READS", 128),
            max_per_ip=_env_int("MCP_MAX_BODY_READS_PER_IP", 8),
        )
        if body_refusal is not None:
            log.warning(
                "mcp_body_read_limit_exceeded",
                scope=body_refusal,
                in_flight=body_limiter.total,
            )
            resp, status = _protocol_error(
                -32000, "Too many concurrent body reads", 429
            )
            resp.headers["Retry-After"] = "1"
            return resp, status
        # Read the body BEFORE taking a slot so slow-body clients hold none.
        body_timeout_s = _env_float("MCP_BODY_READ_TIMEOUT_S", 10.0)
        try:
            try:
                async with asyncio.timeout(body_timeout_s):
                    raw_body = await request.get_data()
            except TimeoutError:
                log.warning("mcp_body_read_timeout", timeout_s=body_timeout_s)
                return _protocol_error(-32000, "Request body read timed out", 408)
            except RequestEntityTooLarge:
                return _protocol_error(-32600, "Request body too large", 413)
        finally:
            body_limiter.release(ip_key)
        refusal = limiter.try_acquire(
            caller_key,
            ip_key,
            max_total=_env_int("MCP_MAX_CONCURRENT_TOTAL", 64),
            max_per_caller=_env_int("MCP_MAX_CONCURRENT_PER_CALLER", 4),
            max_per_ip=_env_int("MCP_MAX_CONCURRENT_PER_IP", 16),
            max_tracked_callers=_env_int("MCP_MAX_TRACKED_CALLERS", 10000),
        )
        if refusal is not None:
            log.warning(
                "mcp_concurrency_exceeded",
                scope=refusal,
                caller=caller_key[:8],
                total_in_flight=limiter.total,
            )
            resp, status = _protocol_error(-32000, "Too many concurrent requests", 429)
            resp.headers["Retry-After"] = "1"
            return resp, status

        try:
            timeout_s = _env_float("MCP_REQUEST_TIMEOUT_S", 60.0)
            try:
                async with asyncio.timeout(timeout_s):
                    return await _process_mcp_post(raw_body)
            except TimeoutError:
                log.warning("mcp_request_timeout", timeout_s=timeout_s)
                return _protocol_error(-32000, "Request timed out", 504)
        finally:
            limiter.release(caller_key, ip_key)

    async def _process_mcp_post(raw_body: bytes) -> Response | tuple[Response, int]:
        """Parse and dispatch one single or batch JSON-RPC payload (body already read)."""
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _protocol_error(-32700, "Parse error", 400)

        elder_client: ElderClient = g.elder_client

        if isinstance(payload, list):
            if not payload or len(payload) > MAX_BATCH_SIZE:
                return _protocol_error(-32600, "Invalid Request", 400)
            max_calls = _env_int("MCP_MAX_TOOL_CALLS_PER_BATCH", 5)
            call_count = sum(
                1
                for item in payload
                if isinstance(item, dict) and item.get("method") == "tools/call"
            )
            if call_count > max_calls:
                return _protocol_error(
                    -32600,
                    f"Too many tools/call entries in batch (max {max_calls})",
                    400,
                )
            responses: list[dict[str, Any]] = []
            negotiated_proto = _negotiate_protocol_version()
            for item in payload:
                reply, item_proto = await _dispatch_jsonrpc(item, elder_client)
                negotiated_proto = item_proto
                if reply is not None:
                    responses.append(reply)
            if not responses:
                empty = Response(b"", status=202)
                empty.headers["MCP-Protocol-Version"] = negotiated_proto
                return empty
            resp = jsonify(responses)
            resp.headers["MCP-Protocol-Version"] = negotiated_proto
            return resp, 200

        if not isinstance(payload, dict):
            return _protocol_error(-32600, "Invalid Request", 400)

        reply, negotiated_proto = await _dispatch_jsonrpc(payload, elder_client)
        if reply is None:
            empty = Response(b"", status=202)
            empty.headers["MCP-Protocol-Version"] = negotiated_proto
            return empty
        resp = jsonify(reply)
        resp.headers["MCP-Protocol-Version"] = negotiated_proto
        return resp, 200

    @app.route("/mcp", methods=["GET"])
    async def mcp_get() -> tuple[Response, int]:
        """MCP Streamable HTTP GET endpoint (stateless server returns 405 per spec)."""
        resp = jsonify({"error": "Method Not Allowed"})
        resp.headers["Allow"] = "POST, DELETE"
        resp.headers["MCP-Protocol-Version"] = _negotiate_protocol_version()
        return resp, 405

    @app.route("/mcp", methods=["DELETE"])
    async def mcp_delete() -> Response:
        """MCP Streamable HTTP session termination endpoint (stateless no-op)."""
        resp = Response(b"", status=204)
        resp.headers["MCP-Protocol-Version"] = _negotiate_protocol_version()
        return resp

    auto_instrument_app(app)
    return app
