# Elder MCP Server (`hub-mcp`)

An MCP (Model Context Protocol) server exposing Elder's curated operational tools plus 1:1 API-first parity with `hub-api` via authenticated REST proxy calls.

## Architecture

- **Hosted HTTP Service (`hub-mcp`)**: Lightweight Python 3.13 + Quart ASGI server (`apps/mcp/http_app.py`, `apps/mcp/start.py`) implementing MCP Streamable HTTP (`POST /mcp`, `GET /mcp`, `DELETE /mcp`) plus `/healthz` and `/readyz` probes with zero direct database or Redis dependencies.
- **Local CLI/IDE Mode**: `FastMCP` stdio transport (`apps/mcp/server.py`) sharing the exact same `TOOL_REGISTRY`, catalog tools, and `execute_tool` layer.
- **Per-Caller Auth & Tenant Isolation**: Hosted HTTP/SSE mode fails closed with HTTP `401` on missing/malformed `Authorization: Bearer <jwt>` or `X-API-Key: <key>` headers, never falls back to process environment tokens, and forwards per-caller credentials over a cookie-free pooled `httpx.AsyncClient` so `hub-api` enforces RBAC and tenant scoping.
- **Observability**: Uses `shared.logging.configure_logging_from_env("hub-mcp")` (sanitized structured JSON logs) and `shared.observability` OpenTelemetry signals (traces, counters/histograms, and OTLP logs).

## Environment Variables

- `ELDER_API_URL` (alias for `ELDER_URL`): Base URL of `hub-api` (default `http://localhost:5000`; `http://hub-api:5000` in container image).
- `MCP_HOST` / `MCP_PORT`: Bind address and port for hosted mode (defaults: `0.0.0.0:8080` in `start.py`, `127.0.0.1:8080` in `server.py`).
- `MCP_TRANSPORT`: `stdio` (default for `server.py`), `streamable-http`, or `sse`.
- `MCP_CORS_ORIGINS`: Comma-separated allowed CORS origins for browser/IDE clients.

**Local `stdio` mode only** (ignored in hosted HTTP/SSE mode):
- `ELDER_API_TOKEN`: Pre-issued Bearer JWT token.
- `ELDER_API_KEY`: Elder API key (`X-API-Key`).
- `ELDER_USERNAME` + `ELDER_PASSWORD`: Credentials for `/api/v1/auth/login`.

## Hardening (hosted mode)

- **Probes**: `/healthz` returns `{"status":"ok"}` and `/readyz` returns `{"status":"ok","ready":<bool>}`; no version, tool or operation counts.
- **Deny list**: unauthenticated and credential-establishing hub-api routes (auth login/register/refresh/logout/captcha, portal-auth, SSO/SAML/OIDC browser flows, public share/document routes, inbound webhook/hook triggers) are excluded from the API catalog and rejected on direct invoke (`apps/mcp/catalog.py` `DENIED_ROUTES`).
- **Destructive operations**: `invoke_api_operation`/`call_api_operation` reject any operation whose catalog entry has `destructive: true` unless the call passes `confirm: true`. That is every `DELETE`/`PUT`/`PATCH` plus POSTs classified by `DESTRUCTIVE_POST_HINTS` in `apps/mcp/catalog.py` (whole-token match on the operation name and path segments: delete, purge, revoke, reset, rotate, disable, restore, execute, erase, regenerate, cleanup, merge, password, ...; ordinary creates are not flagged). The flag is stored in `api_catalog.json` at generation time and shown by `describe_api_operation`; both tools advertise `destructiveHint: true` and `readOnlyHint: false`.
- **Limits** (env, read per request): `MCP_REQUEST_TIMEOUT_S` (60), `MCP_MAX_TOOL_CALLS_PER_BATCH` (5), `MCP_MAX_BATCH_SIZE` (50), `MCP_MAX_CONCURRENT_TOTAL` (64, global in-flight cap regardless of credential, HTTP 429 + `Retry-After`), `MCP_MAX_CONCURRENT_PER_CALLER` (4, keyed by a credential hash, HTTP 429 beyond), `MCP_MAX_TRACKED_CALLERS` (10000, hard bound on the per-caller table; busy entries are never evicted and a full table rejects new callers with 429), `MCP_MAX_CONCURRENT_PER_IP` (16, per client address; `request.remote_addr`, or the right-most untrusted `X-Forwarded-For` hop only when the peer is in `MCP_TRUSTED_PROXIES`, a comma list of CIDRs, unset by default), `MCP_IPV6_PREFIX_LEN` (64, valid 32..128, invalid -> 64 with a warning; IPv6 sources are grouped by this network prefix for the per-IP cap, IPv4-mapped IPv6 maps to its IPv4 key, unparseable/missing addresses share one `unknown` bucket), `MCP_BODY_READ_TIMEOUT_S` (10; the request body is read before any concurrency slot is taken, and a stalled body returns 408 holding no slot), `MCP_MAX_CONCURRENT_BODY_READS` (128, global cap on simultaneous body reads, keyed by IP only because credentials are not yet validated; 429 + `Retry-After` before any buffering), `MCP_MAX_BODY_READS_PER_IP` (8, same IP key/IPv6 grouping), `MCP_MAX_REQUEST_BYTES` (1 MiB; a declared `Content-Length` over it gets 413 before the body is read, chunked bodies are cut off by Quart at the same cap). Known limitation: the body phase is bounded (at most 128 x 1 MiB buffered), but a distributed attacker can still exhaust the global caps; edge rate limiting (#389) is the remaining mitigation, `MCP_MAX_RESPONSE_BYTES` (4 MiB upstream cap; larger responses return an `isError` too-large result).
- **Errors**: callers receive the upstream status code and a short standard phrase only; upstream bodies and transport exception text stay in masked server logs.
- **Metrics**: request metrics label the matched route rule (or `unmatched`), never the raw path.

## Tools

- **90+ Curated Domain Tools**: Infrastructure entities, data stores, networks, organizations, geo, dependency graphs, issues, projects, milestones, helpdesk, vulnerabilities, SBOM, certificates, secrets metadata (never values), KMS keys, access reviews, on-call, IPAM, discovery, sync, webhooks, identities, backups, logs, documents, pages, diagrams, AI resources, streams, and flows.
- **Village ID Lookup**: `lookup_village_id` (`GET /id/{village_id}`) and `resolve_village_id` (`GET /api/v1/id/{village_id}`).
- **Full `hub-api` Catalog Parity (`apps/mcp/api_catalog.json`)**:
  - `list_api_services`: List all Elder API service namespaces and operation counts.
  - `list_api_operations`: Search/filter all cataloged non-internal `/api/*` operations.
  - `describe_api_operation`: Inspect HTTP method, path template, path parameters, summary, and the `destructive` flag.
  - `invoke_api_operation` / `call_api_operation`: Invoke any cataloged non-internal `/api/*` endpoint (destructive operations need `confirm: true`) with strict path-parameter validation and traversal/SSRF blocking.
  - Paths in the catalog equal the routes registered by `hub-api` (declared trailing slashes preserved, function-local blueprint prefixes such as `/api/v1/helpdesk` resolved). `<path:...>` route parameters are listed in each operation's `wildcard_path_params` and accept slash-separated values validated per segment (no `..`, empty/dot segments, backslashes, control characters, `?`, `#`, `://`, or encoded variants).
  - **Excluded non-JSON operations** (`apps/mcp/api_catalog_exclusions.json`, regenerated with the catalog): the tool is JSON-only and never claims to support multipart uploads or binary/CSV/XML downloads. Operations whose handler reads `request.files`, returns `send_file`/`send_from_directory`/a raw `Response`/`make_response` around a non-JSON body, returns a tuple whose headers declare a non-JSON `Content-Type` (e.g. `(csv_text, 200, {"Content-Type": "text/csv"})`), or whose OpenAPI requestBody/2xx response is not JSON are omitted and rejected if invoked:

| Method | Path | Reason |
|--------|------|--------|
| `GET` | `/api/v1/audit-enterprise/export` | non-JSON response body (tuple with text/csv Content-Type) |
| `GET` | `/api/v1/backup/export/{filename}/download` | binary file download response |
| `POST` | `/api/v1/backup/import` | multipart/form-data request body (file upload) |
| `GET` | `/api/v1/backup/{backup_id}/download` | binary file download response |
| `GET` | `/api/v1/diagrams/{diagram_id}/export/json` | binary file download response |
| `GET` | `/api/v1/diagrams/{diagram_id}/export/svg` | binary file download response |
| `GET` | `/api/v1/import/templates/{resource}/download` | non-JSON response body (raw Response) |
| `POST` | `/api/v1/import/{resource}` | multipart/form-data request body (file upload) |
| `GET` | `/api/v1/services/{id}/sbom/export` | non-JSON response body (raw Response) |
| `GET` | `/api/v1/software/{id}/sbom/export` | non-JSON response body (raw Response) |


## AI Researcher (in-cluster MCP client)

The AI Researcher (`POST /api/v1/search/ai-research`) is an MCP client of `hub-mcp`: it
calls `initialize`, `tools/list` and `tools/call` over the same JSON-RPC protocol,
auth and limits as desktop clients, so there is no second tool implementation.

- **Transport**: in-process by default (`httpx.ASGITransport` against `apps.mcp.http_app`, upstream = `ELDER_INTERNAL_API_URL`); set `MCP_SERVER_URL` to point at a remote hub-mcp instead. If hub-mcp is unreachable the request fails with `503 MCP_UNAVAILABLE`; there is no direct-registry fallback.
- **Auth**: the caller's own bearer token (header or HTTP-only cookie, validated by hub-api) is forwarded to hub-mcp, so hub-api enforces the caller's scopes and tenant on every tool call. No service token is minted and there is no static token override. The route requires `ai:read`; the token's tenant must match the resolved tenant.
- **Read-only**: only tools annotated `readOnlyHint: true` (never `destructiveHint`, never `invoke_api_operation`) and permitted by the caller's scopes/enabled modules are offered. Tool output is passed to the model as JSON under `untrusted_data`.

## Using Elder MCP from Claude Code and other desktop clients

One implementation (`hub-mcp`) serves both in-cluster AI Researcher/Associate (as an MCP client) and end users on their desktop, so tool behaviour never diverges. Every call runs with **your own Elder credentials**; `hub-api` enforces your RBAC scopes and tenant, so you only see and change what your Elder account allows. Tenant is derived from your token by `hub-api` and can never be overridden through MCP.

### Get a credential

- **API key** (recommended for desktop): Elder UI > Settings > API Keys (`/api/v1/api-keys`), create a key with only the scopes you need (for example `entities:read`). Sent as `X-API-Key`.
- **Bearer JWT**: obtain via your normal Elder login (`POST /api/v1/auth/login`) or OIDC; short-lived, sent as `Authorization: Bearer <token>`.

Never put tokens in command arguments, committed files, or `.mcp.json` literals; use environment variables.

### With Claude AI (stdio, local process)

Runs `apps/mcp/server.py` on your machine; credentials come from the environment (stdio mode only).

```bash
export ELDER_API_URL=https://elder.example.com   # your hub-api base URL
export ELDER_API_TOKEN=...                        # or ELDER_API_KEY=...
claude mcp add elder -- python3 -m apps.mcp.server
```

The server exposes all tools to the AI agent with full authentication context.

### Hosted / remote (Streamable HTTP, no local process)

Point the client at a deployed `hub-mcp` (`https://<host>/mcp`). Each request must carry credentials; missing or malformed credentials return HTTP 401 and the server never falls back to its own environment.

```bash
claude mcp add --transport http elder https://<host>/mcp \
  --header "Authorization: Bearer $ELDER_API_TOKEN"
# or with an API key:
claude mcp add --transport http elder https://<host>/mcp \
  --header "X-API-Key: $ELDER_API_KEY"
```

Example project `.mcp.json` (secrets expanded from your shell, none stored):

```json
{
  "mcpServers": {
    "elder": {
      "type": "http",
      "url": "${ELDER_MCP_URL:-https://elder.example.com/mcp}",
      "headers": { "Authorization": "Bearer ${ELDER_API_TOKEN}" }
    }
  }
}
```

Known limitation: the global cap (`MCP_MAX_CONCURRENT_TOTAL`) can still be exhausted by a distributed attacker spread across many networks; there is no reserved capacity for verified credentials (it would need a validated-credential cache). Rely on upstream/edge rate limiting (see issue #389).

Operational limits: request body capped at 1 MiB (`MCP_MAX_REQUEST_BYTES`), JSON-RPC batches capped at 50 (`MCP_MAX_BATCH_SIZE`). The API catalog tools only call paths under `/api/` on `ELDER_API_URL` and your credentials are never sent anywhere else. Serve hosted mode behind TLS only.

Note: `docs/CONNECTORS.md` lists `http://api:4000` while the Helm chart uses `http://elder-api:5000` for the API service; set `ELDER_API_URL` to whichever matches your deployment.

## Building & Running

```bash
# Build hub-mcp image from repository root context
docker build -f apps/mcp/Dockerfile -t elder-hub-mcp:local .

# Run hosted HTTP service
docker run --rm --read-only -p 8080:8080 \
  -e ELDER_API_URL=http://host.docker.internal:5000 \
  elder-hub-mcp:local
```

Regenerate the deterministic `hub-api` catalog after adding API endpoints:

```bash
python3 -m apps.mcp.catalog
```
