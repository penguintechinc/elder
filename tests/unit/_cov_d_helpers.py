"""Shared fixtures/helpers for the slice-D coverage tests.

Slice D covers the sbom, issues, flows, documents and pages API modules
plus ``apps/api/services/sbom``.  Route tests go through the real Quart app,
real JWT verification and the real Postgres schema; only true external
boundaries (HTTP to NVD/OSV, worker queues) are faked by individual tests.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest_asyncio

from apps.api.utils.async_utils import run_in_threadpool


def fake_secret(*parts: str) -> str:
    """Build a credential-looking value at runtime so no literal secret is committed."""
    return "-".join(("covd", *parts, uuid.uuid4().hex[:6]))


def now() -> datetime:
    """Return the current UTC time (built inside tests, never at import)."""
    return datetime.now(UTC)


def vid() -> str:
    """Return a unique 25-char lowercase hex village id."""
    return uuid.uuid4().hex[:25]


def make_token(app: Any, tenant_id: int, identity_id: int, scopes: list[str]) -> str:
    """Mint a signed access JWT (explicit ``type``) for an identity."""
    issued = now()
    payload = {
        "sub": str(identity_id),
        "type": "access",
        "iat": issued,
        "exp": issued + timedelta(hours=1),
        "scope": list(scopes),
        "tenant": str(tenant_id),
        "roles": ["admin"],
        "teams": [],
    }
    secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
    return jwt.encode(payload, secret, algorithm="HS256")


async def call(app: Any, method: str, url: str, token: str | None = None, **kw: Any):
    """Issue a request through the app test client; return ``(status, json)``."""
    client = app.test_client()
    headers = dict(kw.pop("headers", {}) or {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = await getattr(client, method)(url, headers=headers, **kw)
    body = await resp.get_data()
    try:
        data = json.loads(body)
    except ValueError:
        data = body
    return resp.status_code, data


def _seed_side(db: Any, side: str, sfx: str, superuser: bool) -> dict[str, Any]:
    """Create tenant + org + identity for one side of the two-tenant world."""
    ts = now()
    tid = db.tenants.insert(
        name=f"cd-{side}-{sfx}", slug=f"cd-{side}-{sfx}", is_active=True
    )
    org = db.organizations.insert(
        name=f"cd-org-{side}-{sfx}", tenant_id=tid, created_at=ts, updated_at=ts
    )
    ident = db.identities.insert(
        identity_type="human",
        username=f"cd-user-{side}-{sfx}",
        email=f"cd-{side}-{sfx}@example.com",
        full_name=f"User {side}",
        tenant_id=tid,
        auth_provider="local",
        is_active=True,
        is_superuser=superuser,
        mfa_enabled=False,
        must_change_password=False,
        portal_role="observer",
        created_at=ts,
        updated_at=ts,
    )
    return {"tenant": tid, "org": org, "user": ident}


@pytest_asyncio.fixture
async def world(app: Any) -> dict[str, Any]:
    """Two isolated tenants A and B, each with an org and an identity.

    Returns a dict with ``a``/``b`` sub-dicts (tenant/org/user ids), plus
    ``tok(side, scopes)`` to mint tokens and ``sfx`` for unique names.
    """
    db = app.db
    sfx = uuid.uuid4().hex[:8]

    def _setup() -> dict[str, Any]:
        out = {
            "a": _seed_side(db, "a", sfx, False),
            "b": _seed_side(db, "b", sfx, False),
        }
        db.commit()
        return out

    w = await run_in_threadpool(_setup)
    w["sfx"] = sfx
    w["db"] = db

    def tok(side: str = "a", scopes: list[str] | None = None) -> str:
        s = w[side]
        return make_token(app, s["tenant"], s["user"], scopes or [])

    w["tok"] = tok
    return w


async def in_db(fn: Any) -> Any:
    """Run a sync DB callable in the threadpool and commit."""
    return await run_in_threadpool(fn)
