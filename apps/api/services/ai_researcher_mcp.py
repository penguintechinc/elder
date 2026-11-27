"""MCP client used by the AI Researcher to reach ``hub-mcp`` (read-only tools only).

The researcher speaks the MCP Streamable HTTP JSON-RPC protocol to ``hub-mcp`` so it
shares the exact auth, limits and tool code path with every other MCP client. By
default hub-mcp runs in-process behind an ``httpx.ASGITransport``; setting
``MCP_SERVER_URL`` switches to a remote hub-mcp. The caller's own bearer token is
forwarded so hub-api enforces the caller's scopes and tenant on every tool call.

A minimal JSON-RPC client is used instead of the ``mcp`` SDK client because hub-mcp is
a stateless, JSON-only endpoint and the SDK is not a hub-api dependency (it ships only
in ``apps/mcp/requirements.txt``).
"""

from __future__ import annotations

import ipaddress
import json
import logging
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2025-06-18"
MCP_PATH = "/mcp"
_EMBEDDED_APP_KEY = "ai_researcher_embedded_mcp_app"

# Tools that must never be offered to the researcher, regardless of annotations.
NEVER_EXPOSE_TOOLS = frozenset({"invoke_api_operation"})


class McpUnavailableError(Exception):
    """Raised when hub-mcp is unreachable, times out, or breaks the protocol."""


class McpConfigError(McpUnavailableError):
    """Raised when the hub-mcp transport configuration is unsafe or invalid (fail loud)."""


class McpToolError(Exception):
    """Raised when hub-mcp executes a tool and reports ``isError`` for that call."""


def is_readonly_mcp_tool(tool: Any) -> bool:
    """Return True only for MCP tools explicitly annotated read-only and non-destructive."""
    if not isinstance(tool, dict):
        return False
    name = tool.get("name")
    if not isinstance(name, str) or not name or name in NEVER_EXPOSE_TOOLS:
        return False
    annotations = tool.get("annotations")
    if not isinstance(annotations, dict):
        return False
    if annotations.get("readOnlyHint") is not True:
        return False
    return annotations.get("destructiveHint") is not True


def select_readonly_tools(
    mcp_tools: Iterable[Any], allowed_names: Iterable[str]
) -> list[dict[str, Any]]:
    """Intersect hub-mcp's advertised tools with the caller-permitted read-only names."""
    allowed = set(allowed_names)
    return [t for t in mcp_tools if is_readonly_mcp_tool(t) and t["name"] in allowed]


DEFAULT_MAX_TOOLS_PER_CALL = 12

_CORE_DISCOVERY_TOOLS: tuple[str, ...] = (
    "universal_search",
    "search_graph",
    "resolve_ref",
    "get_ref_backlinks",
)

_DOMAIN_SYNONYMS: dict[str, tuple[str, ...]] = {
    "server": ("entity", "entities", "infrastructure"),
    "servers": ("entity", "entities", "infrastructure"),
    "host": ("entity", "entities", "infrastructure"),
    "hosts": ("entity", "entities", "infrastructure"),
    "node": ("entity", "entities", "infrastructure"),
    "nodes": ("entity", "entities", "infrastructure"),
    "vm": ("entity", "entities", "infrastructure"),
    "vms": ("entity", "entities", "infrastructure"),
    "database": ("data", "store", "stores"),
    "db": ("data", "store", "stores"),
    "ticket": ("helpdesk", "tickets"),
    "tickets": ("helpdesk", "tickets"),
    "cve": ("vulnerability", "vulnerabilities", "sbom"),
    "vuln": ("vulnerability", "vulnerabilities"),
    "vulns": ("vulnerability", "vulnerabilities"),
    "security": ("vulnerability", "vulnerabilities", "access", "secrets"),
    "doc": ("document", "documents", "page", "pages"),
    "docs": ("document", "documents", "page", "pages"),
    "wiki": ("document", "documents", "page", "pages"),
    "user": ("identity", "identities"),
    "users": ("identity", "identities"),
    "team": ("organization", "organizations", "identity", "identities"),
    "org": ("organization", "organizations"),
    "ip": ("ipam", "address", "addresses", "prefix", "prefixes", "network"),
    "subnet": ("ipam", "prefix", "prefixes", "network"),
    "vlan": ("ipam", "vlan", "vlans", "network"),
    "cert": ("certificate", "certificates"),
    "tls": ("certificate", "certificates"),
    "ssl": ("certificate", "certificates"),
    "pipeline": ("flow", "flows"),
    "pipelines": ("flow", "flows"),
    "ci": ("flow", "flows"),
    "workflow": ("stream", "streams"),
    "workflows": ("stream", "streams"),
    "hook": ("webhook", "webhooks"),
    "alert": ("webhook", "webhooks", "oncall"),
    "key": ("kms", "secrets"),
    "keys": ("kms", "secrets"),
}


def _tokenize_text(text: str) -> set[str]:
    """Split text into lowercase alphanumeric tokens (length >= 2)."""
    tokens: set[str] = set()
    cur: list[str] = []
    for ch in text.lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            tok = "".join(cur)
            if len(tok) >= 2:
                tokens.add(tok)
            cur = []
    if cur:
        tok = "".join(cur)
        if len(tok) >= 2:
            tokens.add(tok)
    return tokens


def select_relevant_tools(
    tools: list[dict[str, Any]],
    query: str,
    *,
    max_tools: int = DEFAULT_MAX_TOOLS_PER_CALL,
) -> list[dict[str, Any]]:
    """Select up to *max_tools* most query-relevant tools to avoid LLM prompt bloat.

    Always preserves core discovery tools (``universal_search``, ``search_graph``,
    ``resolve_ref``, ``get_ref_backlinks``) when present, then ranks remaining
    tools by lexical overlap with *query* (including common domain synonyms).
    When ``len(tools) <= max_tools`` (or ``max_tools <= 0``), returns *tools* as-is.
    """
    if max_tools <= 0 or len(tools) <= max_tools:
        return list(tools)

    by_name = {t["name"]: t for t in tools if isinstance(t.get("name"), str)}
    selected: list[dict[str, Any]] = []
    selected_names: set[str] = set()

    for core_name in _CORE_DISCOVERY_TOOLS:
        if core_name in by_name and len(selected) < max_tools:
            selected.append(by_name[core_name])
            selected_names.add(core_name)

    query_tokens = _tokenize_text(query)
    expanded_tokens = set(query_tokens)
    for tok in query_tokens:
        expanded_tokens.update(_DOMAIN_SYNONYMS.get(tok, ()))

    scored: list[tuple[int, int, dict[str, Any]]] = []
    for idx, tool in enumerate(tools):
        name = str(tool.get("name") or "")
        if name in selected_names:
            continue
        name_tokens = _tokenize_text(name.replace("_", " "))
        desc_tokens = _tokenize_text(str(tool.get("description") or ""))
        score = 3 * len(expanded_tokens & name_tokens) + len(
            expanded_tokens & desc_tokens
        )
        # Prefer broad list_/search_ tools slightly over get_ when tied on score > 0
        if score > 0 and (name.startswith("list_") or name.startswith("search_")):
            score += 1
        scored.append((score, -idx, tool))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    remaining_slots = max_tools - len(selected)
    for _, _, tool in scored[:remaining_slots]:
        selected.append(tool)

    return selected


def _compact_description(raw: Any, max_len: int = 96) -> str:
    """Keep a concise single-sentence description to minimize LLM prompt tokens."""
    text = " ".join(str(raw or "").split())
    if not text:
        return ""
    first_sentence = text.split(". ", 1)[0].rstrip(".")
    if len(first_sentence) <= max_len:
        return first_sentence
    return first_sentence[: max_len - 1].rstrip() + "…"


def _compact_input_schema(schema: Any) -> dict[str, Any]:
    """Strip verbose JSON Schema prose while keeping types, enums, and required keys."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    raw_props = schema.get("properties")
    compact_props: dict[str, Any] = {}
    if isinstance(raw_props, dict):
        for prop_name, prop_def in raw_props.items():
            if not isinstance(prop_def, dict):
                compact_props[prop_name] = {"type": "string"}
                continue
            entry: dict[str, Any] = {}
            if "type" in prop_def:
                entry["type"] = prop_def["type"]
            if "enum" in prop_def and isinstance(prop_def["enum"], list):
                entry["enum"] = prop_def["enum"]
            if "items" in prop_def and isinstance(prop_def["items"], dict):
                entry["items"] = {"type": prop_def["items"].get("type", "string")}
            compact_props[prop_name] = entry or {"type": "string"}
    out: dict[str, Any] = {
        "type": schema.get("type", "object"),
        "properties": compact_props,
    }
    required = schema.get("required")
    if isinstance(required, list) and required:
        out["required"] = required
    return out


def to_ollama_tools_from_mcp(tools: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert MCP ``tools/list`` entries into compact Ollama ``/api/chat`` tool schemas."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": _compact_description(t.get("description")),
                "parameters": _compact_input_schema(t.get("inputSchema")),
            },
        }
        for t in tools
    ]


def _is_internal_host(host: str) -> bool:
    """Return True for loopback, cluster-internal (``*.svc``) or single-label hostnames."""
    host = host.strip().lower().rstrip(".")
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    if host == "localhost" or host.endswith((".svc", ".svc.cluster.local")):
        return True
    return "." not in host


def validate_remote_mcp_url(url: str) -> str:
    """Return *url* if it is https, or http to a loopback/cluster-internal host.

    Plain http to any other host would send the caller's bearer token in cleartext,
    so it is refused with a clear ``McpConfigError`` (no fallback to in-process).
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme == "https" and host:
        return url
    if parts.scheme == "http" and _is_internal_host(host):
        return url
    raise McpConfigError(
        "MCP_SERVER_URL must use https (http is allowed only for loopback or "
        "cluster-internal hosts)"
    )


async def aclose_embedded_mcp(app: Any) -> None:
    """Close the cached in-process hub-mcp app's pooled hub-api client (idempotent).

    Called from hub-api's shutdown hook; the embedded app is driven through an ASGI
    transport so it never receives its own lifespan ``after_serving`` event.
    """
    extensions = getattr(app, "extensions", None)
    cached = extensions.pop(_EMBEDDED_APP_KEY, None) if extensions is not None else None
    if cached is None:
        return
    mcp_app = cached[1]
    client = mcp_app.extensions.get("mcp_http_client")
    mcp_app.extensions["mcp_http_client"] = None
    if client is not None and not client.is_closed:
        await client.aclose()


def _embedded_mcp_app(app: Any, internal_api_url: str, elder_transport: Any) -> Any:
    """Return (and cache) the in-process hub-mcp ASGI app pointed at hub-api's URL."""
    key = (internal_api_url, id(elder_transport))
    cached = (
        app.extensions.get(_EMBEDDED_APP_KEY) if hasattr(app, "extensions") else None
    )
    if cached is not None and cached[0] == key:
        return cached[1]
    from apps.mcp.http_app import create_app as create_mcp_app

    mcp_app = create_mcp_app(
        elder_api_url=internal_api_url,
        elder_transport=elder_transport,
        embedded=True,
    )
    if hasattr(app, "extensions"):
        app.extensions[_EMBEDDED_APP_KEY] = (key, mcp_app)
    return mcp_app


class HubMcpClient:
    """Async MCP client (``initialize``, ``tools/list``, ``tools/call``) bound to one caller token."""

    def __init__(
        self,
        *,
        base_url: str,
        bearer_token: str,
        timeout: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Store connection settings; the HTTP client is opened by ``__aenter__``."""
        if not bearer_token or not bearer_token.strip():
            raise McpUnavailableError("Caller credential required to reach hub-mcp")
        self._base_url = base_url.rstrip("/")
        self._token = bearer_token.strip()
        self._timeout = timeout
        self._transport = transport
        self._http: httpx.AsyncClient | None = None
        self._next_id = 0

    async def __aenter__(self) -> HubMcpClient:
        """Open the HTTP client carrying the caller's bearer token."""
        kwargs: dict[str, Any] = {
            "base_url": self._base_url,
            "timeout": self._timeout,
            "headers": {
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
            },
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        self._http = httpx.AsyncClient(**kwargs)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        """Close the HTTP client."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """POST one JSON-RPC request and return its ``result`` (fail loudly otherwise)."""
        if self._http is None:
            raise McpUnavailableError("MCP client is not open")
        self._next_id += 1
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            body["params"] = params
        try:
            resp = await self._http.post(MCP_PATH, json=body)
        except httpx.HTTPError as exc:
            # Detail (may include the internal host) stays server-side only.
            logger.warning(
                "ai_researcher_mcp_unreachable",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:300]},
            )
            raise McpUnavailableError(
                f"hub-mcp unreachable ({type(exc).__name__})"
            ) from exc
        if resp.status_code in (401, 403):
            raise McpUnavailableError(
                f"hub-mcp rejected the caller credential (HTTP {resp.status_code})"
            )
        if resp.status_code >= 400:
            raise McpUnavailableError(f"hub-mcp returned HTTP {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise McpUnavailableError("hub-mcp returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise McpUnavailableError("hub-mcp returned an unexpected payload")
        if "error" in payload:
            err = payload["error"] if isinstance(payload["error"], dict) else {}
            raise McpUnavailableError(
                f"hub-mcp protocol error: {str(err.get('message') or 'unknown')[:200]}"
            )
        return payload.get("result")

    async def initialize(self) -> None:
        """Perform the MCP ``initialize`` handshake."""
        result = await self._rpc(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "elder-ai-researcher", "version": "1"},
            },
        )
        if not isinstance(result, dict):
            raise McpUnavailableError("hub-mcp initialize returned no result")

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return the tool descriptors advertised by ``tools/list``."""
        result = await self._rpc("tools/list", {})
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            raise McpUnavailableError("hub-mcp tools/list returned no tools array")
        return [t for t in tools if isinstance(t, dict)]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Execute ``tools/call`` and return structured/JSON content (raises on isError)."""
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        if not isinstance(result, dict):
            raise McpUnavailableError("hub-mcp tools/call returned no result")
        text = ""
        for part in result.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "text":
                text += str(part.get("text") or "")
        if result.get("isError"):
            message = text
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict) and parsed.get("error"):
                    message = str(parsed["error"])
            except ValueError:
                pass
            raise McpToolError(message[:500] or "tool reported an error")
        structured = result.get("structuredContent")
        if structured is not None:
            return structured
        try:
            return json.loads(text)
        except ValueError:
            return text


def open_hub_mcp_client(
    app: Any,
    *,
    bearer_token: str,
    internal_api_url: str,
    timeout: float,
    elder_transport: httpx.AsyncBaseTransport | None = None,
    mcp_transport: httpx.AsyncBaseTransport | None = None,
) -> HubMcpClient:
    """Build a client for remote hub-mcp (``MCP_SERVER_URL``) or the in-process default."""
    remote_url = str(app.config.get("MCP_SERVER_URL") or "").strip()
    if remote_url:
        validate_remote_mcp_url(remote_url)
        logger.info("ai_researcher_mcp_remote")
        return HubMcpClient(
            base_url=remote_url,
            bearer_token=bearer_token,
            timeout=timeout,
            transport=mcp_transport,
        )
    mcp_app = _embedded_mcp_app(app, internal_api_url, elder_transport)
    return HubMcpClient(
        base_url="http://hub-mcp.embedded",
        bearer_token=bearer_token,
        timeout=timeout,
        transport=httpx.ASGITransport(app=mcp_app),
    )
