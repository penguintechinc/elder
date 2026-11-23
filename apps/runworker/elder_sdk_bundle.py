"""Built-in zero-dependency Elder Helper SDKs for CodeRun workspaces.

Provides pure-stdlib helper modules mounted automatically into every CodeRun
workspace (both warm ``SandboxRunner`` and ``K8sRunner`` pods):

- ``elder.py`` (Python 3 stdlib: ``urllib.request`` + ``json``)
- ``elder.mjs`` (Node.js 18+/22 native ``fetch``)
- ``elder.rb`` (Ruby stdlib: ``net/http`` + ``json``)

Each SDK reads ``ELDER_GATEWAY_URL`` (falling back to ``ELDER_API_URL``) and
``ELDER_GATEWAY_TOKEN`` (falling back to ``ELDER_API_TOKEN``) so untrusted
CodeRun scripts talk only to the per-execution local ``RestrictionGateway``
and never hold raw Elder API credentials.
"""

from __future__ import annotations

from pathlib import Path

from apps.runworker.runtimes import RUNTIME_ALIASES

ELDER_PY_SDK: str = '''"""Built-in zero-dependency Elder API helper SDK for Python CodeRuns."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_DEFAULT_TIMEOUT = 30.0


def _resolve_config() -> tuple[str, str]:
    """Return ``(base_url, token)`` from gateway or fallback API env vars."""
    base_url = (
        os.environ.get("ELDER_GATEWAY_URL")
        or os.environ.get("ELDER_API_URL")
        or ""
    ).strip().rstrip("/")
    token = (
        os.environ.get("ELDER_GATEWAY_TOKEN")
        or os.environ.get("ELDER_API_TOKEN")
        or ""
    ).strip()
    if not base_url or not token:
        raise PermissionError(
            "Elder API access was not granted for this CodeRun execution "
            "(missing ELDER_GATEWAY_URL/ELDER_GATEWAY_TOKEN)."
        )
    return base_url, token


def _build_url(base_url: str, path: str, params: dict[str, Any] | None = None) -> str:
    """Safely join a relative API path onto ``base_url``."""
    raw_path = (path or "").strip()
    if not raw_path:
        raise ValueError("path must be a non-empty Elder API path")
    if raw_path.startswith(("http://", "https://")):
        raise ValueError("path must be relative (e.g. '/api/v1/entities'), not a full URL")
    if not raw_path.startswith("/"):
        raw_path = f"/{raw_path}"
    url = f"{base_url}{raw_path}"
    if params:
        query = urllib.parse.urlencode(params, doseq=True)
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{query}"
    return url


def _request(
    method: str,
    path: str,
    body: Any = None,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> Any:
    """Send an authenticated request through the Elder restriction gateway."""
    base_url, token = _resolve_config()
    url = _build_url(base_url, path, params=params)
    req_headers: dict[str, str] = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    if headers:
        for key, value in headers.items():
            if key.lower() != "authorization":
                req_headers[key] = value

    data: bytes | None = None
    if body is not None:
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body)
        else:
            data = json.dumps(body).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(
        url=url,
        data=data,
        headers=req_headers,
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            raw = resp.read()
            if not raw:
                return None
            content_type = (resp.headers.get("Content-Type") or "").lower()
            text = raw.decode("utf-8", errors="replace")
            if "json" in content_type:
                return json.loads(text)
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace").strip()
        except Exception:
            detail = str(exc.reason or "")
        if exc.code in (401, 403):
            raise PermissionError(
                f"Elder API access denied ({exc.code}) for {method.upper()} {path}: {detail}"
            ) from None
        raise RuntimeError(
            f"Elder API request failed ({exc.code}) for {method.upper()} {path}: {detail}"
        ) from None
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Elder API gateway unreachable for {method.upper()} {path}: {exc.reason}"
        ) from None


def get(
    path: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """Perform an authenticated GET request against the Elder API."""
    return _request("GET", path, params=params, headers=headers)


def post(
    path: str,
    body: Any = None,
    *,
    headers: dict[str, str] | None = None,
) -> Any:
    """Perform an authenticated POST request against the Elder API."""
    return _request("POST", path, body=body, headers=headers)


def put(
    path: str,
    body: Any = None,
    *,
    headers: dict[str, str] | None = None,
) -> Any:
    """Perform an authenticated PUT request against the Elder API."""
    return _request("PUT", path, body=body, headers=headers)


def patch(
    path: str,
    body: Any = None,
    *,
    headers: dict[str, str] | None = None,
) -> Any:
    """Perform an authenticated PATCH request against the Elder API."""
    return _request("PATCH", path, body=body, headers=headers)


def delete(
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> Any:
    """Perform an authenticated DELETE request against the Elder API."""
    return _request("DELETE", path, headers=headers)


def update_entity_metadata(entity_id: str, metadata: dict[str, Any]) -> Any:
    """Patch metadata on an Elder entity by its public UUID."""
    safe_id = urllib.parse.quote(str(entity_id), safe="")
    return patch(f"/api/v1/entities/{safe_id}/metadata", {"metadata": metadata})


def trigger_integration_sync(
    integration_id: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    """Trigger a sync on an Elder integration by its public UUID."""
    safe_id = urllib.parse.quote(str(integration_id), safe="")
    return post(
        f"/api/v1/integrations/{safe_id}/sync",
        payload if payload is not None else {},
    )


def push_integration(integration_id: str, payload: dict[str, Any]) -> Any:
    """Push data to an Elder integration by its public UUID."""
    safe_id = urllib.parse.quote(str(integration_id), safe="")
    return post(f"/api/v1/integrations/{safe_id}/push", payload)


def trigger_webhook(webhook_id: str, payload: dict[str, Any]) -> Any:
    """Trigger an Elder webhook by its public UUID."""
    safe_id = urllib.parse.quote(str(webhook_id), safe="")
    return post(f"/api/v1/webhooks/{safe_id}/trigger", payload)
'''

ELDER_MJS_SDK: str = """// Built-in zero-dependency Elder API helper SDK for Node.js CodeRuns.

function resolveConfig() {
  const baseUrl = (
    process.env.ELDER_GATEWAY_URL ||
    process.env.ELDER_API_URL ||
    ""
  ).trim().replace(/\\/+$/, "");
  const token = (
    process.env.ELDER_GATEWAY_TOKEN ||
    process.env.ELDER_API_TOKEN ||
    ""
  ).trim();
  if (!baseUrl || !token) {
    const err = new Error(
      "PermissionError: Elder API access was not granted for this CodeRun execution " +
        "(missing ELDER_GATEWAY_URL/ELDER_GATEWAY_TOKEN)."
    );
    err.name = "PermissionError";
    throw err;
  }
  return { baseUrl, token };
}

function buildUrl(baseUrl, path) {
  const raw = String(path || "").trim();
  if (!raw) {
    throw new Error("path must be a non-empty Elder API path");
  }
  if (raw.startsWith("http://") || raw.startsWith("https://")) {
    throw new Error("path must be relative (e.g. '/api/v1/entities'), not a full URL");
  }
  const normalized = raw.startsWith("/") ? raw : `/${raw}`;
  return `${baseUrl}${normalized}`;
}

async function request(method, path, body = undefined, headers = {}) {
  const { baseUrl, token } = resolveConfig();
  const url = buildUrl(baseUrl, path);
  const reqHeaders = {
    Accept: "application/json",
    Authorization: `Bearer ${token}`,
  };
  for (const [key, value] of Object.entries(headers || {})) {
    if (key.toLowerCase() !== "authorization") {
      reqHeaders[key] = value;
    }
  }

  const init = { method: method.toUpperCase(), headers: reqHeaders };
  if (body !== undefined && body !== null) {
    init.body = typeof body === "string" ? body : JSON.stringify(body);
    if (!reqHeaders["Content-Type"] && !reqHeaders["content-type"]) {
      reqHeaders["Content-Type"] = "application/json";
    }
  }

  const resp = await fetch(url, init);
  const text = await resp.text();
  if (!resp.ok) {
    if (resp.status === 401 || resp.status === 403) {
      const err = new Error(
        `PermissionError: Elder API access denied (${resp.status}) for ${method.toUpperCase()} ${path}: ${text}`
      );
      err.name = "PermissionError";
      throw err;
    }
    throw new Error(
      `Elder API request failed (${resp.status}) for ${method.toUpperCase()} ${path}: ${text}`
    );
  }
  if (!text) {
    return null;
  }
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export async function get(path, headers = {}) {
  return request("GET", path, undefined, headers);
}

export async function post(path, body = null, headers = {}) {
  return request("POST", path, body, headers);
}

export async function put(path, body = null, headers = {}) {
  return request("PUT", path, body, headers);
}

export async function patch(path, body = null, headers = {}) {
  return request("PATCH", path, body, headers);
}

export async function del(path, headers = {}) {
  return request("DELETE", path, undefined, headers);
}

export { del as delete };

export async function update_entity_metadata(entityId, metadata) {
  const safeId = encodeURIComponent(String(entityId));
  return patch(`/api/v1/entities/${safeId}/metadata`, { metadata });
}

export async function trigger_integration_sync(integrationId, payload = null) {
  const safeId = encodeURIComponent(String(integrationId));
  return post(`/api/v1/integrations/${safeId}/sync`, payload ?? {});
}

export async function push_integration(integrationId, payload) {
  const safeId = encodeURIComponent(String(integrationId));
  return post(`/api/v1/integrations/${safeId}/push`, payload);
}

export async function trigger_webhook(webhookId, payload) {
  const safeId = encodeURIComponent(String(webhookId));
  return post(`/api/v1/webhooks/${safeId}/trigger`, payload);
}

export default {
  get,
  post,
  put,
  patch,
  delete: del,
  update_entity_metadata,
  trigger_integration_sync,
  push_integration,
  trigger_webhook,
};
"""

ELDER_RB_SDK: str = """# frozen_string_literal: true

# Built-in zero-dependency Elder API helper SDK for Ruby CodeRuns.

require "json"
require "net/http"
require "uri"

module Elder
  class PermissionError < RuntimeError; end

  class << self
    def get(path, headers: {})
      request("GET", path, nil, headers: headers)
    end

    def post(path, body = nil, headers: {})
      request("POST", path, body, headers: headers)
    end

    def put(path, body = nil, headers: {})
      request("PUT", path, body, headers: headers)
    end

    def patch(path, body = nil, headers: {})
      request("PATCH", path, body, headers: headers)
    end

    def delete(path, headers: {})
      request("DELETE", path, nil, headers: headers)
    end

    def update_entity_metadata(entity_id, metadata)
      safe_id = URI.encode_www_form_component(entity_id.to_s)
      patch("/api/v1/entities/#{safe_id}/metadata", { metadata: metadata })
    end

    def trigger_integration_sync(integration_id, payload = nil)
      safe_id = URI.encode_www_form_component(integration_id.to_s)
      post("/api/v1/integrations/#{safe_id}/sync", payload.nil? ? {} : payload)
    end

    def push_integration(integration_id, payload)
      safe_id = URI.encode_www_form_component(integration_id.to_s)
      post("/api/v1/integrations/#{safe_id}/push", payload)
    end

    def trigger_webhook(webhook_id, payload)
      safe_id = URI.encode_www_form_component(webhook_id.to_s)
      post("/api/v1/webhooks/#{safe_id}/trigger", payload)
    end

    private

    def resolve_config
      base_url = (ENV["ELDER_GATEWAY_URL"] || ENV["ELDER_API_URL"] || "").strip.sub(%r{/+$}, "")
      token = (ENV["ELDER_GATEWAY_TOKEN"] || ENV["ELDER_API_TOKEN"] || "").strip
      if base_url.empty? || token.empty?
        raise PermissionError,
              "Elder API access was not granted for this CodeRun execution " \\
              "(missing ELDER_GATEWAY_URL/ELDER_GATEWAY_TOKEN)."
      end
      [base_url, token]
    end

    def build_uri(base_url, path)
      raw = path.to_s.strip
      raise ArgumentError, "path must be a non-empty Elder API path" if raw.empty?
      if raw.start_with?("http://", "https://")
        raise ArgumentError, "path must be relative (e.g. '/api/v1/entities'), not a full URL"
      end

      normalized = raw.start_with?("/") ? raw : "/#{raw}"
      URI.parse("#{base_url}#{normalized}")
    end

    def request(method, path, body, headers: {})
      base_url, token = resolve_config
      uri = build_uri(base_url, path)

      req_class = {
        "GET" => Net::HTTP::Get,
        "POST" => Net::HTTP::Post,
        "PUT" => Net::HTTP::Put,
        "PATCH" => Net::HTTP::Patch,
        "DELETE" => Net::HTTP::Delete,
      }.fetch(method.upcase)

      req = req_class.new(uri.request_uri)
      req["Accept"] = "application/json"
      req["Authorization"] = "Bearer #{token}"
      (headers || {}).each do |key, value|
        req[key] = value unless key.to_s.downcase == "authorization"
      end

      unless body.nil?
        req.body = body.is_a?(String) ? body : JSON.generate(body)
        req["Content-Type"] ||= "application/json"
      end

      http = Net::HTTP.new(uri.host, uri.port)
      http.use_ssl = uri.scheme == "https"
      http.open_timeout = 10
      http.read_timeout = 30
      resp = http.request(req)
      code = resp.code.to_i

      if [401, 403].include?(code)
        raise PermissionError,
              "Elder API access denied (#{code}) for #{method.upcase} #{path}: #{resp.body}"
      end
      if code < 200 || code >= 300
        raise RuntimeError,
              "Elder API request failed (#{code}) for #{method.upcase} #{path}: #{resp.body}"
      end

      return nil if resp.body.nil? || resp.body.empty?

      JSON.parse(resp.body)
    rescue JSON::ParserError
      resp.body
    end
  end
end
"""

_SDK_BY_RUNTIME: dict[str, tuple[str, str]] = {
    "python": ("elder.py", ELDER_PY_SDK),
    "nodejs": ("elder.mjs", ELDER_MJS_SDK),
    "ruby": ("elder.rb", ELDER_RB_SDK),
}


def sdk_file_for_runtime(runtime: str) -> tuple[str, str] | None:
    """Return ``(filename, source)`` for the built-in SDK of ``runtime``, if any."""
    normalized = (runtime or "").strip().lower()
    canonical = RUNTIME_ALIASES.get(normalized, normalized)
    return _SDK_BY_RUNTIME.get(canonical)


def write_sdk_files(workspace_dir: Path, runtime: str) -> Path | None:
    """Write the built-in Elder helper SDK into ``workspace_dir`` for ``runtime``."""
    entry = sdk_file_for_runtime(runtime)
    if entry is None:
        return None
    filename, source = entry
    target = workspace_dir / filename
    target.write_text(source, encoding="utf-8")
    return target
