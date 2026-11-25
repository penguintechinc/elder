"""Unit tests for the SSO blueprint's OIDC, SAML-ACS and SCIM endpoints.

The real app and portal-token auth run; the SSO service classes are replaced
with mocks so each route's validation and error mapping is asserted directly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import jwt
import pytest

BASE = "/api/v1/sso"
MOD = "apps.api.api.v1.sso"


def _portal(
    app, tenant_id: int = 1, global_role=None, tenant_role="reader"
) -> dict[str, str]:
    """Headers carrying a signed portal-user token."""
    now = datetime.now(UTC)
    claims = {
        "sub": "1",
        "email": "p@example.test",
        "tenant_id": tenant_id,
        "tenant_role": tenant_role,
        "global_role": global_role,
        "roles": ["observer"],
        "scope": "",
        "type": "portal_user",
        "principal_kind": "portal_user",
        "iat": now,
        "exp": now + timedelta(minutes=10),
    }
    secret = app.config["JWT_SECRET_KEY"] or app.config["SECRET_KEY"]
    tok = jwt.encode(claims, secret, algorithm=app.config["JWT_ALGORITHM"])
    return {"Authorization": f"Bearer {tok}"}


# ---------------------------------------------------------------- SAML ACS


@pytest.mark.asyncio
async def test_saml_acs_requires_response(async_client):
    """The ACS endpoint rejects posts without a SAMLResponse."""
    r = await async_client.post(f"{BASE}/saml/acs/1", form={})
    assert r.status_code == 400


# -------------------------------------------------------------------- OIDC


@pytest.mark.asyncio
async def test_oidc_authorize(async_client):
    """Authorize requires a redirect URI and maps service errors."""
    assert (await async_client.get(f"{BASE}/oidc/authorize/1")).status_code == 400
    with patch(f"{MOD}.OIDCService") as svc:
        svc.get_authorization_url.return_value = {"url": "https://idp.example.test/a"}
        r = await async_client.get(
            f"{BASE}/oidc/authorize/1?redirect_uri=https://x.test/cb&state=s"
        )
        assert r.status_code == 200
        svc.get_authorization_url.return_value = {"error": "nope"}
        r = await async_client.get(
            f"{BASE}/oidc/authorize/1?redirect_uri=https://x.test/cb"
        )
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_oidc_callback_validation_and_errors(async_client):
    """Callback validates parameters and each service step's error."""
    C = f"{BASE}/oidc/callback"
    assert (await async_client.get(C)).status_code == 400
    assert (await async_client.get(f"{C}?code=c")).status_code == 400
    assert (await async_client.get(f"{C}?code=c&idp_id=1")).status_code == 400
    q = f"{C}?code=c&idp_id=1&redirect_uri=https://x.test/cb"
    with patch(f"{MOD}.OIDCService") as svc:
        svc.exchange_code_for_tokens.return_value = {"error": "bad code"}
        assert (await async_client.get(q)).status_code == 400
        svc.exchange_code_for_tokens.return_value = {
            "id_token": "i",
            "access_token": "a",
        }
        svc.validate_id_token.return_value = {"error": "bad id token"}
        assert (await async_client.get(q)).status_code == 400
        svc.validate_id_token.return_value = {"sub": "u"}
        svc.get_userinfo.return_value = {"error": "no userinfo"}
        svc.get_idp_config.return_value = None
        assert (await async_client.get(q)).status_code == 404
        svc.get_idp_config.return_value = {
            "tenant_id": 2,
            "jit_provisioning_enabled": False,
        }
        assert (await async_client.get(q)).status_code == 403
        svc.get_idp_config.return_value = {
            "tenant_id": 2,
            "jit_provisioning_enabled": True,
        }
        svc.jit_provision_user.return_value = {"error": "jit failed"}
        assert (await async_client.get(q)).status_code == 400


@pytest.mark.asyncio
async def test_oidc_callback_success_and_gate(async_client):
    """A provisioned user is issued tokens unless the MFA gate intervenes."""
    q = f"{BASE}/oidc/callback?code=c&idp_id=1&redirect_uri=https://x.test/cb"
    user = {"id": 7, "email": "u@example.test", "tenant_id": 2}
    with patch(f"{MOD}.OIDCService") as svc:
        svc.exchange_code_for_tokens.return_value = {
            "id_token": "i",
            "access_token": "a",
        }
        svc.validate_id_token.return_value = {"sub": "u"}
        svc.get_userinfo.return_value = {"name": "n"}
        svc.get_idp_config.return_value = {
            "tenant_id": 2,
            "jit_provisioning_enabled": True,
        }
        svc.jit_provision_user.return_value = user
        with (
            patch(f"{MOD}._sso_mfa_gate", return_value=None),
            patch(
                f"{MOD}.generate_tokens",
                return_value={"access_token": "a", "refresh_token": "r"},
            ),
            patch(f"{MOD}._attach_auth_cookies"),
        ):
            r = await async_client.get(q)
            assert r.status_code == 200 and (await r.get_json())["user"]["id"] == 7
        with patch(f"{MOD}._sso_mfa_gate", side_effect=LookupError("gone")):
            assert (await async_client.get(q)).status_code == 401
        gated = ({"mfa": "required"}, 403)
        with patch(f"{MOD}._sso_mfa_gate", return_value=gated):
            assert (await async_client.get(q)).status_code == 403


@pytest.mark.asyncio
async def test_oidc_logout_userinfo_refresh(app, async_client):
    """Authenticated OIDC helper routes validate input and map errors."""
    h = _portal(app)
    with patch(f"{MOD}.OIDCService") as svc:
        svc.logout.return_value = {"end_session_url": "https://idp.example.test/out"}
        r = await async_client.post(
            f"{BASE}/oidc/logout/1", json={"id_token_hint": "h"}, headers=h
        )
        assert r.status_code == 200
        r = await async_client.post(f"{BASE}/oidc/logout/1", headers=h)
        assert r.status_code == 200
        svc.logout.return_value = {"error": "no endpoint"}
        assert (
            await async_client.post(f"{BASE}/oidc/logout/1", json={}, headers=h)
        ).status_code == 400

        assert (
            await async_client.get(f"{BASE}/oidc/userinfo/1", headers=h)
        ).status_code == 400
        svc.get_userinfo.return_value = {"sub": "u"}
        ih = {**h, "X-OIDC-Access-Token": "t"}
        assert (
            await async_client.get(f"{BASE}/oidc/userinfo/1", headers=ih)
        ).status_code == 200
        svc.get_userinfo.return_value = {"error": "bad"}
        assert (
            await async_client.get(f"{BASE}/oidc/userinfo/1", headers=ih)
        ).status_code == 400

        assert (
            await async_client.post(f"{BASE}/oidc/refresh/1", json={}, headers=h)
        ).status_code == 400
        assert (
            await async_client.post(f"{BASE}/oidc/refresh/1", json={"x": 1}, headers=h)
        ).status_code == 400
        svc.refresh_tokens.return_value = {"access_token": "n"}
        r = await async_client.post(
            f"{BASE}/oidc/refresh/1", json={"refresh_token": "r"}, headers=h
        )
        assert r.status_code == 200
        svc.refresh_tokens.return_value = {"error": "expired"}
        r = await async_client.post(
            f"{BASE}/oidc/refresh/1", json={"refresh_token": "r"}, headers=h
        )
        assert r.status_code == 400
    assert (
        await async_client.post(f"{BASE}/oidc/logout/1", json={})
    ).status_code == 401


# -------------------------------------------------------------------- SCIM


@pytest.mark.asyncio
async def test_scim_config_endpoints(app, async_client):
    """SCIM config read/regenerate honour roles and report missing config."""
    h = _portal(app)
    admin = _portal(app, global_role="admin")
    with patch(f"{MOD}.SCIMService") as svc:
        svc.get_scim_config.return_value = {"enabled": True}
        assert (
            await async_client.get(f"{BASE}/scim/config/3", headers=h)
        ).status_code == 200
        svc.get_scim_config.return_value = None
        assert (
            await async_client.get(f"{BASE}/scim/config/3", headers=h)
        ).status_code == 404

        assert (
            await async_client.post(f"{BASE}/scim/config/3/regenerate-token", headers=h)
        ).status_code == 403
        svc.regenerate_token.return_value = {"bearer_token": "n"}
        assert (
            await async_client.post(
                f"{BASE}/scim/config/3/regenerate-token", headers=admin
            )
        ).status_code == 200
        tadmin = _portal(app, tenant_role="admin")
        assert (
            await async_client.post(
                f"{BASE}/scim/config/3/regenerate-token", headers=tadmin
            )
        ).status_code == 200
        svc.regenerate_token.return_value = {"error": "none"}
        assert (
            await async_client.post(
                f"{BASE}/scim/config/3/regenerate-token", headers=admin
            )
        ).status_code == 404


@pytest.mark.asyncio
async def test_scim_config_create(app, async_client):
    """Creating SCIM config needs an admin and a tenant id."""
    reader = _portal(app)
    assert (
        await async_client.post(f"{BASE}/scim/config", json={}, headers=reader)
    ).status_code == 403
    with patch(f"{MOD}.SCIMService") as svc:
        svc.create_scim_config.return_value = {"bearer_token": "n"}
        tadmin = _portal(app, tenant_id=4, tenant_role="admin")
        r = await async_client.post(
            f"{BASE}/scim/config", json={"tenant_id": 99}, headers=tadmin
        )
        assert r.status_code == 201 and svc.create_scim_config.call_args.args == (4,)
        gadmin = _portal(app, global_role="admin")
        r = await async_client.post(
            f"{BASE}/scim/config", json={"tenant_id": 8}, headers=gadmin
        )
        assert svc.create_scim_config.call_args.args == (8,)
        r = await async_client.post(f"{BASE}/scim/config", json={}, headers=gadmin)
        assert r.status_code == 400


def _scim_hdr(token: str = "tok") -> dict[str, str]:
    """Bearer header for SCIM calls."""
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_scim_auth_decorator(async_client):
    """SCIM routes require a valid bearer token for the tenant."""
    U = f"{BASE}/scim/3/Users"
    r = await async_client.get(U)
    assert (
        r.status_code == 401
        and (await r.get_json())["detail"] == "Authorization required"
    )
    assert (
        await async_client.get(U, headers={"Authorization": "Basic x"})
    ).status_code == 401
    with patch(f"{MOD}.SCIMService") as svc:
        svc.validate_bearer_token.return_value = False
        r = await async_client.get(U, headers=_scim_hdr())
        assert r.status_code == 401 and "Invalid" in (await r.get_json())["detail"]
        svc.validate_bearer_token.return_value = True
        svc.list_users.return_value = {"Resources": []}
        r = await async_client.get(
            f"{U}?startIndex=2&count=5&filter=x", headers=_scim_hdr()
        )
        assert r.status_code == 200
        assert svc.list_users.call_args.args == (3, 2, 5, "x")


@pytest.mark.asyncio
async def test_scim_user_read_delete(async_client):
    """SCIM get/delete map service results to SCIM error bodies."""
    U = f"{BASE}/scim/3/Users/9"
    with patch(f"{MOD}.SCIMService") as svc:
        svc.validate_bearer_token.return_value = True
        svc.get_user.return_value = {"id": "9"}
        assert (await async_client.get(U, headers=_scim_hdr())).status_code == 200
        svc.get_user.return_value = {"error": "missing"}
        r = await async_client.get(U, headers=_scim_hdr())
        assert r.status_code == 404 and (await r.get_json())["detail"] == "missing"
        svc.get_user.return_value = {"error": "denied", "status": 403}
        assert (await async_client.get(U, headers=_scim_hdr())).status_code == 403

        svc.delete_user.return_value = {}
        assert (await async_client.delete(U, headers=_scim_hdr())).status_code == 204
        svc.delete_user.return_value = {"error": "missing"}
        assert (await async_client.delete(U, headers=_scim_hdr())).status_code == 404


@pytest.mark.xfail(
    strict=True,
    reason="bug: scim_auth_required wraps async views with a sync decorator (un-awaited coroutine)",
)
@pytest.mark.asyncio
async def test_scim_user_write(async_client):
    """SCIM create/update should validate bodies and map results (currently a 500)."""
    base = f"{BASE}/scim/3/Users"
    with patch(f"{MOD}.SCIMService") as svc:
        svc.validate_bearer_token.return_value = True
        assert (
            await async_client.post(base, json={}, headers=_scim_hdr())
        ).status_code == 400
        svc.create_user.return_value = {"id": "1"}
        assert (
            await async_client.post(base, json={"userName": "u"}, headers=_scim_hdr())
        ).status_code == 201
        svc.create_user.return_value = {"error": "dup", "status": 409}
        assert (
            await async_client.post(base, json={"userName": "u"}, headers=_scim_hdr())
        ).status_code == 409
        assert (
            await async_client.put(f"{base}/1", json={}, headers=_scim_hdr())
        ).status_code == 400
        svc.update_user.return_value = {"id": "1"}
        assert (
            await async_client.put(
                f"{base}/1", json={"userName": "u"}, headers=_scim_hdr()
            )
        ).status_code == 200
        svc.update_user.return_value = {"error": "bad"}
        assert (
            await async_client.put(
                f"{base}/1", json={"userName": "u"}, headers=_scim_hdr()
            )
        ).status_code == 400


def test_scim_decorator_rejects_before_calling_view():
    """The SCIM decorator short-circuits without invoking the wrapped view."""
    import asyncio

    from apps.api.api.v1.sso import scim_auth_required

    view = MagicMock()
    wrapped = scim_auth_required(view)

    async def run():
        """Call the wrapper with no auth header inside a request context."""
        from quart import Quart

        app = Quart(__name__)
        async with app.test_request_context("/"):
            resp, status = wrapped(1)
            assert status == 401
            view.assert_not_called()

    asyncio.run(run())
