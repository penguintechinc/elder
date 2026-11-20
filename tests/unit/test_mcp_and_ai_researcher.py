"""Unit and integration-style tests for Elder MCP server expansion, auth, and AI Researcher."""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import jwt
import pytest

from apps.api.auth.jwt_handler import (
    MAX_SERVICE_RESEARCHER_TTL_SECONDS,
    generate_service_researcher_token,
    generate_token,
    get_current_user,
    get_service_researcher_read_scopes,
    verify_token,
)
from apps.api.auth.rbac import role_scopes
from apps.api.common.modules.tenant_toggle import set_module_enabled
from apps.api.config import Config
from apps.api.main import create_app
from apps.api.services.ai_researcher import (
    AIResearchPermissionError,
    filter_tools_by_scopes,
    get_ai_researcher_config,
    get_tool_required_scope,
    intersect_caller_read_scopes,
    resolve_effective_modules,
    run_ai_research,
)
from apps.api.services.portal_auth.service import PortalAuthService
from apps.mcp.auth import ElderAuthError, ElderSession
from apps.mcp.client import ElderAPIError, ElderClient
from apps.mcp.registry import (
    TOOL_BY_NAME,
    TOOL_REGISTRY,
    get_tools,
    invoke_tool,
    to_ollama_tools,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _seed_org(db, tenant_id: int, name: str) -> int:
    now = datetime.now(UTC)
    org_id = db.organizations.insert(
        name=name,
        tenant_id=tenant_id,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return int(org_id)


def _seed_entity(
    db,
    org_id: int,
    name: str,
    entity_type: str = "database",
) -> int:
    now = datetime.now(UTC)
    entity_id = db.entities.insert(
        name=name,
        type=entity_type,
        organization_id=org_id,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return int(entity_id)


def _seed_user_headers(db, tenant_id: int = 1) -> dict[str, str]:
    now = datetime.now(UTC)
    username = f"researcher-{uuid4().hex[:8]}"
    identity_id = db.identities.insert(
        identity_type="human",
        username=username,
        email=f"{username}@example.com",
        tenant_id=tenant_id,
        auth_provider="local",
        is_active=True,
        is_superuser=False,
        mfa_enabled=False,
        must_change_password=False,
        portal_role="admin",
        created_at=now,
        updated_at=now,
    )
    db.commit()
    identity = db(db.identities.id == identity_id).select().first()
    token = generate_token(identity)
    return {"Authorization": f"Bearer {token}"}


def test_mcp_registry_endpoints_match_real_app_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every MCP tool endpoint_path in TOOL_REGISTRY must correspond to a real route in app.url_map."""
    monkeypatch.setenv("ELDER_MODULES_ENABLED", "all")
    monkeypatch.setenv("ELDER_MODULE_HELPDESK", "true")
    full_app = create_app("testing")

    url_rules = {rule.rule for rule in full_app.url_map.iter_rules()}
    missing: list[tuple[str, str]] = []
    for tool_def in TOOL_REGISTRY:
        if tool_def.endpoint_path not in url_rules:
            missing.append((tool_def.name, tool_def.endpoint_path))

    assert missing == [], f"MCP tools pointing at non-existent routes: {missing}"
    assert "list_secrets_metadata" in TOOL_BY_NAME
    assert all("/unmask" not in tool_def.endpoint_path for tool_def in TOOL_REGISTRY)


def test_mcp_standalone_and_package_importability_plus_server_wiring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """apps/mcp works both via package import and standalone sys.path, and server wires all tools."""
    mcp_dir = str(REPO_ROOT / "apps" / "mcp")
    env = dict(os.environ)
    env["PYTHONPATH"] = mcp_dir
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import auth, client, registry, tools; "
                "assert len(registry.TOOL_REGISTRY) >= 85, len(registry.TOOL_REGISTRY)"
            ),
        ],
        cwd=mcp_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"Standalone import failed: {proc.stderr}"

    registered_names: list[str] = []

    class _FakeFastMCP:
        def __init__(self, name: str, **kwargs: object) -> None:
            self.name = name
            self.kwargs = kwargs

        def tool(self, name: str, description: str):
            def _decorator(fn):
                sig = inspect.signature(fn)
                assert all(
                    p.kind != inspect.Parameter.VAR_KEYWORD
                    for p in sig.parameters.values()
                ), f"{name} exposed VAR_KEYWORD to FastMCP"
                assert description
                registered_names.append(name)
                return fn

            return _decorator

        def run(self, transport: str = "stdio") -> None:
            self.transport = transport

    fake_mcp_pkg = types.ModuleType("mcp")
    fake_server_pkg = types.ModuleType("mcp.server")
    fake_fastmcp_pkg = types.ModuleType("mcp.server.fastmcp")
    fake_fastmcp_pkg.FastMCP = _FakeFastMCP  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mcp", fake_mcp_pkg)
    monkeypatch.setitem(sys.modules, "mcp.server", fake_server_pkg)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fake_fastmcp_pkg)
    sys.modules.pop("apps.mcp.server", None)

    import apps.mcp.server as mcp_server

    assert set(mcp_server.REGISTERED_TOOLS) == set(TOOL_BY_NAME.keys())
    assert set(registered_names) == set(TOOL_BY_NAME.keys())


def test_mcp_read_only_and_module_filtering() -> None:
    """get_tools excludes write tools in read_only=True mode and filters by module."""
    read_only_tools = get_tools(read_only=True)
    read_only_names = {t.name for t in read_only_tools}

    assert all(t.read_only for t in read_only_tools)
    assert "update_entity" not in read_only_names
    assert "create_relationship" not in read_only_names
    assert "delete_relationship" not in read_only_names
    assert "universal_search" in read_only_names

    all_tools = get_tools(read_only=False)
    all_names = {t.name for t in all_tools}
    assert {"update_entity", "create_relationship", "delete_relationship"} <= all_names

    infra_only = get_tools(enabled_modules=["infrastructure"], read_only=True)
    infra_names = {t.name for t in infra_only}
    assert "universal_search" in infra_names  # core tool (required_module=None)
    assert "search_entities" in infra_names
    assert "list_vulnerabilities" not in infra_names  # sbom module
    assert "list_secrets_metadata" not in infra_names  # secrets module
    assert "list_helpdesk_tickets" not in infra_names  # helpdesk module

    ollama_schemas = to_ollama_tools(infra_only)
    assert len(ollama_schemas) == len(infra_only)
    assert ollama_schemas[0]["type"] == "function"
    assert "parameters" in ollama_schemas[0]["function"]


@pytest.mark.asyncio
async def test_elder_client_api_key_auth_put_and_error_handling() -> None:
    """ElderClient supports X-API-Key, put(), and fails loud on HTTP errors."""
    captured: list[tuple[str, str, dict[str, str], bytes]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            (
                request.method,
                request.url.path,
                dict(request.headers),
                request.content,
            )
        )
        if request.url.path == "/api/v1/projects/7":
            return httpx.Response(200, json={"project": {"id": 7, "name": "updated"}})
        if request.url.path == "/api/v1/secrets":
            assert "unmask" not in str(request.url)
            return httpx.Response(200, json={"secrets": [{"id": 1, "name": "db-pass"}]})
        return httpx.Response(403, json={"error": "Forbidden"})

    transport = httpx.MockTransport(_handler)
    session = ElderSession.from_api_key("http://elder.local", "ek_live_test123")
    async with ElderClient(session=session, transport=transport) as client:
        put_res = await client.put("/api/v1/projects/7", json={"name": "updated"})
        assert put_res["project"]["name"] == "updated"

        sec_res = await invoke_tool("list_secrets_metadata", client, {"limit": 10})
        assert sec_res["secrets"][0]["name"] == "db-pass"

        with pytest.raises(PermissionError):
            await invoke_tool(
                "update_entity",
                client,
                {"entity_id": 1, "name": "x"},
                read_only_only=True,
            )

        with pytest.raises(PermissionError):
            await invoke_tool(
                "list_vulnerabilities",
                client,
                {},
                allowed_modules=["infrastructure"],
            )

        with pytest.raises(ElderAPIError) as exc_info:
            await client.get("/api/v1/entities/999")
        assert exc_info.value.status_code == 403

    assert captured[0][0] == "PUT"
    assert captured[0][2].get("x-api-key") == "ek_live_test123"
    assert "authorization" not in captured[0][2]

    with pytest.raises(ElderAuthError):
        await ElderSession(base_url="http://elder.local").auth_headers()


@pytest.mark.asyncio
async def test_service_researcher_jwt_validation_and_strict_enforcement(app) -> None:
    """service_researcher JWTs enforce <=10m TTL, viewer role, read-only scopes, caller delegation, and tenant boundary."""
    async with app.app_context():
        with pytest.raises(ValueError, match="TTL"):
            generate_service_researcher_token(
                tenant_id=1,
                scopes=["entities:read"],
                ttl_seconds=MAX_SERVICE_RESEARCHER_TTL_SECONDS + 1,
                caller_sub="user-1",
            )
        with pytest.raises(ValueError, match="scope"):
            generate_service_researcher_token(
                tenant_id=1,
                scopes=["entities:write"],
                caller_sub="user-1",
            )
        with pytest.raises(ValueError, match="scope"):
            generate_service_researcher_token(
                tenant_id=1,
                scopes=["*:read"],
                caller_sub="user-1",
            )
        with pytest.raises(ValueError, match="scope"):
            generate_service_researcher_token(
                tenant_id=1,
                scopes=[],
                caller_sub="user-1",
            )
        with pytest.raises(ValueError, match="caller_sub"):
            generate_service_researcher_token(
                tenant_id=1,
                scopes=["entities:read"],
                caller_sub="   ",
            )

        valid_token = generate_service_researcher_token(
            tenant_id=1,
            scopes=["infrastructure:read", "ai:read", "infrastructure:read"],
            ttl_seconds=300,
            caller_sub="user-42",
            caller_username="alice",
        )
        verified = verify_token(valid_token)
        assert verified is not None
        assert verified["type"] == "service_researcher"
        assert verified["tenant"] == "1"
        assert verified["roles"] == ["viewer"]
        assert verified["scope"] == ["ai:read", "infrastructure:read"]
        assert verified["sub"] == "service-researcher:1:user-42"
        assert verified["delegated_by"] == "user-42"
        assert verified["act"] == {"sub": "user-42", "username": "alice"}

        async with app.test_request_context(
            "/api/v1/entities",
            headers={"Authorization": f"Bearer {valid_token}"},
        ):
            identity = get_current_user()
            assert identity is not None
            assert getattr(identity, "delegated_by", None) == "user-42"
            assert getattr(identity, "act", None) == {
                "sub": "user-42",
                "username": "alice",
            }

        secret = app.config["JWT_SECRET_KEY"] or app.config["SECRET_KEY"]
        alg = app.config["JWT_ALGORITHM"]
        now = datetime.now(UTC)

        forged_long = jwt.encode(
            {
                "sub": "service-researcher:1:user-42",
                "delegated_by": "user-42",
                "type": "service_researcher",
                "iat": now,
                "exp": now + timedelta(seconds=900),
                "tenant": "1",
                "scope": ["entities:read"],
                "iss": app.config.get("JWT_ISSUER", "elder-api"),
                "aud": app.config.get("JWT_AUDIENCE", "elder-api"),
            },
            secret,
            algorithm=alg,
        )
        assert verify_token(forged_long) is None

        forged_write_scope = jwt.encode(
            {
                "sub": "service-researcher:1:user-42",
                "delegated_by": "user-42",
                "type": "service_researcher",
                "iat": now,
                "exp": now + timedelta(seconds=120),
                "tenant": "1",
                "scope": ["entities:read", "entities:write"],
                "iss": app.config.get("JWT_ISSUER", "elder-api"),
                "aud": app.config.get("JWT_AUDIENCE", "elder-api"),
            },
            secret,
            algorithm=alg,
        )
        assert verify_token(forged_write_scope) is None

        forged_no_iss = jwt.encode(
            {
                "sub": "service-researcher:1:user-42",
                "delegated_by": "user-42",
                "type": "service_researcher",
                "iat": now,
                "exp": now + timedelta(seconds=120),
                "tenant": "1",
                "scope": ["entities:read"],
                "aud": app.config.get("JWT_AUDIENCE", "elder-api"),
            },
            secret,
            algorithm=alg,
        )
        assert verify_token(forged_no_iss) is None

        forged_no_delegated_by = jwt.encode(
            {
                "sub": "service-researcher:1",
                "type": "service_researcher",
                "iat": now,
                "exp": now + timedelta(seconds=120),
                "tenant": "1",
                "scope": ["entities:read"],
                "iss": app.config.get("JWT_ISSUER", "elder-api"),
                "aud": app.config.get("JWT_AUDIENCE", "elder-api"),
            },
            secret,
            algorithm=alg,
        )
        assert verify_token(forged_no_delegated_by) is None

        forged_blank_delegated_by = jwt.encode(
            {
                "sub": "service-researcher:1",
                "delegated_by": "   ",
                "type": "service_researcher",
                "iat": now,
                "exp": now + timedelta(seconds=120),
                "tenant": "1",
                "scope": ["entities:read"],
                "iss": app.config.get("JWT_ISSUER", "elder-api"),
                "aud": app.config.get("JWT_AUDIENCE", "elder-api"),
            },
            secret,
            algorithm=alg,
        )
        assert verify_token(forged_blank_delegated_by) is None

        db = app.db
        org1 = _seed_org(db, tenant_id=1, name=f"Tenant1Org-{uuid4().hex[:6]}")
        t1_entity = _seed_entity(db, org_id=org1, name="t1-app-node")

        other_tid = int(
            db.tenants.insert(
                name="Foreign Corp",
                slug=f"foreign-{uuid4().hex[:8]}",
                is_active=True,
            )
        )
        db.commit()
        org2 = _seed_org(db, tenant_id=other_tid, name="ForeignOrg")
        t2_entity = _seed_entity(db, org_id=org2, name="t2-secret-node")

    headers = {"Authorization": f"Bearer {valid_token}"}
    async with app.test_client() as test_cli:
        resp_read = await test_cli.get(f"/api/v1/entities/{t1_entity}", headers=headers)
        assert resp_read.status_code == 200

        resp_cross_entity = await test_cli.get(
            f"/api/v1/entities/{t2_entity}", headers=headers
        )
        assert resp_cross_entity.status_code in (403, 404)

        resp_cross_org = await test_cli.get(
            f"/api/v1/organizations/{org2}", headers=headers
        )
        assert resp_cross_org.status_code in (403, 404)

        resp_write_entity = await test_cli.post(
            "/api/v1/entities",
            headers=headers,
            json={"name": "hacked", "type": "server", "organization_id": org1},
        )
        assert resp_write_entity.status_code == 403

        resp_write_dep = await test_cli.post(
            "/api/v1/dependencies",
            headers=headers,
            json={
                "source_type": "entity",
                "source_id": t1_entity,
                "target_type": "entity",
                "target_id": t1_entity,
                "dependency_type": "uses",
            },
        )
        assert resp_write_dep.status_code == 403

        resp_delete = await test_cli.delete(
            f"/api/v1/entities/{t1_entity}", headers=headers
        )
        assert resp_delete.status_code == 403

        resp_recursive = await test_cli.post(
            "/api/v1/search/ai-research",
            headers=headers,
            json={"query": "recurse"},
        )
        assert resp_recursive.status_code == 403


@pytest.mark.asyncio
async def test_ai_researcher_config_module_gating_and_ollama_status(app) -> None:
    """Config endpoint reflects tenant module toggles, Ollama tags, and unreachable state cleanly."""
    sentinel = object()
    prior_ollama_transport = app.extensions.get(
        "ai_researcher_ollama_transport", sentinel
    )
    redis_client = app.extensions.get("module_redis")
    prior_had_row = False
    prior_enabled = True
    prior_settings = None

    async with app.app_context():
        db = app.db
        user_headers = _seed_user_headers(db, tenant_id=1)
        baseline_modules = resolve_effective_modules(app, tenant_id=1)
        assert "infrastructure" in baseline_modules

        existing = (
            db(
                (db.tenant_modules.tenant_id == 1)
                & (db.tenant_modules.module_name == "sbom")
            )
            .select()
            .first()
        )
        if existing is not None:
            prior_had_row = True
            prior_enabled = bool(existing.enabled)
            prior_settings = existing.settings

    try:
        async with app.app_context():
            db = app.db
            if redis_client is not None:
                set_module_enabled(db, redis_client, 1, "sbom", False)
            elif prior_had_row:
                db(
                    (db.tenant_modules.tenant_id == 1)
                    & (db.tenant_modules.module_name == "sbom")
                ).update(enabled=False)
            else:
                db.tenant_modules.insert(
                    tenant_id=1,
                    module_name="sbom",
                    enabled=False,
                )
            db.commit()

            reduced_modules = resolve_effective_modules(app, tenant_id=1)
            assert "sbom" not in reduced_modules
            expected_tool_count = len(
                get_tools(enabled_modules=reduced_modules, read_only=True)
            )

        app.extensions["ai_researcher_ollama_transport"] = httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={"models": [{"name": "gemma4:e4b"}, {"name": "llama3.2:latest"}]},
            )
        )
        async with app.test_client() as test_cli:
            resp_ok = await test_cli.get(
                "/api/v1/search/ai-research/config", headers=user_headers
            )
            assert resp_ok.status_code == 200
            cfg = await resp_ok.get_json()
            assert cfg["default_model"] == "gemma4:e4b"
            assert cfg["ollama_reachable"] is True
            assert cfg["ollama_error"] is None
            assert cfg["mcp_tool_count"] == expected_tool_count
            assert "sbom" not in cfg["enabled_modules"]
            assert "gemma4:e4b" in cfg["available_models"]

        def _unreachable(_req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        app.extensions["ai_researcher_ollama_transport"] = httpx.MockTransport(
            _unreachable
        )
        async with app.test_client() as test_cli:
            resp_down = await test_cli.get(
                "/api/v1/search/ai-research/config", headers=user_headers
            )
            assert resp_down.status_code == 200
            down_cfg = await resp_down.get_json()
            assert down_cfg["ollama_reachable"] is False
            assert down_cfg["ollama_error"] is not None
            assert "connection refused" in down_cfg["ollama_error"]
    finally:
        if prior_ollama_transport is sentinel:
            app.extensions.pop("ai_researcher_ollama_transport", None)
        else:
            app.extensions["ai_researcher_ollama_transport"] = prior_ollama_transport

        async with app.app_context():
            db = app.db
            if prior_had_row:
                if redis_client is not None:
                    set_module_enabled(
                        db, redis_client, 1, "sbom", prior_enabled, prior_settings
                    )
                else:
                    db(
                        (db.tenant_modules.tenant_id == 1)
                        & (db.tenant_modules.module_name == "sbom")
                    ).update(enabled=prior_enabled, settings=prior_settings)
            else:
                db(
                    (db.tenant_modules.tenant_id == 1)
                    & (db.tenant_modules.module_name == "sbom")
                ).delete()
                if redis_client is not None:
                    redis_client.delete("elder:modtoggle:1")
            db.commit()


@pytest.mark.asyncio
async def test_ai_researcher_multistep_tool_calling_and_fail_loud_503(app) -> None:
    """Multi-step Ollama /api/chat invokes Elder MCP tools with service JWT and fails loud on 503."""
    sentinel = object()
    prior_ollama_transport = app.extensions.get(
        "ai_researcher_ollama_transport", sentinel
    )
    prior_elder_transport = app.extensions.get(
        "ai_researcher_elder_transport", sentinel
    )

    async with app.app_context():
        db = app.db
        user_headers = _seed_user_headers(db, tenant_id=1)
        org_id = _seed_org(db, tenant_id=1, name=f"ResearchOrg-{uuid4().hex[:6]}")
        entity_name = f"prod-db-{uuid4().hex[:6]}"
        entity_id = _seed_entity(db, org_id=org_id, name=entity_name)

    chat_requests: list[dict[str, Any]] = []

    def _ollama_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        chat_requests.append(body)
        if len(chat_requests) == 1:
            return httpx.Response(
                200,
                json={
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "search_entities",
                                    "arguments": {"name": entity_name},
                                }
                            }
                        ],
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": (
                        f"## Findings\n- Discovered database **{entity_name}** "
                        f"(ID `{entity_id}`)."
                    ),
                }
            },
        )

    try:
        app.extensions["ai_researcher_ollama_transport"] = httpx.MockTransport(
            _ollama_handler
        )
        app.extensions["ai_researcher_elder_transport"] = httpx.ASGITransport(app=app)

        async with app.test_client() as test_cli:
            resp = await test_cli.post(
                "/api/v1/search/ai-research",
                headers=user_headers,
                json={"query": f"Find entity {entity_name}"},
            )
            assert resp.status_code == 200
            data = await resp.get_json()

        assert len(chat_requests) == 2
        assert chat_requests[0]["model"] == "gemma4:e4b"
        offered_names = {t["function"]["name"] for t in chat_requests[0]["tools"]}
        assert "search_entities" in offered_names
        assert "update_entity" not in offered_names
        assert "create_relationship" not in offered_names

        tool_msgs = [m for m in chat_requests[1]["messages"] if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert entity_name in tool_msgs[0]["content"]

        assert entity_name in data["answer"]
        assert len(data["steps"]) == 1
        assert data["steps"][0]["tool"] == "search_entities"
        assert data["steps"][0]["status"] == "ok"

        entity_citations = [
            c
            for c in data["citations"]
            if c["resource_type"] == "entity" and int(c["id"]) == entity_id
        ]
        assert len(entity_citations) == 1
        assert entity_citations[0]["url"] == f"/entities/{entity_id}"
        assert entity_citations[0]["name"] == entity_name

        def _ollama_down(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("dial tcp: connection refused")

        app.extensions["ai_researcher_ollama_transport"] = httpx.MockTransport(
            _ollama_down
        )
        async with app.test_client() as test_cli:
            resp_503 = await test_cli.post(
                "/api/v1/search/ai-research",
                headers=user_headers,
                json={"query": "Check system status"},
            )
            assert resp_503.status_code == 503
            err_data = await resp_503.get_json()
            assert err_data["error"] == "OLLAMA_UNAVAILABLE"
    finally:
        if prior_ollama_transport is sentinel:
            app.extensions.pop("ai_researcher_ollama_transport", None)
        else:
            app.extensions["ai_researcher_ollama_transport"] = prior_ollama_transport
        if prior_elder_transport is sentinel:
            app.extensions.pop("ai_researcher_elder_transport", None)
        else:
            app.extensions["ai_researcher_elder_transport"] = prior_elder_transport


def _encode_test_jwt(
    app,
    *,
    sub: str,
    tenant_id: int = 1,
    token_type: str = "access",
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
    extra: dict | None = None,
) -> str:
    secret = app.config["JWT_SECRET_KEY"] or app.config["SECRET_KEY"]
    alg = app.config["JWT_ALGORITHM"]
    now = datetime.now(UTC)
    payload: dict = {
        "sub": sub,
        "type": token_type,
        "iat": now,
        "exp": now + timedelta(minutes=15),
        "iss": app.config.get("JWT_ISSUER", "elder-api"),
        "aud": app.config.get("JWT_AUDIENCE", "elder-api"),
    }
    if token_type == "portal_user":
        payload["tenant_id"] = tenant_id
    else:
        payload["tenant"] = str(tenant_id)
    if roles is not None:
        payload["roles"] = roles
    if scopes is not None:
        payload["scope"] = scopes
    if extra:
        payload.update(extra)
    return jwt.encode(payload, secret, algorithm=alg)


def _create_test_identity(
    app,
    *,
    username: str,
    tenant_id: int = 1,
    portal_role: str = "observer",
    is_superuser: bool = False,
) -> int:
    existing = app.db(app.db.identities.username == username).select().first()
    if existing:
        app.db(app.db.identities.id == existing.id).delete()
        app.db.commit()
    ident_id = app.db.identities.insert(
        tenant_id=tenant_id,
        identity_type="human",
        username=username,
        email=f"{username}@example.com",
        auth_provider="local",
        is_active=True,
        is_superuser=is_superuser,
        mfa_enabled=False,
        must_change_password=False,
        do_not_sell_share=False,
        portal_role=portal_role,
    )
    app.db.commit()
    return int(ident_id)


def _delete_test_identity(app, ident_id: int) -> None:
    app.db(app.db.identities.id == ident_id).delete()
    app.db.commit()


def _create_test_portal_user(
    app,
    *,
    email: str,
    tenant_id: int = 1,
    global_role: str | None = None,
    tenant_role: str | None = "observer",
) -> int:
    existing = app.db(app.db.portal_users.email == email).select().first()
    if existing:
        app.db(app.db.portal_users.id == existing.id).delete()
        app.db.commit()
    pu_id = app.db.portal_users.insert(
        tenant_id=tenant_id,
        email=email,
        password_hash="test-hash",
        global_role=global_role,
        tenant_role=tenant_role,
        is_active=True,
        email_verified=True,
    )
    app.db.commit()
    return int(pu_id)


def _delete_test_portal_user(app, pu_id: int) -> None:
    app.db(app.db.portal_users.id == pu_id).delete()
    app.db.commit()


@pytest.mark.asyncio
async def test_ai_research_endpoints_require_ai_read_scope(app, async_client) -> None:
    """Callers without ai:read are rejected with 403 on both AI research config and run endpoints."""
    async with app.app_context():
        ident_id = _create_test_identity(
            app, username="sec-observer-no-ai", tenant_id=1, portal_role="observer"
        )
        no_ai_token = _encode_test_jwt(
            app,
            sub=str(ident_id),
            tenant_id=1,
            roles=["observer"],
            scopes=["infrastructure:read"],
            extra={"username": "sec-observer-no-ai"},
        )

    headers = {"Authorization": f"Bearer {no_ai_token}"}
    try:
        cfg_resp = await async_client.get(
            "/api/v1/search/ai-research/config", headers=headers
        )
        assert cfg_resp.status_code == 403

        run_resp = await async_client.post(
            "/api/v1/search/ai-research",
            headers=headers,
            json={"query": "find web hosts"},
        )
        assert run_resp.status_code == 403

        async with app.app_context():
            with pytest.raises(AIResearchPermissionError):
                await run_ai_research(
                    app,
                    1,
                    query="find web hosts",
                    caller_sub=str(ident_id),
                    caller_scopes=["infrastructure:read"],
                )

            with pytest.raises(AIResearchPermissionError):
                await run_ai_research(
                    app,
                    1,
                    query="find web hosts",
                    caller_sub="   ",
                    caller_scopes=["ai:read", "infrastructure:read"],
                )
    finally:
        async with app.app_context():
            _delete_test_identity(app, ident_id)


@pytest.mark.asyncio
async def test_ai_research_confused_deputy_scope_and_tool_filtering(
    app, async_client
) -> None:
    """AI researcher narrows downstream JWT scopes, carries caller delegation, and blocks unpermitted tools."""
    module_redis = app.extensions.get("module_redis")
    async with app.app_context():
        prior_modules = resolve_effective_modules(app, 1)
        prior_secrets = "secrets" in prior_modules
        prior_issues = "issues" in prior_modules
        set_module_enabled(app.db, module_redis, 1, "secrets", True)
        set_module_enabled(app.db, module_redis, 1, "issues", True)
        ident_id = _create_test_identity(
            app, username="narrow", tenant_id=1, portal_role="observer"
        )
        narrow_scopes = ["ai:read", "infrastructure:read"]
        caller_token = _encode_test_jwt(
            app,
            sub=str(ident_id),
            tenant_id=1,
            roles=["observer"],
            scopes=narrow_scopes,
            extra={"username": "narrow"},
        )
        full_admin_cfg = await get_ai_researcher_config(
            app, 1, caller_scopes=get_service_researcher_read_scopes()
        )
        expected_narrow_tools = filter_tools_by_scopes(
            get_tools(
                enabled_modules=resolve_effective_modules(app, 1), read_only=True
            ),
            narrow_scopes,
        )

    sentinel = object()
    prior_ollama_transport = app.extensions.get(
        "ai_researcher_ollama_transport", sentinel
    )
    prior_elder_transport = app.extensions.get(
        "ai_researcher_elder_transport", sentinel
    )

    seen_ollama_tool_names: list[list[str]] = []
    seen_elder_paths: list[str] = []
    seen_elder_tokens: list[str] = []
    ollama_turn = 0

    def ollama_handler(request: httpx.Request) -> httpx.Response:
        nonlocal ollama_turn
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3.2"}]})
        if request.url.path == "/api/chat":
            body = json.loads(request.content.decode("utf-8"))
            tools = body.get("tools") or []
            seen_ollama_tool_names.append([t["function"]["name"] for t in tools])
            ollama_turn += 1
            if ollama_turn == 1:
                return httpx.Response(
                    200,
                    json={
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "search_entities",
                                        "arguments": {"query": "api"},
                                    }
                                },
                                {
                                    "function": {
                                        "name": "list_secrets_metadata",
                                        "arguments": {},
                                    }
                                },
                            ],
                        }
                    },
                )
            messages = body.get("messages") or []
            tool_msgs = [
                json.loads(m["content"]) for m in messages if m.get("role") == "tool"
            ]
            assert any(
                "list_secrets_metadata" in msg.get("error", "")
                and "not permitted" in msg.get("error", "")
                for msg in tool_msgs
            )
            return httpx.Response(
                200,
                json={
                    "message": {
                        "role": "assistant",
                        "content": "Found matching entities; secret tools are not permitted.",
                    }
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    def elder_handler(request: httpx.Request) -> httpx.Response:
        seen_elder_paths.append(request.url.path)
        auth_header = request.headers.get("Authorization", "")
        assert auth_header.startswith("Bearer ")
        seen_elder_tokens.append(auth_header.removeprefix("Bearer ").strip())
        if request.url.path.startswith("/api/v1/secrets"):
            return httpx.Response(500, json={"error": "must not be called"})
        if request.url.path == "/api/v1/entities":
            return httpx.Response(
                200,
                json={
                    "entities": [{"id": 10, "name": "api-gw", "village_id": "v-api-1"}]
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    app.extensions["ai_researcher_ollama_transport"] = httpx.MockTransport(
        ollama_handler
    )
    app.extensions["ai_researcher_elder_transport"] = httpx.MockTransport(elder_handler)

    try:
        headers = {"Authorization": f"Bearer {caller_token}"}
        cfg_resp = await async_client.get(
            "/api/v1/search/ai-research/config", headers=headers
        )
        assert cfg_resp.status_code == 200
        cfg_data = await cfg_resp.get_json()
        assert cfg_data["mcp_tool_count"] == len(expected_narrow_tools)
        assert cfg_data["mcp_tool_count"] < full_admin_cfg["mcp_tool_count"]

        run_resp = await async_client.post(
            "/api/v1/search/ai-research",
            headers=headers,
            json={"query": "inspect entities and secrets"},
        )
        assert run_resp.status_code == 200
        run_data = await run_resp.get_json()
        assert "Found matching entities" in run_data["answer"]
        assert any(
            s.get("tool") == "list_secrets_metadata" and s.get("status") == "error"
            for s in run_data.get("steps", [])
        )
        assert any(
            s.get("tool") == "search_entities" and s.get("status") == "ok"
            for s in run_data.get("steps", [])
        )

        assert seen_ollama_tool_names
        for exposed_names in seen_ollama_tool_names:
            assert "search_entities" in exposed_names
            assert "universal_search" in exposed_names
            assert "list_secrets_metadata" not in exposed_names
            assert "list_issues" not in exposed_names
            assert "list_identities" not in exposed_names

        assert "/api/v1/entities" in seen_elder_paths
        assert not any(p.startswith("/api/v1/secrets") for p in seen_elder_paths)
        assert len(seen_elder_tokens) >= 1

        async with app.app_context():
            for downstream_tok in seen_elder_tokens:
                claims = verify_token(downstream_tok)
                assert claims is not None
                assert claims["type"] == "service_researcher"
                assert claims["tenant"] == "1"
                assert claims["roles"] == ["viewer"]
                assert claims["scope"] == ["ai:read", "infrastructure:read"]
                assert claims["delegated_by"] == str(ident_id)
                assert claims["sub"] == f"service-researcher:1:{ident_id}"
                assert claims["act"] == {"sub": str(ident_id), "username": "narrow"}
    finally:
        async with app.app_context():
            _delete_test_identity(app, ident_id)
            set_module_enabled(app.db, module_redis, 1, "secrets", prior_secrets)
            set_module_enabled(app.db, module_redis, 1, "issues", prior_issues)
        if prior_ollama_transport is sentinel:
            app.extensions.pop("ai_researcher_ollama_transport", None)
        else:
            app.extensions["ai_researcher_ollama_transport"] = prior_ollama_transport
        if prior_elder_transport is sentinel:
            app.extensions.pop("ai_researcher_elder_transport", None)
        else:
            app.extensions["ai_researcher_elder_transport"] = prior_elder_transport


def test_tool_scope_mapping_completeness_and_intersection() -> None:
    """Every read-only MCP tool maps to a registered read scope; scope intersection drops mutations."""
    catalog = set(get_service_researcher_read_scopes())
    read_only_tools = [tool for tool in TOOL_REGISTRY if tool.read_only]
    assert len(read_only_tools) > 0

    for tool in read_only_tools:
        req_scope = get_tool_required_scope(tool)
        assert (
            req_scope is not None
        ), f"Missing required scope for read-only tool {tool.name}"
        assert (
            req_scope in catalog
        ), f"Scope {req_scope!r} for tool {tool.name} missing from catalog"

    assert intersect_caller_read_scopes(
        [
            "ai:read",
            "entities:write",
            "secrets:admin",
            "issues:delete",
            "flows:execute",
            "access_reviews:approve",
            "entities:read",
        ]
    ) == ["ai:read", "entities:read"]
    assert intersect_caller_read_scopes("ai:read entities:read entities:write") == [
        "ai:read",
        "entities:read",
    ]
    wildcard_expanded = intersect_caller_read_scopes(["*:read"])
    assert "*:read" not in wildcard_expanded
    assert wildcard_expanded == sorted(catalog)
    assert intersect_caller_read_scopes(None) == []

    observer_scopes = role_scopes("observer")
    assert "ai:read" in observer_scopes
    assert "entities:read" in observer_scopes
    assert "infrastructure:read" in observer_scopes
    assert "documents:read" in observer_scopes
    assert "webhooks_alerting:read" in observer_scopes
    assert "users:read" not in observer_scopes
    assert "backup:read" not in observer_scopes
    assert "logs:read" not in observer_scopes
    assert all(s.endswith(":read") for s in observer_scopes)

    all_module_ids = {t.required_module for t in read_only_tools if t.required_module}
    all_read_tools = get_tools(enabled_modules=all_module_ids, read_only=True)

    observer_tool_names = {
        t.name for t in filter_tools_by_scopes(all_read_tools, observer_scopes)
    }
    for sensitive_tool in (
        "list_identities",
        "get_identity",
        "list_identity_groups",
        "list_backups",
        "get_backup_stats",
        "list_system_logs",
    ):
        assert sensitive_tool not in observer_tool_names

    editor_tool_names = {
        t.name for t in filter_tools_by_scopes(all_read_tools, role_scopes("editor"))
    }
    assert "list_identities" in editor_tool_names
    assert "list_backups" not in editor_tool_names
    assert "list_system_logs" not in editor_tool_names

    admin_tool_names = {
        t.name for t in filter_tools_by_scopes(all_read_tools, role_scopes("admin"))
    }
    assert "list_backups" in admin_tool_names
    assert "list_system_logs" in admin_tool_names


@pytest.mark.asyncio
async def test_cross_tenant_service_token_override_completely_removed(app) -> None:
    """AI_RESEARCHER_SERVICE_TOKEN is absent from Config/app.config and ignored if manually injected."""
    assert not hasattr(Config, "AI_RESEARCHER_SERVICE_TOKEN")
    assert "AI_RESEARCHER_SERVICE_TOKEN" not in app.config

    async with app.app_context():
        foreign_token = generate_service_researcher_token(
            tenant_id=2,
            scopes=["ai:read", "infrastructure:read"],
            ttl_seconds=300,
            caller_sub="foreign-admin",
        )
        seen_tokens: list[str] = []

        def ollama_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/chat":
                body = json.loads(request.content.decode("utf-8"))
                tool_msgs = [
                    m for m in (body.get("messages") or []) if m.get("role") == "tool"
                ]
                if not tool_msgs:
                    return httpx.Response(
                        200,
                        json={
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "function": {
                                            "name": "search_entities",
                                            "arguments": {"query": "db"},
                                        }
                                    }
                                ],
                            }
                        },
                    )
                return httpx.Response(
                    200, json={"message": {"role": "assistant", "content": "Done"}}
                )
            return httpx.Response(404, json={"error": "not found"})

        def elder_handler(request: httpx.Request) -> httpx.Response:
            auth_header = request.headers.get("Authorization", "")
            seen_tokens.append(auth_header.removeprefix("Bearer ").strip())
            return httpx.Response(200, json={"entities": []})

        app.config["AI_RESEARCHER_SERVICE_TOKEN"] = foreign_token
        try:
            result = await run_ai_research(
                app,
                1,
                query="db",
                caller_sub="caller-tenant-1",
                caller_scopes=["ai:read", "infrastructure:read"],
                ollama_transport=httpx.MockTransport(ollama_handler),
                elder_transport=httpx.MockTransport(elder_handler),
            )
            assert result["answer"] == "Done"
            assert len(seen_tokens) == 1
            assert seen_tokens[0] != foreign_token
            verified = verify_token(seen_tokens[0])
            assert verified is not None
            assert verified["tenant"] == "1"
            assert verified["delegated_by"] == "caller-tenant-1"
        finally:
            app.config.pop("AI_RESEARCHER_SERVICE_TOKEN", None)


@pytest.mark.asyncio
async def test_portal_user_scopes_and_superuser_empty_scope_fallback(
    app, async_client
) -> None:
    """portal_user JWTs receive role-derived read scopes (including legacy cookies) and non-superusers cannot bypass empty scope."""
    observer_claims = PortalAuthService.generate_jwt_claims(
        {
            "id": 71,
            "email": "obs@example.com",
            "tenant_id": 1,
            "global_role": None,
            "tenant_role": "observer",
        }
    )
    assert observer_claims["roles"] == ["observer"]
    assert observer_claims["scope"] == role_scopes("observer")
    assert "ai:read" in observer_claims["scope"]
    assert "users:read" not in observer_claims["scope"]
    assert all(s.endswith(":read") for s in observer_claims["scope"])

    superadmin_claims = PortalAuthService.generate_jwt_claims(
        {
            "id": 72,
            "email": "sa@example.com",
            "tenant_id": 1,
            "global_role": "superadmin",
            "tenant_role": "observer",
        }
    )
    assert superadmin_claims["roles"] == ["admin"]
    assert superadmin_claims["scope"] == role_scopes("admin")

    sentinel = object()
    prior_ollama_transport = app.extensions.get(
        "ai_researcher_ollama_transport", sentinel
    )
    prior_elder_transport = app.extensions.get(
        "ai_researcher_elder_transport", sentinel
    )
    seen_downstream_scopes: list[list[str]] = []

    def ollama_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3.2"}]})
        if request.url.path == "/api/chat":
            body = json.loads(request.content.decode("utf-8"))
            tool_msgs = [
                m for m in (body.get("messages") or []) if m.get("role") == "tool"
            ]
            if not tool_msgs:
                return httpx.Response(
                    200,
                    json={
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "search_entities",
                                        "arguments": {"query": "edge"},
                                    }
                                }
                            ],
                        }
                    },
                )
            return httpx.Response(
                200, json={"message": {"role": "assistant", "content": "OK"}}
            )
        return httpx.Response(404, json={"error": "not found"})

    def elder_handler(request: httpx.Request) -> httpx.Response:
        tok = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        seen_downstream_scopes.append(
            list(jwt.decode(tok, options={"verify_signature": False})["scope"])
        )
        return httpx.Response(200, json={"entities": []})

    app.extensions["ai_researcher_ollama_transport"] = httpx.MockTransport(
        ollama_handler
    )
    app.extensions["ai_researcher_elder_transport"] = httpx.MockTransport(elder_handler)
    pu_email = "sec-portal-obs@example.com"
    async with app.app_context():
        pu_id = _create_test_portal_user(
            app, email=pu_email, tenant_id=1, global_role=None, tenant_role="observer"
        )
        non_su_id = _create_test_identity(
            app,
            username="sec-empty-regular",
            tenant_id=1,
            portal_role="observer",
            is_superuser=False,
        )
        legacy_portal_token = _encode_test_jwt(
            app,
            sub=str(pu_id),
            tenant_id=1,
            token_type="portal_user",
            extra={"email": pu_email, "tenant_role": "observer", "global_role": None},
        )
        empty_non_su = _encode_test_jwt(
            app,
            sub=str(non_su_id),
            tenant_id=1,
            token_type="access",
            roles=[],
            scopes=[],
            extra={"username": "sec-empty-regular"},
        )
        empty_su = _encode_test_jwt(
            app,
            sub="1",
            tenant_id=1,
            token_type="access",
            roles=[],
            scopes=[],
            extra={"username": "admin@localhost.local"},
        )
        expected_catalog_sorted = sorted(get_service_researcher_read_scopes())

    try:
        portal_headers = {"Authorization": f"Bearer {legacy_portal_token}"}

        cfg_resp = await async_client.get(
            "/api/v1/search/ai-research/config", headers=portal_headers
        )
        assert cfg_resp.status_code == 200
        run_resp = await async_client.post(
            "/api/v1/search/ai-research",
            headers=portal_headers,
            json={"query": "edge"},
        )
        assert run_resp.status_code == 200
        assert len(seen_downstream_scopes) == 1
        assert seen_downstream_scopes[0] == sorted(
            intersect_caller_read_scopes(role_scopes("observer"))
        )
        assert "users:read" not in seen_downstream_scopes[0]
        assert "backup:read" not in seen_downstream_scopes[0]

        non_su_headers = {"Authorization": f"Bearer {empty_non_su}"}
        assert (
            await async_client.get(
                "/api/v1/search/ai-research/config", headers=non_su_headers
            )
        ).status_code == 403
        assert (
            await async_client.post(
                "/api/v1/search/ai-research",
                headers=non_su_headers,
                json={"query": "edge"},
            )
        ).status_code == 403

        su_headers = {"Authorization": f"Bearer {empty_su}"}
        su_cfg = await async_client.get(
            "/api/v1/search/ai-research/config", headers=su_headers
        )
        assert su_cfg.status_code == 200
        su_run = await async_client.post(
            "/api/v1/search/ai-research",
            headers=su_headers,
            json={"query": "edge"},
        )
        assert su_run.status_code == 200
        assert seen_downstream_scopes[-1] == expected_catalog_sorted
    finally:
        async with app.app_context():
            _delete_test_portal_user(app, pu_id)
            _delete_test_identity(app, non_su_id)
        if prior_ollama_transport is sentinel:
            app.extensions.pop("ai_researcher_ollama_transport", None)
        else:
            app.extensions["ai_researcher_ollama_transport"] = prior_ollama_transport
        if prior_elder_transport is sentinel:
            app.extensions.pop("ai_researcher_elder_transport", None)
        else:
            app.extensions["ai_researcher_elder_transport"] = prior_elder_transport
