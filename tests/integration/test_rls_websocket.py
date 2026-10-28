"""Websocket tenant binding under RLS (diagram collaboration).

Quart skips ``before_request`` hooks for websockets, so ``populate_claims`` never
binds a tenant for them. The handler must bind the tenant from the single-use
ticket (minted from a verified JWT) itself; otherwise, under enforced RLS, the
session INSERT / collaborator query run unbound and fail closed. Real DB (app
role), real Redis, real websocket handler.
"""

import asyncio
import json
import os
import secrets

import pytest
import redis
from rls_support import T1, T2, _run, _seed
from sqlalchemy import text

from shared.database import rls

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("_reset_rls_state"),
]

DIAGRAM_ID = 4242


@pytest.fixture()
def ws_app(dal, rls_env):
    """Minimal Quart app mounting only the collaboration blueprint, on the app-role DAL."""
    from quart import Quart

    from apps.api.modules.diagrams.routes.collab import collab_bp

    url = os.environ.get("REDIS_URL", "redis://localhost:56379/0")
    app = Quart(__name__)
    app.config.update(TESTING=True, REDIS_URL=url)
    app.db = dal
    app.extensions["module_redis"] = redis.Redis.from_url(url, decode_responses=True)
    app.register_blueprint(collab_bp, url_prefix="/diagrams")
    with rls_env.admin.begin() as conn:
        conn.execute(text("DELETE FROM dg_collaboration_sessions"))
        conn.execute(text("DELETE FROM dg_diagrams WHERE id = :d"), {"d": DIAGRAM_ID})
        conn.execute(text("DELETE FROM identities WHERE id IN (5, 6, 99)"))
        conn.execute(text("SET session_replication_role = replica"))
        _seed(conn, "dg_diagrams", id=DIAGRAM_ID, tenant_id=T1, title="collab diagram")
        for identity_id, tenant in ((5, T1), (6, T2), (99, T2)):
            _seed(
                conn,
                "identities",
                id=identity_id,
                tenant_id=tenant,
                username=f"ws-user-{identity_id}",
                is_active=True,
            )
        _seed(
            conn,
            "dg_collaboration_sessions",
            tenant_id=T2,
            diagram_id=DIAGRAM_ID,
            identity_id=99,
            session_id="foreign-session",
            socket_id="foreign-session",
            permission="viewer",
            is_active=True,
        )
    yield app
    with rls_env.admin.begin() as conn:
        conn.execute(text("DELETE FROM dg_collaboration_sessions"))
        conn.execute(text("DELETE FROM dg_diagrams WHERE id = :d"), {"d": DIAGRAM_ID})
        conn.execute(text("DELETE FROM identities WHERE id IN (5, 6, 99)"))


def _mint(app, tenant_id, identity_id=5, permission="editor", diagram_id=DIAGRAM_ID):
    """Store a single-use ticket exactly as issue_collab_ticket does; returns its token."""
    ticket = secrets.token_urlsafe(16)
    payload = {
        "diagram_id": diagram_id,
        "identity_id": identity_id,
        "permission": permission,
    }
    if tenant_id is not ...:
        payload["tenant_id"] = tenant_id
    app.extensions["module_redis"].setex(
        f"elder:dg:ticket:{ticket}", 60, json.dumps(payload)
    )
    return ticket


async def _receive(ws):
    """Receive one frame, failing (not hanging) if the handler closes silently.

    Quart's test client never wakes ``receive()`` when a handler closes *after*
    accept, so an unbounded await would hang the whole run.
    """
    return await asyncio.wait_for(ws.receive(), 15)


def _sessions(rls_env, identity_id):
    rows, _ = _run(
        rls_env.admin,
        "SELECT tenant_id, is_active FROM dg_collaboration_sessions WHERE identity_id = :i",
        i=identity_id,
    )
    return rows


@pytest.mark.asyncio
async def test_ticket_tenant_is_bound_for_the_whole_connection(ws_app, rls_env):
    """Own-tenant ticket: the session INSERT succeeds and only own-tenant peers are listed."""
    ticket = _mint(ws_app, T1)
    async with ws_app.test_client().websocket(
        f"/diagrams/{DIAGRAM_ID}/collab/ws?ticket={ticket}"
    ) as ws:
        first = json.loads(await _receive(ws))
        assert first["type"] == "collaborators"
        # The other tenant's active session on the same diagram is invisible (RLS),
        # while this connection's own session was inserted under T1.
        assert {u["identity_id"] for u in first["users"]} == {5}
        assert _sessions(rls_env, 5) == [(T1, True)]
    assert rls.current_tenant_id() is None  # nothing leaks out of the connection


@pytest.mark.asyncio
async def test_the_binding_is_the_tickets_tenant_not_a_fixed_one(ws_app, rls_env):
    ticket = _mint(ws_app, T2, identity_id=6)
    async with ws_app.test_client().websocket(
        f"/diagrams/{DIAGRAM_ID}/collab/ws?ticket={ticket}"
    ) as ws:
        first = json.loads(await _receive(ws))
        assert {u["identity_id"] for u in first["users"]} == {
            6,
            99,
        }  # T2 sees its own peers
        assert _sessions(rls_env, 6) == [(T2, True)]


@pytest.mark.asyncio
@pytest.mark.parametrize("tenant", ["not-a-number", 0, -4, None, ...])
async def test_a_forged_or_missing_ticket_tenant_refuses_the_connection(
    ws_app, rls_env, tenant
):
    """No usable tenant => closed before accept, before any DB write; nothing is inserted."""
    ticket = _mint(ws_app, tenant, identity_id=7)
    async with ws_app.test_client().websocket(
        f"/diagrams/{DIAGRAM_ID}/collab/ws?ticket={ticket}"
    ) as ws:
        with pytest.raises(
            asyncio.TimeoutError
        ):  # closed pre-accept: no frame ever arrives
            await asyncio.wait_for(ws.receive(), 2)
        assert ws.accepted is False
    assert _sessions(rls_env, 7) == []


@pytest.mark.asyncio
async def test_unbound_connection_would_fail_closed_proving_the_binding_matters(
    ws_app, rls_env, monkeypatch
):
    """Control: with the binding removed the session INSERT is rejected by RLS."""
    from apps.api.modules.diagrams.routes import collab

    monkeypatch.setattr(collab.rls, "set_tenant", lambda tenant_id: None)
    ticket = _mint(ws_app, T1, identity_id=8)
    async with ws_app.test_client().websocket(
        f"/diagrams/{DIAGRAM_ID}/collab/ws?ticket={ticket}"
    ) as ws:
        with pytest.raises(
            asyncio.TimeoutError
        ):  # accepted, then closed 1011: no frames
            await asyncio.wait_for(ws.receive(), 3)
        assert ws.accepted is True
    assert _sessions(rls_env, 8) == []


def test_every_websocket_route_in_the_api_is_audited():
    """Only one websocket route exists; a new one must bind its tenant too (see collab.py)."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "apps"
    hits = []
    for path in root.rglob("*.py"):
        if "tests" in path.parts:
            continue
        for match in re.finditer(r"\.websocket\(", path.read_text()):
            hits.append(f"{path.relative_to(root)}:{match.start()}")
    assert len(hits) == 1 and hits[0].startswith(
        "api/modules/diagrams/routes/collab.py"
    ), hits
