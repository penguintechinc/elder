"""Public webhook-hook endpoint tests (``/api/v1/hooks/<token>``).

Real Quart app and Postgres.  Request body parsing (JSON / form / query /
malformed), method gating, HMAC signature handling, the ``/test`` probe and
job-bus enqueue failure tolerance (Redis is the external boundary).
"""

# flake8: noqa: E501
# ruff: noqa: F811

import hashlib
import hmac
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from apps.api.modules.streams.routes import hooks
from tests.unit._cov_d_helpers import call, in_db, now, world  # noqa: F401
from tests.unit.test_cov_d_strbk_streams_routes import _insert_stream, sw  # noqa: F401

BASE = "/api/v1/hooks"


def _sig(body: bytes, secret: str) -> str:
    """Return a valid sha256= signature header value."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _mk_hook(
    sw_, enabled=True, stream_enabled=True, methods=None, secret=None, validate=False
):
    """Create a stream + webhook pair and return the token."""
    db = sw_["db"]
    token = "t-" + uuid.uuid4().hex

    def seed():
        """Insert the playbook and its webhook."""
        sid = _insert_stream(db, sw_["tenant"], sw_["owner"], is_enabled=stream_enabled)
        ts = now()
        db.stream_webhooks.insert(
            tenant_id=sw_["tenant"],
            playbook_id=sid,
            name="h",
            token=token,
            allowed_methods=methods,
            validate_signature=validate,
            signature_secret=secret,
            is_enabled=enabled,
            created_at=ts,
            updated_at=ts,
        )
        db.commit()

    await in_db(seed)
    return token


@pytest.mark.asyncio
class TestTrigger:
    """POST/GET trigger behaviour."""

    async def test_unknown_disabled_and_inactive_are_uniform_404(self, app, sw):
        """Unknown tokens, disabled hooks and disabled streams all 404 identically."""
        st, body = await call(app, "post", f"{BASE}/nope-{uuid.uuid4().hex}")
        assert st == 404 and body == {"error": "Not found"}
        for kw in ({"enabled": False}, {"stream_enabled": False}):
            tok = await _mk_hook(sw, **kw)
            st, b = await call(app, "post", f"{BASE}/{tok}")
            assert (st, b) == (404, {"error": "Not found"})

    async def test_payload_shapes_and_methods(self, app, sw):
        """JSON, form, query and malformed payloads queue; other methods are gated."""
        with patch("apps.worker.config.settings.settings.redis_url", ""):
            tok = await _mk_hook(sw, methods=["POST", "GET"])
            st, body = await call(app, "post", f"{BASE}/{tok}", json={"a": 1})
            assert st == 202 and body["status"] == "queued"
            st, _ = await call(app, "post", f"{BASE}/{tok}", data={"f": "1"})
            assert st == 202
            st, _ = await call(app, "get", f"{BASE}/{tok}?q=1")
            assert st == 202
            st, _ = await call(
                app,
                "post",
                f"{BASE}/{tok}",
                data=b"{bad",
                headers={"Content-Type": "application/json"},
            )
            assert st == 202
            st, body = await call(app, "put", f"{BASE}/{tok}")
            assert st == 405 and "Allowed: POST, GET" in body["error"]
            st, _ = await call(app, "post", f"{BASE}/{await _mk_hook(sw)}")
            assert st == 202

    async def test_signature_enforcement(self, app, sw):
        """Signed hooks fail closed without secret, header or a valid digest."""
        secret = "s" + uuid.uuid4().hex
        body = b'{"x":1}'
        with patch("apps.worker.config.settings.settings.redis_url", ""):
            tok = await _mk_hook(sw, secret=secret, validate=True)
            h = {"Content-Type": "application/json"}
            assert (await call(app, "post", f"{BASE}/{tok}", data=body, headers=h))[
                0
            ] == 401
            assert (
                await call(
                    app,
                    "post",
                    f"{BASE}/{tok}",
                    data=body,
                    headers={**h, "X-Hub-Signature-256": "sha256=bad"},
                )
            )[0] == 401
            assert (
                await call(
                    app,
                    "post",
                    f"{BASE}/{tok}",
                    data=body,
                    headers={**h, "X-Signature-256": _sig(body, secret)},
                )
            )[0] == 202
            nosec = await _mk_hook(sw, secret=None, validate=True)
            assert (await call(app, "post", f"{BASE}/{nosec}", data=body, headers=h))[
                0
            ] == 401

    async def test_enqueue_paths(self, app, sw):
        """Enqueue success is quiet; a Redis failure never fails the request."""
        tok = await _mk_hook(sw)
        with (
            patch("shared.jobbus.JobBus.ensure_group", new=AsyncMock()),
            patch("shared.jobbus.JobBus.enqueue", new=AsyncMock()) as enq,
            patch(
                "apps.worker.config.settings.settings.redis_url",
                "redis://localhost:1/0",
            ),
            patch("redis.asyncio.from_url") as fu,
        ):
            fu.return_value.close = AsyncMock()
            st, _ = await call(app, "post", f"{BASE}/{tok}", json={})
            assert st == 202 and enq.await_count == 1
        with (
            patch(
                "shared.jobbus.JobBus.ensure_group",
                new=AsyncMock(side_effect=RuntimeError("down")),
            ),
            patch(
                "apps.worker.config.settings.settings.redis_url",
                "redis://localhost:1/0",
            ),
        ):
            st, _ = await call(app, "post", f"{BASE}/{tok}", json={})
            assert st == 202


@pytest.mark.asyncio
class TestProbe:
    """The side-effect-free /test endpoint."""

    async def test_probe(self, app, sw):
        """Unknown tokens 404; known hooks report signature validity."""
        assert (await call(app, "get", f"{BASE}/nope-{uuid.uuid4().hex}/test"))[
            0
        ] == 404
        tok = await _mk_hook(sw)
        st, body = await call(app, "get", f"{BASE}/{tok}/test")
        assert (
            st == 200
            and body["signature_valid"] is None
            and body["request"]["has_body"] is False
        )
        secret = "s" + uuid.uuid4().hex
        signed = await _mk_hook(sw, secret=secret, validate=True)
        payload = b"abc"
        st, body = await call(
            app,
            "post",
            f"{BASE}/{signed}/test",
            data=payload,
            headers={"X-Hub-Signature-256": _sig(payload, secret)},
        )
        assert body["signature_valid"] is True and body["request"]["has_body"] is True
        st, body = await call(app, "post", f"{BASE}/{signed}/test", data=payload)
        assert body["signature_valid"] is False
        nosec = await _mk_hook(sw, secret=None, validate=True)
        st, body = await call(
            app,
            "post",
            f"{BASE}/{nosec}/test",
            data=payload,
            headers={"X-Hub-Signature-256": "sha256=zz"},
        )
        assert body["signature_valid"] is False


def test_verify_hmac_signature():
    """Only well-formed, matching signatures verify."""
    assert not hooks.verify_hmac_signature(b"x", "", "k")
    assert not hooks.verify_hmac_signature(b"x", "md5=ab", "k")
    assert hooks.verify_hmac_signature(b"x", _sig(b"x", "k"), "k")
    assert not hooks.verify_hmac_signature(b"y", _sig(b"x", "k"), "k")
