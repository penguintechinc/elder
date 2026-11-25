"""Unit tests for the portal authentication API (register, login, MFA, profile).

Real Quart app and test database. Registration and login use the real
``PortalAuthService``; MFA state changes use a mocked service so each route's
validation and error mapping can be asserted without a TOTP device.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import jwt
import pytest

BASE = "/api/v1/portal-auth"
MOD = "apps.api.api.v1.portal_auth"
PW = "Portal-Cov-Pass-1"


def _tenant(db) -> tuple[int, str]:
    """Insert a tenant and return ``(id, slug)``."""
    slug = f"covb-{uuid4().hex[:10]}"
    tid = db.tenants.insert(name="cov-b", slug=slug, is_active=True)
    db.commit()
    return tid, slug


def _portal_user(db, tid: int, **extra) -> int:
    """Insert a portal user directly (password unknown to callers)."""
    now = datetime.now(UTC)
    fields = dict(
        email=f"{uuid4().hex[:8]}@example.test",
        email_verified=True,
        is_active=True,
        tenant_id=tid,
        tenant_role="reader",
        created_at=now,
        updated_at=now,
    )
    fields.update(extra)
    uid = db.portal_users.insert(**fields)
    db.commit()
    return uid


def _hdr(
    app, sub: int, tid: int, global_role=None, tenant_role="reader", kind="portal_user"
) -> dict[str, str]:
    """Headers carrying a signed portal token of ``kind``."""
    now = datetime.now(UTC)
    claims = {
        "sub": str(sub),
        "email": "p@example.test",
        "tenant_id": tid,
        "tenant_role": tenant_role,
        "global_role": global_role,
        "roles": ["reader"],
        "scope": "",
        "type": kind,
        "principal_kind": "portal_user",
        "iat": now,
        "exp": now + timedelta(minutes=10),
    }
    secret = app.config["JWT_SECRET_KEY"] or app.config["SECRET_KEY"]
    tok = jwt.encode(claims, secret, algorithm=app.config["JWT_ALGORITHM"])
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def world(app):
    """Seed a tenant with one portal user."""
    tid, slug = _tenant(app.db)
    uid = _portal_user(app.db, tid)
    return {"tid": tid, "slug": slug, "uid": uid}


# ------------------------------------------------------------ small helpers


def test_helper_functions():
    """MFA code extraction and error mapping helpers."""
    from apps.api.api.v1.portal_auth import (
        _current_factor_code,
        _submitted_mfa_code,
    )

    assert _submitted_mfa_code({"mfaCode": " 123 "}) == "123"
    assert _submitted_mfa_code({"totp_code": 456}) == "456"
    assert _submitted_mfa_code({"code": True}) is None
    assert _submitted_mfa_code({"mfa_code": None, "code": "x"}) == "x"
    assert _submitted_mfa_code({"mfaCode": ["x"]}) is None
    assert _current_factor_code(None) is None
    assert _current_factor_code({"current_code": 12}) == "12"
    assert _current_factor_code({"totp_code": True, "code": "c"}) == "c"
    assert _current_factor_code({"current_code": "  "}) is None


@pytest.mark.asyncio
async def test_mfa_error_helpers(app):
    """MFA error bodies carry the right codes and statuses."""
    from apps.api.api.v1.portal_auth import _mfa_change_error, _mfa_error_response

    async with app.test_request_context("/"):
        resp, status = _mfa_error_response(
            {"error": "x", "locked": True, "status_code": 429}
        )
        assert status == 429 and (await resp.get_json())["errorCode"] == "MFA_LOCKED"
        resp, status = _mfa_error_response({"error": "x", "challenge_invalid": True})
        body = await resp.get_json()
        assert (
            status == 401
            and body["errorCode"] == "MFA_CHALLENGE_INVALID"
            and body["mfaChallengeInvalid"]
        )
        resp, _ = _mfa_error_response({"error": "x"})
        assert (await resp.get_json())["errorCode"] == "INVALID_MFA_CODE"
        resp, status = _mfa_change_error(
            {"error": "e", "mfa_code_required": True, "status_code": 403}
        )
        assert status == 403 and (await resp.get_json())["mfa_code_required"] is True
        resp, status = _mfa_change_error({"error": "e"})
        assert status == 400 and "mfa_code_required" not in await resp.get_json()


@pytest.mark.asyncio
async def test_auth_decorator_failures(app, async_client, world):
    """Missing, junk and wrong-type tokens are rejected."""
    assert (await async_client.get(f"{BASE}/me")).status_code == 401
    r = await async_client.get(f"{BASE}/me", headers={"Authorization": "Bearer junk"})
    assert r.status_code == 401
    bad = _hdr(app, world["uid"], world["tid"], kind="access")
    assert (await async_client.get(f"{BASE}/me", headers=bad)).status_code == 401


# ------------------------------------------------------ register / login


@pytest.mark.asyncio
async def test_register_paths(app, async_client, world):
    """Registration validates input and tenant, then creates the user."""
    R = f"{BASE}/register"
    assert (await async_client.post(R, json={})).status_code == 400
    r = await async_client.post(R, json={"email": "bad", "password": "x"})
    assert r.status_code == 422
    email = f"reg-{uuid4().hex[:8]}@example.test"
    r = await async_client.post(R, json={"email": email, "password": PW})
    assert (
        r.status_code == 400 and (await r.get_json())["errorCode"] == "INVALID_TENANT"
    )
    r = await async_client.post(
        R, json={"email": email, "password": PW, "tenant": "no-such-tenant"}
    )
    assert r.status_code == 400

    inactive, islug = _tenant(app.db)
    app.db(app.db.tenants.id == inactive).update(is_active=False)
    app.db.commit()
    r = await async_client.post(
        R, json={"email": email, "password": PW, "tenant_id": inactive}
    )
    assert r.status_code == 400 and (await r.get_json())["error"] == "Invalid tenant"
    r = await async_client.post(
        R, json={"email": email, "password": PW, "tenant": islug}
    )
    assert r.status_code == 400

    r = await async_client.post(
        R,
        json={
            "email": email,
            "password": PW,
            "tenant": world["slug"],
            "full_name": "R",
        },
    )
    assert r.status_code == 201, await r.get_data()
    r = await async_client.post(
        R, json={"email": email, "password": PW, "tenant": world["slug"]}
    )
    assert r.status_code in (400, 402)
    with patch(
        f"{MOD}.PortalAuthService.create_portal_user",
        return_value={"error": "limit", "status_code": 402},
    ):
        r = await async_client.post(
            R,
            json={
                "email": f"x-{uuid4().hex[:6]}@example.test",
                "password": PW,
                "tenant_id": world["tid"],
            },
        )
        assert (
            r.status_code == 402
            and (await r.get_json())["errorCode"] == "LIMIT_REACHED"
        )


@pytest.mark.asyncio
async def test_login_paths(app, async_client, world):
    """Login validates input and resolves tenants."""
    L = f"{BASE}/login"
    assert (await async_client.post(L, json={})).status_code == 400
    assert (
        await async_client.post(L, json={"email": "bad", "password": "x"})
    ).status_code == 422
    r = await async_client.post(
        L, json={"email": "u@example.test", "password": "x", "tenant_id": 99999999}
    )
    assert r.status_code in (400, 401)

    email = f"login-{uuid4().hex[:8]}@example.test"
    r = await async_client.post(
        f"{BASE}/register",
        json={"email": email, "password": PW, "tenant": world["slug"]},
    )
    assert r.status_code == 201
    r = await async_client.post(
        L, json={"email": email, "password": PW, "tenant": world["slug"]}
    )
    assert r.status_code == 200, await r.get_data()
    r = await async_client.post(
        L, json={"email": email, "password": "wrong", "tenant": world["slug"]}
    )
    assert r.status_code == 401
    r = await async_client.post(
        L, json={"email": email, "password": PW, "tenant": "no-such"}
    )
    assert r.status_code in (400, 401)
    with patch(
        f"{MOD}.PortalAuthService.authenticate",
        return_value={"error": "locked", "status_code": 423},
    ):
        r = await async_client.post(
            L, json={"email": email, "password": PW, "tenant_id": world["tid"]}
        )
        assert r.status_code in (401, 423)


# ------------------------------------------------------------------- MFA


@pytest.mark.asyncio
async def test_mfa_verify_route(app, async_client, world):
    """MFA verify validates the body and maps service errors."""
    V = f"{BASE}/mfa/verify"
    assert (await async_client.post(V, json=[])).status_code == 400
    assert (await async_client.post(V, json={"mfa_challenge": "c"})).status_code == 400
    assert (await async_client.post(V, json={"totp_code": "1"})).status_code == 400
    with patch(
        f"{MOD}.PortalAuthService.verify_mfa_challenge",
        return_value={"error": "bad", "challenge_invalid": True},
    ):
        r = await async_client.post(V, json={"mfaChallenge": "c", "mfaCode": "123456"})
        assert r.status_code == 401
    ok = {
        "id": world["uid"],
        "email": "p@example.test",
        "tenant_id": world["tid"],
        "tenant_role": "reader",
        "global_role": None,
    }
    with patch(f"{MOD}.PortalAuthService.verify_mfa_challenge", return_value=ok):
        r = await async_client.post(
            V, json={"mfa_challenge": "c", "totp_code": "123456"}
        )
        assert r.status_code == 200 and "access_token" in await r.get_json()


@pytest.mark.asyncio
async def test_mfa_management_routes(app, async_client, world):
    """Enable/confirm/regenerate/disable map service results and errors."""
    h = _hdr(app, world["uid"], world["tid"])
    svc = f"{MOD}.PortalAuthService"
    with patch(f"{svc}.enable_mfa", return_value={"secret": "s"}):
        r = await async_client.post(
            f"{BASE}/mfa/enable", json={"current_code": "1"}, headers=h
        )
        assert r.status_code == 200 and "no-store" in r.headers.get("Cache-Control", "")
        assert (
            await async_client.post(f"{BASE}/mfa/enable", headers=h)
        ).status_code == 200
    with patch(
        f"{svc}.enable_mfa",
        return_value={
            "error": "need code",
            "mfa_code_required": True,
            "status_code": 403,
        },
    ):
        r = await async_client.post(f"{BASE}/mfa/enable", json={}, headers=h)
        assert r.status_code == 403 and (await r.get_json())["mfa_code_required"]

    C = f"{BASE}/mfa/confirm"
    assert (await async_client.post(C, json={}, headers=h)).status_code == 400
    with patch(
        f"{svc}.confirm_mfa", return_value={"error": "bad code", "status_code": 401}
    ):
        r = await async_client.post(C, json={"totp_code": "000000"}, headers=h)
        assert r.status_code == 401 and (await r.get_json())["verified"] is False
    with patch(f"{svc}.confirm_mfa", return_value={}):
        r = await async_client.post(C, json={"totp_code": "123456"}, headers=h)
        assert (await r.get_json()) == {"verified": True}

    with patch(
        f"{svc}.regenerate_backup_codes",
        return_value={"backup_codes": ["a"], "remaining": 1},
    ):
        r = await async_client.post(
            f"{BASE}/mfa/backup-codes/regenerate", json={"code": "1"}, headers=h
        )
        assert r.status_code == 200
    with patch(f"{svc}.regenerate_backup_codes", return_value={"error": "no"}):
        assert (
            await async_client.post(f"{BASE}/mfa/backup-codes/regenerate", headers=h)
        ).status_code == 400
    with patch(f"{svc}.disable_mfa", return_value={"disabled": True}):
        assert (
            await async_client.post(
                f"{BASE}/mfa/disable", json={"code": "1"}, headers=h
            )
        ).status_code == 200
    with patch(
        f"{svc}.disable_mfa", return_value={"error": "enforced", "status_code": 403}
    ):
        assert (
            await async_client.post(f"{BASE}/mfa/disable", json={}, headers=h)
        ).status_code == 403


@pytest.mark.asyncio
async def test_enrollment_token_confirm_clears_cookies(app, async_client, world):
    """An enrolment-only token can confirm MFA and then must re-authenticate."""
    h = _hdr(app, world["uid"], world["tid"])
    enrol = {"sub": str(world["uid"]), "tenant_id": world["tid"]}
    with (
        patch(f"{MOD}.verify_token", return_value=None),
        patch(
            f"{MOD}.PortalAuthService.verify_mfa_enrollment_token", return_value=enrol
        ),
        patch(f"{MOD}.PortalAuthService.confirm_mfa", return_value={}),
    ):
        r = await async_client.post(
            f"{BASE}/mfa/confirm", json={"totp_code": "123456"}, headers=h
        )
        assert r.status_code == 200 and (await r.get_json())["reauthenticate"] is True
        r = await async_client.post(f"{BASE}/password/change", json={}, headers=h)
        assert r.status_code == 401


# ----------------------------------------------------- password / refresh


@pytest.mark.asyncio
async def test_password_routes(app, async_client, world):
    """Password change and reset validate input and map service results."""
    h = _hdr(app, world["uid"], world["tid"])
    C = f"{BASE}/password/change"
    assert (await async_client.post(C, json={}, headers=h)).status_code == 400
    assert (
        await async_client.post(C, json={"current_password": "a"}, headers=h)
    ).status_code == 400
    with patch(
        f"{MOD}.PortalAuthService.change_password", return_value={"error": "wrong"}
    ):
        assert (
            await async_client.post(
                C, json={"current_password": "a", "new_password": "b"}, headers=h
            )
        ).status_code == 400
    with patch(
        f"{MOD}.PortalAuthService.change_password", return_value={"message": "ok"}
    ):
        assert (
            await async_client.post(
                C, json={"current_password": "a", "new_password": "b"}, headers=h
            )
        ).status_code == 200

    R = f"{BASE}/password/reset"
    assert (await async_client.post(R, json={})).status_code == 400
    assert (
        await async_client.post(R, json={"email": "e@example.test"})
    ).status_code == 400
    with patch(
        f"{MOD}.PortalAuthService.reset_password", return_value={"message": "sent"}
    ):
        r = await async_client.post(
            R, json={"email": "e@example.test", "tenant_id": world["tid"]}
        )
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_refresh_and_logout(app, async_client, world):
    """Refresh handles missing, invalid, inactive, locked-out and valid tokens."""
    F = f"{BASE}/refresh"
    NOCSRF = {"Authorization": "Bearer ignored"}
    assert (await async_client.post(F, json={}, headers=NOCSRF)).status_code == 400
    r = await async_client.post(F, json={"refresh_token": "junk"}, headers=NOCSRF)
    assert r.status_code == 401

    good = _hdr(app, world["uid"], world["tid"], kind="portal_refresh")[
        "Authorization"
    ].split()[1]
    r = await async_client.post(F, json={"refresh_token": good}, headers=NOCSRF)
    assert r.status_code == 200 and "access_token" in await r.get_json()

    gone = _portal_user(app.db, world["tid"], is_active=False)
    tok = _hdr(app, gone, world["tid"], kind="portal_refresh")["Authorization"].split()[
        1
    ]
    assert (
        await async_client.post(F, json={"refresh_token": tok}, headers=NOCSRF)
    ).status_code == 401
    ghost = _hdr(app, 99999999, world["tid"], kind="portal_refresh")[
        "Authorization"
    ].split()[1]
    assert (
        await async_client.post(F, json={"refresh_token": ghost}, headers=NOCSRF)
    ).status_code == 401

    with patch(f"{MOD}.compute_user_mfa_status", return_value={"mfa_locked_out": True}):
        r = await async_client.post(F, json={"refresh_token": good}, headers=NOCSRF)
        assert r.status_code == 401 and "MFA" in (await r.get_json())["error"]
    with patch(f"{MOD}.verify_token", side_effect=jwt.ExpiredSignatureError()):
        r = await async_client.post(F, json={"refresh_token": good}, headers=NOCSRF)
        assert r.status_code == 401 and "expired" in (await r.get_json())["error"]
    with patch(f"{MOD}.verify_token", side_effect=jwt.InvalidTokenError()):
        r = await async_client.post(F, json={"refresh_token": good}, headers=NOCSRF)
        assert r.status_code == 401

    r = await async_client.post(f"{BASE}/logout", headers=NOCSRF)
    assert r.status_code == 200


# ------------------------------------------------------------- profile


@pytest.mark.asyncio
async def test_get_me(app, async_client, world):
    """/me returns profile data, organization names and handles odd columns."""
    db = app.db
    now = datetime.now(UTC)
    org = db.organizations.insert(
        name="Org Me", tenant_id=world["tid"], created_at=now, updated_at=now
    )
    db.commit()
    h = _hdr(app, world["uid"], world["tid"])
    r = await async_client.get(f"{BASE}/me", headers=h)
    body = await r.get_json()
    assert r.status_code == 200 and body["email"] and body["organization_id"] is None

    db.portal_user_org_assignments.insert(
        portal_user_id=world["uid"], organization_id=org, role="reader"
    )
    db(db.portal_users.id == world["uid"]).update(mfa_backup_codes='["a", "b"]')
    db.commit()
    body = await (await async_client.get(f"{BASE}/me", headers=h)).get_json()
    assert (
        body["organization_name"] == "Org Me"
        and body["mfa_backup_codes_remaining"] == 2
    )

    db(db.portal_users.id == world["uid"]).update(mfa_backup_codes="not json")
    db.commit()
    body = await (await async_client.get(f"{BASE}/me", headers=h)).get_json()
    assert body["mfa_backup_codes_remaining"] == 0

    ghost = _hdr(app, 99999999, world["tid"])
    assert (await async_client.get(f"{BASE}/me", headers=ghost)).status_code == 404


@pytest.mark.xfail(
    strict=True,
    reason="bug: sync update_current_user reads request.get_json() without awaiting it",
)
@pytest.mark.asyncio
async def test_patch_me(app, async_client, world):
    """PATCH /me should update the allowed fields (currently a TypeError 500)."""
    h = _hdr(app, world["uid"], world["tid"])
    r = await async_client.patch(f"{BASE}/me", json={"full_name": "New"}, headers=h)
    assert r.status_code == 200
    assert (await r.get_json())["full_name"] == "New"


@pytest.mark.asyncio
async def test_patch_me_missing_user(app, async_client):
    """PATCH /me for an unknown portal user is a 404 before the body is read."""
    h = _hdr(app, 99999999, 1)
    r = await async_client.patch(f"{BASE}/me", json={"full_name": "x"}, headers=h)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_org_assignments(app, async_client, world):
    """Org role assignment needs an admin and validates its body."""
    A = f"{BASE}/org-assignments"
    reader = _hdr(app, world["uid"], world["tid"])
    assert (await async_client.post(A, json={}, headers=reader)).status_code == 403
    admin = _hdr(app, world["uid"], world["tid"], tenant_role="admin")
    assert (await async_client.post(A, json={}, headers=admin)).status_code == 400
    assert (
        await async_client.post(A, json={"portal_user_id": 1}, headers=admin)
    ).status_code == 400
    body = {"portal_user_id": 1, "organization_id": 1, "role": "owner"}
    assert (await async_client.post(A, json=body, headers=admin)).status_code == 400
    body["role"] = "reader"
    with patch(
        f"{MOD}.PortalAuthService.assign_org_role", return_value={"error": "no"}
    ):
        assert (await async_client.post(A, json=body, headers=admin)).status_code == 400
    with patch(
        f"{MOD}.PortalAuthService.assign_org_role", return_value={"assigned": True}
    ):
        assert (await async_client.post(A, json=body, headers=admin)).status_code == 200
