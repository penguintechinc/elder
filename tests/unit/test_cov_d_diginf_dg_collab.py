"""Tests for the diagram collaboration ticket endpoint and WebSocket handler.

Real Quart app, real Redis and Postgres.  License and PostHog lookups (true
external boundaries) are patched; WebSocket traffic goes through the real
handler via the Quart test client.
"""

# flake8: noqa: E501

import asyncio
import json
import secrets
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from quart.wrappers import Websocket

from apps.api.modules.diagrams.routes import collab
from tests.unit._cov_d_helpers import call, in_db, now, world  # noqa: F401
from tests.unit.test_cov_d_diginf_dg_core import dw, tok  # noqa: F401

DG = "/api/v1/diagrams"


class _Lic:
    """License client stub whose validation result is configurable."""

    def __init__(self, result=True, boom=False):
        """Store the canned validation outcome."""
        self.result = result
        self.boom = boom

    def validate(self):
        """Return the canned outcome or raise when configured to."""
        if self.boom:
            raise RuntimeError("lic down")
        return self.result


def _app(**ext):
    """Build a minimal app stand-in exposing ``extensions``."""
    return SimpleNamespace(extensions=ext)


def test_collaboration_gating_branches():
    """License layer and PostHog layer produce the right enabled/reason pairs."""
    with (
        patch.object(collab, "is_bypass_domain", return_value=True),
        patch.object(collab, "flag_enabled", return_value=True),
    ):
        assert collab._is_collaboration_enabled(_app(), "1") == (True, None)
    with (
        patch.object(collab, "is_bypass_domain", return_value=False),
        patch.object(collab, "flag_enabled", return_value=True),
    ):
        assert collab._is_collaboration_enabled(_app(), "1") == (True, None)
        assert collab._is_collaboration_enabled(
            _app(license_client=_Lic(True)), "1"
        ) == (True, None)
        assert collab._is_collaboration_enabled(
            _app(license_client=_Lic(False)), "1"
        ) == (False, "License validation failed")
        assert collab._is_collaboration_enabled(
            _app(license_client=_Lic(boom=True)), "1"
        ) == (True, None)
        bad_app = SimpleNamespace(
            extensions=MagicMock(get=MagicMock(side_effect=RuntimeError("x")))
        )
        assert collab._is_collaboration_enabled(bad_app, "1") == (True, None)
    with (
        patch.object(collab, "is_bypass_domain", return_value=True),
        patch.object(collab, "flag_enabled", return_value=False),
    ):
        assert collab._is_collaboration_enabled(_app(), "1") == (
            False,
            "Collaboration feature is not enabled",
        )
    with (
        patch.object(collab, "is_bypass_domain", return_value=True),
        patch.object(collab, "flag_enabled", side_effect=RuntimeError("ph")),
    ):
        assert collab._is_collaboration_enabled(_app(), "1") == (
            False,
            "Collaboration feature check unavailable",
        )


@pytest.mark.asyncio
async def test_ticket_endpoint(app, dw):  # noqa: F811
    """Ticket issuance: scope, flag, visibility, redis outages and success payload."""
    w = dw
    T = w["T"]
    s, d = await call(app, "post", DG, T(), json={"title": "Collab"})
    did = d["id"]
    url = f"{DG}/{did}/collab/ticket"
    assert (await call(app, "post", url))[0] == 401
    with patch.object(
        collab, "_is_collaboration_enabled", return_value=(False, "nope")
    ):
        s, d = await call(app, "post", url, T())
        assert s == 403 and d["error"] == "nope"
    with patch.object(collab, "_is_collaboration_enabled", return_value=(False, None)):
        s, d = await call(app, "post", url, T())
        assert s == 403 and "not enabled" in d["error"]
    with patch.object(collab, "_is_collaboration_enabled", return_value=(True, None)):
        assert (await call(app, "post", f"{DG}/99999999/collab/ticket", T()))[0] == 404
        assert (await call(app, "post", url, T("other")))[0] == 404
        assert (await call(app, "post", url, T("b")))[0] == 404
        s, d = await call(app, "post", url, T())
        assert s == 200 and d["ticket"] in d["ws_path"]
        stored = json.loads(
            app.extensions["module_redis"].get(f"elder:dg:ticket:{d['ticket']}")
        )
        assert stored["permission"] == "editor" and stored["diagram_id"] == did

        # viewer share -> viewer permission
        def _share():
            """Insert a share row granting access to the second identity.
            Used only to set up this test's data."""
            n = now()
            app.db.dg_shares.insert(
                tenant_id=w["a"]["tenant"],
                diagram_id=did,
                shared_with_identity_id=w["other"],
                shared_by_identity_id=w["a"]["user"],
                permission="viewer",
                created_at=n,
                updated_at=n,
            )
            app.db.commit()

        await in_db(_share)
        s, d = await call(app, "post", url, T("other"))
        assert s == 200
        stored = json.loads(
            app.extensions["module_redis"].get(f"elder:dg:ticket:{d['ticket']}")
        )
        assert stored["permission"] == "viewer"
        # redis outages
        real = app.extensions["module_redis"]
        app.extensions["module_redis"] = None
        try:
            assert (await call(app, "post", url, T()))[0] == 503
            app.extensions["module_redis"] = MagicMock(
                setex=MagicMock(side_effect=RuntimeError("down"))
            )
            assert (await call(app, "post", url, T()))[0] == 500
        finally:
            app.extensions["module_redis"] = real


class _Harness:
    """Open collaboration WebSockets against the real handler and record close codes."""

    def __init__(self, app, w, did):
        """Remember the app, world and diagram id."""
        self.app, self.w, self.did = app, w, did
        self.closes: list[int] = []

    def origin(self):
        """Return an allowed CORS origin for the handshake."""
        o = self.app.config["CORS_ORIGINS"]
        return o if isinstance(o, str) else next(iter(o))

    def put_ticket(self, permission="editor", diagram_id=None, raw=None, ident=None):
        """Store a ticket in Redis and return its token string."""
        t = secrets.token_urlsafe(16)
        data = (
            raw
            if raw is not None
            else json.dumps(
                {
                    "diagram_id": diagram_id or self.did,
                    "identity_id": ident or self.w["a"]["user"],
                    "tenant_id": self.w["a"]["tenant"],
                    "permission": permission,
                }
            )
        )
        self.app.extensions["module_redis"].setex(f"elder:dg:ticket:{t}", 60, data)
        return t

    def connect(self, query):
        """Return the websocket context manager for ``query`` (e.g. ``ticket=..``)."""
        return self.app.test_client().websocket(
            f"{DG}/{self.did}/collab/ws?{query}", headers={"Origin": self.origin()}
        )


async def _expect_close(h, query, code):
    """Connect with ``query`` and assert the server closes with ``code``."""
    done = asyncio.Event()
    codes: list[int] = []
    orig = Websocket.close

    async def spy(self, c, reason=""):
        """Record the close code, then delegate."""
        codes.append(c)
        try:
            await orig(self, c, reason)
        finally:
            done.set()

    with patch.object(Websocket, "close", spy):
        try:
            async with h.connect(query) as ws:
                await asyncio.wait_for(done.wait(), 10)
        except (
            BaseException
        ) as exc:  # teardown may surface the handler's CancelledError group
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
    assert code in codes, codes


@pytest.mark.asyncio
async def test_ws_rejections(app, dw):  # noqa: F811
    """Every pre-accept failure closes the socket with the right code."""
    w = dw
    s, d = await call(app, "post", DG, w["T"](), json={"title": "WS"})
    h = _Harness(app, w, d["id"])
    await _expect_close(h, "", 1008)  # no ticket
    await _expect_close(h, "ticket=unknown", 1008)  # unknown ticket
    await _expect_close(
        h, f"ticket={h.put_ticket(raw='{not json')}", 1008
    )  # unparsable
    await _expect_close(
        h, f"ticket={h.put_ticket(diagram_id=h.did + 1000)}", 1008
    )  # path mismatch
    real = app.extensions["module_redis"]
    try:
        app.extensions["module_redis"] = None
        await _expect_close(h, "ticket=x", 1011)
        app.extensions["module_redis"] = MagicMock(
            getdel=MagicMock(side_effect=RuntimeError("down"))
        )
        await _expect_close(h, "ticket=x", 1008)
    finally:
        app.extensions["module_redis"] = real
    # session registration failure
    t = h.put_ticket()
    calls = {"n": 0}
    real_run = collab.run_in_threadpool

    async def flaky(fn, *a, **k):
        """Fail the first (registration) threadpool call, delegate the rest."""
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db down")
        return await real_run(fn, *a, **k)

    with patch.object(collab, "run_in_threadpool", flaky):
        await _expect_close(h, f"ticket={t}", 1011)


@pytest.mark.asyncio
async def test_ws_editor_session(app, dw):  # noqa: F811
    """An editor receives collaborators, can send cursor/op/presence, and sees peer broadcasts."""
    w = dw
    s, d = await call(app, "post", DG, w["T"](), json={"title": "WS2"})
    h = _Harness(app, w, d["id"])
    rc = app.extensions["module_redis"]
    sub = rc.pubsub()
    sub.subscribe(f"elder:dg:{h.did}")
    t = h.put_ticket("editor")
    async with h.connect(f"ticket={t}") as ws:
        first = json.loads(await asyncio.wait_for(ws.receive(), 10))
        assert first["type"] == "collaborators" and len(first["users"]) == 1
        await ws.send("not json")
        await ws.send(json.dumps({"type": "cursor", "x": 3, "y": 4}))
        await ws.send(json.dumps({"type": "cursor", "x": 3}))
        await ws.send(json.dumps({"type": "op", "payload": {"a": 1}}))
        await ws.send(json.dumps({"type": "drawing_change", "payload": {"b": 2}}))
        await ws.send(json.dumps({"type": "unknown"}))
        await ws.send(json.dumps({"type": "presence"}))
        reply = json.loads(await asyncio.wait_for(ws.receive(), 10))
        assert reply["type"] == "collaborators"
        # a peer message (different sender) is forwarded; our own echo is not
        peer = json.dumps(
            {"type": "cursor_moved", "sender_session": "peer", "x": 1, "y": 2}
        )
        for _ in range(20):
            rc.publish(f"elder:dg:{h.did}", peer)
            try:
                got = json.loads(await asyncio.wait_for(ws.receive(), 0.5))
            except TimeoutError:
                continue
            if got.get("type") == "cursor_moved":
                assert got["sender_session"] == "peer"
                break
        else:
            pytest.fail("peer broadcast never forwarded")
        rc.publish(f"elder:dg:{h.did}", "not-json")
    await asyncio.sleep(0.3)
    types = []
    while True:
        m = sub.get_message(timeout=0.2)
        if m is None:
            break
        if m["type"] == "message":
            try:
                types.append(json.loads(m["data"])["type"])
            except ValueError:
                pass
    assert {"user_joined", "cursor_moved", "drawing_changed", "user_left"} <= set(types)

    def _sessions():
        """Return (is_active, cursor_x) per collaboration session.
        Used only to set up this test's data."""
        rows = app.db(app.db.dg_collaboration_sessions.diagram_id == h.did).select()
        return [(r.is_active, r.last_cursor_x) for r in rows]

    rows = await in_db(_sessions)
    assert rows and rows[0][0] is False and rows[0][1] == 3
    sub.close()


@pytest.mark.asyncio
async def test_ws_viewer_cannot_draw(app, dw):  # noqa: F811
    """A viewer's drawing ops are rejected and never broadcast; cursors still work."""
    w = dw
    s, d = await call(app, "post", DG, w["T"](), json={"title": "WS3"})
    h = _Harness(app, w, d["id"])
    rc = app.extensions["module_redis"]
    sub = rc.pubsub()
    sub.subscribe(f"elder:dg:{h.did}")
    t = h.put_ticket("viewer")
    async with h.connect(f"ticket={t}") as ws:
        await asyncio.wait_for(ws.receive(), 10)
        await ws.send(json.dumps({"type": "op", "payload": {"x": 1}}))
        await ws.send(json.dumps({"type": "cursor", "x": 1, "y": 1}))
        await ws.send(json.dumps({"type": "presence"}))
        assert (
            json.loads(await asyncio.wait_for(ws.receive(), 10))["type"]
            == "collaborators"
        )
    await asyncio.sleep(0.3)
    types = []
    while (m := sub.get_message(timeout=0.2)) is not None:
        if m["type"] == "message":
            types.append(json.loads(m["data"])["type"])
    assert "drawing_changed" not in types and "cursor_moved" in types
    sub.close()


class _FlakyRedis:
    """Real Redis client wrapper whose ``publish`` always fails."""

    def __init__(self, real):
        """Wrap the real client."""
        self._real = real

    def publish(self, *a, **k):
        """Fail like a broken Redis connection."""
        raise RuntimeError("publish down")

    def __getattr__(self, name):
        """Delegate everything else to the real client."""
        return getattr(self._real, name)


async def _open_basic(h, ticket_perm="editor"):
    """Return a ticket string for a fresh connection."""
    return h.put_ticket(ticket_perm)


@pytest.mark.asyncio
async def test_ws_publish_failures_are_tolerated(app, dw):  # noqa: F811
    """Redis publish errors never break the socket; cleanup still completes."""
    w = dw
    s, d = await call(app, "post", DG, w["T"](), json={"title": "WS4"})
    h = _Harness(app, w, d["id"])
    t = h.put_ticket("editor")
    real = app.extensions["module_redis"]
    app.extensions["module_redis"] = _FlakyRedis(real)
    try:
        async with h.connect(f"ticket={t}") as ws:
            first = json.loads(await asyncio.wait_for(ws.receive(), 10))
            assert first["type"] == "collaborators"
            await ws.send(json.dumps({"type": "cursor", "x": 1, "y": 2}))
            await ws.send(json.dumps({"type": "presence"}))
            assert (
                json.loads(await asyncio.wait_for(ws.receive(), 10))["type"]
                == "collaborators"
            )
        await asyncio.sleep(0.3)
    finally:
        app.extensions["module_redis"] = real

    def _active():
        """Return the active flags of the diagram's sessions.
        Used only to set up this test's data."""
        return [
            r.is_active
            for r in app.db(
                app.db.dg_collaboration_sessions.diagram_id == h.did
            ).select()
        ]

    assert await in_db(_active) == [False]


@pytest.mark.asyncio
async def test_ws_db_failures_after_registration(app, dw):  # noqa: F811
    """Failures in later threadpool calls (fetch, cursor update, cleanup) are logged, not fatal."""
    w = dw
    s, d = await call(app, "post", DG, w["T"](), json={"title": "WS5"})
    h = _Harness(app, w, d["id"])
    t = h.put_ticket("editor")
    real_run = collab.run_in_threadpool
    calls = {"n": 0}

    async def flaky(fn, *a, **k):
        """Let registration succeed, fail every later threadpool call."""
        calls["n"] += 1
        if calls["n"] == 1:
            return await real_run(fn, *a, **k)
        raise RuntimeError("db down")

    with patch.object(collab, "run_in_threadpool", flaky):
        async with h.connect(f"ticket={t}") as ws:
            first = json.loads(await asyncio.wait_for(ws.receive(), 10))
            assert first == {"type": "collaborators", "users": []}
            await ws.send(json.dumps({"type": "cursor", "x": 1, "y": 2}))
            await ws.send(json.dumps({"type": "presence"}))
            assert json.loads(await asyncio.wait_for(ws.receive(), 10))["users"] == []
        await asyncio.sleep(0.3)
    assert calls["n"] >= 4


@pytest.mark.asyncio
async def test_ws_send_failures_and_odd_frames(app, dw):  # noqa: F811
    """Server-side send errors, empty frames and non-object frames end handlers cleanly."""
    w = dw
    s, d = await call(app, "post", DG, w["T"](), json={"title": "WS6"})
    h = _Harness(app, w, d["id"])
    rc = app.extensions["module_redis"]

    async def boom(self, *a, **k):
        """Fail every server-side websocket send."""
        raise RuntimeError("send failed")

    t = h.put_ticket("editor")
    with patch.object(Websocket, "send", boom):
        async with h.connect(f"ticket={t}") as ws:
            await ws.send(json.dumps({"type": "presence"}))
            await asyncio.sleep(0.3)
            for _ in range(5):
                rc.publish(
                    f"elder:dg:{h.did}",
                    json.dumps({"type": "cursor_moved", "sender_session": "peer"}),
                )
                await asyncio.sleep(0.2)
    await asyncio.sleep(0.2)
    # empty frame stops the client handler; a non-object frame trips its error handler
    t = h.put_ticket("editor")
    async with h.connect(f"ticket={t}") as ws:
        await asyncio.wait_for(ws.receive(), 10)
        await ws.send("")
        await asyncio.sleep(0.2)
    t = h.put_ticket("editor")
    async with h.connect(f"ticket={t}") as ws:
        await asyncio.wait_for(ws.receive(), 10)
        await ws.send("[1, 2]")
        await asyncio.sleep(0.2)
    # non-JSON and non-message traffic on the channel is ignored
    t = h.put_ticket("editor")
    async with h.connect(f"ticket={t}") as ws:
        await asyncio.wait_for(ws.receive(), 10)
        for _ in range(5):
            rc.publish(f"elder:dg:{h.did}", "not-json")
            await asyncio.sleep(0.15)
    # redis subscriber setup failure only ends that coroutine
    t = h.put_ticket("editor")
    with patch.object(
        collab.aioredis, "from_url", side_effect=RuntimeError("no redis")
    ):
        async with h.connect(f"ticket={t}") as ws:
            first = json.loads(await asyncio.wait_for(ws.receive(), 10))
            assert first["type"] == "collaborators"
            await ws.send(json.dumps({"type": "presence"}))
            assert (
                json.loads(await asyncio.wait_for(ws.receive(), 10))["type"]
                == "collaborators"
            )
