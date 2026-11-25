"""Shared seeding, token and HTTP helpers for the slice-C coverage tests.

Everything writes real rows to the test Postgres and mints real, typed JWTs
signed with the application's key. Only external boundaries are faked by the
individual test modules.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from werkzeug.security import generate_password_hash


def fake_secret(*parts: str) -> str:
    """Build a credential-looking value at runtime so no literal secret is committed."""
    return "-".join(("covc", *parts, uuid.uuid4().hex[:6]))


def sfx() -> str:
    """Return a short unique suffix for row names."""
    return uuid.uuid4().hex[:8]


def mk_tenant(db: Any, label: str = "t") -> int:
    """Insert an active tenant and return its id."""
    s = sfx()
    tid = db.tenants.insert(
        name=f"covc-{label}-{s}", slug=f"covc-{label}-{s}", is_active=True
    )
    db.commit()
    return tid


def mk_identity(
    db: Any,
    tenant_id: int,
    role: str = "admin",
    superuser: bool = False,
    password: str | None = None,
    active: bool = True,
    **extra: Any,
) -> int:
    """Insert a local human identity in ``tenant_id`` and return its id."""
    name = f"covc-{role}-{sfx()}"
    fields: dict[str, Any] = dict(
        username=name,
        email=f"{name}@example.test",
        identity_type="human",
        auth_provider="local",
        password_hash=generate_password_hash(password) if password else None,
        is_active=active,
        is_superuser=superuser,
        mfa_enabled=False,
        must_change_password=False,
        portal_role=role,
        tenant_id=tenant_id,
    )
    fields.update(extra)
    iid = db.identities.insert(**fields)
    db.commit()
    return iid


def mk_org(
    db: Any, tenant_id: int, name: str | None = None, parent_id: int | None = None
) -> int:
    """Insert an organization in ``tenant_id``."""
    now = datetime.now(UTC)
    oid = db.organizations.insert(
        name=name or f"covc-org-{sfx()}",
        tenant_id=tenant_id,
        parent_id=parent_id,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return oid


def mk_entity(
    db: Any, org_id: int, name: str | None = None, etype: str = "compute"
) -> int:
    """Insert an entity in ``org_id``."""
    now = datetime.now(UTC)
    eid = db.entities.insert(
        name=name or f"covc-ent-{sfx()}",
        type=etype,
        organization_id=org_id,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return eid


def mk_dep(db: Any, tenant_id: int, src: int, dst: int) -> int:
    """Insert an entity->entity dependency recorded for ``tenant_id``."""
    now = datetime.now(UTC)
    did = db.dependencies.insert(
        tenant_id=tenant_id,
        source_type="entity",
        source_id=src,
        target_type="entity",
        target_id=dst,
        dependency_type="depends_on",
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return did


def token(
    app: Any,
    identity_id: int | str,
    tenant_id: int | str,
    scopes: list[str] | None = None,
    ttype: str = "access",
    **extra: Any,
) -> str:
    """Mint a typed JWT signed with the app key (timestamps built at call time)."""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(identity_id),
        "type": ttype,
        "iat": now,
        "exp": now + timedelta(hours=1),
        "scope": list(scopes or []),
        "tenant": str(tenant_id),
        "iss": app.config.get("JWT_ISSUER", "elder-api"),
        "aud": app.config.get("JWT_AUDIENCE", "elder-api"),
        "roles": ["admin"],
        "teams": [],
    }
    payload.update(extra)
    secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
    return jwt.encode(
        payload, secret, algorithm=app.config.get("JWT_ALGORITHM", "HS256")
    )


async def call(app: Any, method: str, url: str, tok: str | None = None, **kw: Any):
    """Issue a request through the Quart test client; return ``(status, json|None)``."""
    client = app.test_client()
    headers = dict(kw.pop("headers", {}) or {})
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    resp = await getattr(client, method)(url, headers=headers, **kw)
    body = await resp.get_data()
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    return resp.status_code, data
