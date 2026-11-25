"""HTTP route tests for the streams webhook and node-metadata endpoints.

Real Quart app, JWT verification and Postgres.  Covers owner / editor /
viewer / outsider authorization, tenant isolation, token redaction for
read-only callers and the full webhook lifecycle.
"""

# flake8: noqa: E501
# ruff: noqa: F811

import uuid

import pytest

from tests.unit._cov_d_helpers import call, in_db, world  # noqa: F401
from tests.unit.test_cov_d_strbk_streams_routes import (  # noqa: F401
    _insert_stream,
    sw,
)

BASE = "/api/v1/streams"


@pytest.mark.asyncio
class TestNodeMetadata:
    """GET/PUT node metadata."""

    async def test_get_defaults_and_denials(self, app, sw):
        """Unset metadata returns an empty shape; unreadable/missing streams 404."""
        sid = sw["sid"]
        url = f"{BASE}/{sid}/nodes/n1/metadata"
        st, body = await call(app, "get", url, sw["tok"]())
        assert st == 200 and body == {"node_id": "n1", "comments": None, "metadata": {}}
        assert (await call(app, "get", url, sw["tok"]("viewer")))[0] == 200
        assert (await call(app, "get", url, sw["tok"]("outsider")))[0] == 404
        assert (await call(app, "get", url, sw["tok"]("buser")))[0] == 404
        assert (
            await call(app, "get", f"{BASE}/999999999/nodes/n1/metadata", sw["tok"]())
        )[0] == 404
        assert (await call(app, "get", url))[0] == 401

    async def test_put_creates_then_updates(self, app, sw):
        """PUT inserts on first write, updates after, and enforces edit rights."""
        sid = sw["sid"]
        url = f"{BASE}/{sid}/nodes/nx/metadata"
        st, body = await call(
            app,
            "put",
            url,
            sw["tok"]("editor"),
            json={"comments": "hi", "metadata": {"a": 1}},
        )
        assert st == 200 and body["comments"] == "hi" and body["metadata"] == {"a": 1}
        st, body = await call(app, "get", url, sw["tok"]())
        assert body["comments"] == "hi" and body["updated_at"]
        st, body = await call(app, "put", url, sw["tok"](), json={"comments": None})
        assert st == 200 and body["metadata"] == {}
        st, body = await call(app, "get", url, sw["tok"]())
        assert body["comments"] is None and body["metadata"] == {}
        assert (await call(app, "put", url, sw["tok"]("viewer"), json={}))[0] == 403
        assert (await call(app, "put", url, sw["tok"]("buser"), json={}))[0] == 404
        assert (
            await call(
                app, "put", f"{BASE}/999999999/nodes/nx/metadata", sw["tok"](), json={}
            )
        )[0] == 404
        assert (
            await call(app, "put", url, sw["tok"](scopes=["streams:read"]), json={})
        )[0] == 403

    async def test_get_node_without_updated_at(self, app, sw):
        """A metadata row with a NULL updated_at serialises as None."""
        db = sw["db"]
        sid = sw["sid"]

        def seed():
            """Insert a metadata row lacking timestamps."""
            db.stream_node_metadata.insert(
                tenant_id=sw["tenant"],
                playbook_id=sid,
                node_id="nt",
                comments="c",
                metadata_json=None,
                updated_by_identity_id=sw["owner"],
            )
            db.commit()

        await in_db(seed)
        st, body = await call(
            app, "get", f"{BASE}/{sid}/nodes/nt/metadata", sw["tok"]()
        )
        assert st == 200 and body["updated_at"] is None and body["metadata"] == {}


@pytest.mark.asyncio
class TestWebhooks:
    """Webhook list/create/delete."""

    async def test_lifecycle(self, app, sw):
        """Create, list (token redaction), and delete a webhook."""
        sid = sw["sid"]
        url = f"{BASE}/{sid}/webhooks"
        st, body = await call(app, "get", url, sw["tok"]())
        assert st == 200 and body == []
        st, body = await call(app, "post", url, sw["tok"]("editor"), json={})
        assert (
            st == 201
            and body["url"].startswith("/api/v1/hooks/")
            and body["allowed_methods"] == ["POST"]
        )
        wid = body["id"]
        st, body2 = await call(
            app,
            "post",
            url,
            sw["tok"](),
            json={
                "name": "named",
                "allowed_methods": ["GET"],
                "validate_signature": True,
                "signature_secret": "x" + uuid.uuid4().hex,
            },
        )
        assert (
            st == 201
            and body2["name"] == "named"
            and body2["validate_signature"] is True
        )
        st, items = await call(app, "get", url, sw["tok"]())
        assert len(items) == 2 and all(i["token"] for i in items)
        assert any(i["allowed_methods"] == ["GET"] for i in items)
        st, items = await call(app, "get", url, sw["tok"]("viewer"))
        assert all(i["token"] is None and i["url"] is None for i in items)
        assert (await call(app, "get", url, sw["tok"]("outsider")))[0] == 404
        assert (await call(app, "get", f"{BASE}/999999999/webhooks", sw["tok"]()))[
            0
        ] == 404
        # deletes
        assert (await call(app, "delete", f"{url}/{wid}", sw["tok"]("viewer")))[
            0
        ] == 403
        assert (await call(app, "delete", f"{url}/{wid}", sw["tok"]("buser")))[0] == 404
        assert (
            await call(app, "delete", f"{BASE}/999999999/webhooks/{wid}", sw["tok"]())
        )[0] == 404
        assert (await call(app, "delete", f"{url}/{wid}", sw["tok"]()))[0] == 200
        assert (await call(app, "delete", f"{url}/{wid}", sw["tok"]()))[0] == 404

    async def test_create_denied(self, app, sw):
        """Viewers, strangers and other tenants cannot create webhooks."""
        sid = sw["sid"]
        url = f"{BASE}/{sid}/webhooks"
        assert (await call(app, "post", url, sw["tok"]("viewer"), json={}))[0] == 403
        assert (await call(app, "post", url, sw["tok"]("buser"), json={}))[0] == 404
        assert (
            await call(app, "post", f"{BASE}/999999999/webhooks", sw["tok"](), json={})
        )[0] == 404
