"""Edge-path tests for the diagram routes: missing tenant and missing identity claims.

A token whose tenant claim is zero reaches each handler with a falsy tenant id;
a handler-local ``g`` without ``identity_id`` exercises the identity guards.
Everything else runs through the real Quart app and Postgres.
"""

# flake8: noqa: E501

import importlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.unit._cov_d_helpers import call, world  # noqa: F401
from tests.unit.test_cov_d_diginf_dg_core import dw, tok  # noqa: F401

DG = "/api/v1/diagrams"
COL = "/api/v1/diagram-collections"
TPL = "/api/v1/diagram-templates"
LIB = "/api/v1/diagram-libraries"
ST = "/api/v1/diagram-storage"
R = "apps.api.modules.diagrams.routes."

# (module, method, url, json, identity_required)
ROUTES = [
    ("diagrams", "get", f"{DG}", None, False),
    ("diagrams", "post", f"{DG}", {"title": "t"}, True),
    ("diagrams", "get", f"{DG}/1", None, False),
    ("diagrams", "patch", f"{DG}/1", {"title": "t"}, False),
    ("diagrams", "delete", f"{DG}/1", None, False),
    ("diagrams", "post", f"{DG}/1/versions", {"content": {}}, True),
    ("diagrams", "get", f"{DG}/1/versions", None, False),
    ("diagrams", "get", f"{DG}/1/versions/1", None, False),
    ("diagrams", "post", f"{DG}/1/versions/1/restore", {}, True),
    ("shares", "post", f"{DG}/1/shares", {}, True),
    ("shares", "get", f"{DG}/1/shares", None, False),
    ("shares", "delete", f"{DG}/1/shares/1", None, False),
    ("collections", "get", f"{COL}", None, False),
    ("collections", "post", f"{COL}", {"name": "n"}, True),
    ("collections", "get", f"{COL}/1", None, False),
    ("collections", "patch", f"{COL}/1", {"name": "n"}, False),
    ("collections", "delete", f"{COL}/1", None, False),
    ("collections", "post", f"{COL}/1/items", {"diagram_id": 1}, False),
    ("collections", "delete", f"{COL}/1/items/1", None, False),
    ("comments", "post", f"{DG}/1/comments", {"text_content": "x"}, True),
    ("comments", "get", f"{DG}/1/comments", None, False),
    ("comments", "post", f"{DG}/1/comments/1/replies", {"text_content": "x"}, True),
    ("comments", "patch", f"{DG}/1/comments/1", {}, True),
    ("comments", "delete", f"{DG}/1/comments/1", None, True),
    ("templates", "get", f"{TPL}", None, False),
    ("templates", "post", f"{TPL}", {"name": "n", "content": {}}, True),
    ("templates", "get", f"{TPL}/1", None, False),
    ("templates", "patch", f"{TPL}/1", {}, True),
    ("templates", "delete", f"{TPL}/1", None, True),
    ("libraries", "get", f"{LIB}", None, False),
    ("libraries", "post", f"{LIB}", {"name": "n"}, True),
    ("libraries", "get", f"{LIB}/1", None, False),
    ("libraries", "patch", f"{LIB}/1", {}, True),
    ("libraries", "delete", f"{LIB}/1", None, True),
    ("libraries", "post", f"{LIB}/1/shapes", {"name": "n"}, True),
    ("libraries", "delete", f"{LIB}/1/shapes/1", None, True),
    ("storage_providers", "get", f"{ST}", None, False),
    (
        "storage_providers",
        "post",
        f"{ST}",
        {"name": "n", "provider_type": "s3", "config_json": {}},
        True,
    ),
    ("storage_providers", "get", f"{ST}/1", None, False),
    ("storage_providers", "patch", f"{ST}/1", {}, True),
    ("storage_providers", "delete", f"{ST}/1", None, True),
    ("storage_providers", "post", f"{ST}/1/test", None, True),
    ("collab", "post", f"{DG}/1/collab/ticket", None, True),
    ("export", "get", f"{DG}/1/export/json", None, False),
    ("export", "get", f"{DG}/1/export/svg", None, False),
]


def _kw(body):
    """Return call kwargs carrying a JSON body when one is given."""
    return {} if body is None else {"json": body}


@pytest.mark.asyncio
async def test_missing_tenant_is_forbidden(app, dw):  # noqa: F811
    """A zero tenant claim is refused with 403 on every diagram route."""
    t = tok(app, 0, dw["a"]["user"])
    for mod, method, url, body, _ in ROUTES:
        s, _d = await call(app, method, url, t, **_kw(body))
        assert s == 403, (method, url, s)


@pytest.mark.asyncio
async def test_missing_identity_is_forbidden(app, dw):  # noqa: F811
    """Handlers that need an identity refuse a claims set without ``identity_id``."""
    tid = dw["a"]["tenant"]
    t = dw["T"]()
    for mod, method, url, body, needs in ROUTES:
        if not needs:
            continue
        m = importlib.import_module(R + mod)
        fake = SimpleNamespace(claims={"tenant": str(tid)})
        with patch.object(m, "g", fake), patch(R + "diagrams.g", fake):
            if mod == "collab":
                s, _d = await call(app, method, url, t, **_kw(body))
            else:
                s, _d = await call(app, method, url, t, **_kw(body))
        assert s == 403, (mod, method, url, s)


def test_get_tenant_id_parsing():
    """_get_tenant_id tolerates empty, non-numeric and numeric tenant claims."""
    from apps.api.modules.diagrams.routes import diagrams as dgm

    for claims, want in [
        ({}, None),
        ({"tenant": ""}, None),
        ({"tenant": "abc"}, None),
        ({"tenant": None}, None),
        ({"tenant": "7"}, 7),
    ]:
        with patch.object(dgm, "g", SimpleNamespace(claims=claims)):
            assert dgm._get_tenant_id() == want
    with patch.object(dgm, "g", SimpleNamespace(claims=None)):
        assert dgm._get_tenant_id() is None


def test_can_read_edge_cases():
    """Visibility helpers refuse cross-tenant rows and anonymous callers on private rows."""
    from apps.api.modules.diagrams.routes import collections as cm
    from apps.api.modules.diagrams.routes import diagrams as dgm
    from apps.api.modules.diagrams.routes import libraries as lm
    from apps.api.modules.diagrams.routes import templates as tm

    row = SimpleNamespace(
        tenant_id=1,
        is_public=False,
        owner_identity_id=5,
        created_by_identity_id=5,
        id=1,
    )
    assert (
        dgm._can_read_diagram(None, SimpleNamespace(tenant_id=2, is_public=True), 1)
        is False
    )
    assert dgm._can_read_diagram(None, row, 1, None) is False
    assert dgm._can_edit_diagram(None, row, 1, None) is False
    assert dgm._can_edit_diagram(None, SimpleNamespace(tenant_id=2), 1, 5) is False
    assert (
        cm._can_read_collection(None, SimpleNamespace(tenant_id=2, is_public=True), 1)
        is False
    )
    assert cm._can_read_collection(None, row, 1, None) is False
    assert (
        lm._can_read_library(None, SimpleNamespace(tenant_id=2, is_public=True), 1)
        is False
    )
    assert lm._can_read_library(None, row, 1, None) is False
    assert lm._can_read_library(None, row, 1, 9) is False
    assert (
        tm._can_read_template(None, SimpleNamespace(tenant_id=2, is_public=True), 1)
        is False
    )
    assert tm._can_read_template(None, row, 1, None) is False
    assert tm._can_read_template(None, row, 1, 9) is False
