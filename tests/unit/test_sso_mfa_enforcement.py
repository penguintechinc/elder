"""SSO sign-ins honour the tenant MFA policy like password logins do.

A user whose MFA enrolment grace period has expired must not receive a full
session through SAML or OIDC; they get only the enrolment-scoped response.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.api.auth.portal_cookies import REFRESH_COOKIE_NAME

_TOKENS = {
    "access_token": "acc-jwt",
    "refresh_token": "ref-jwt",
    "token_type": "Bearer",
    "expires_in": 3600,
}

_ROW = SimpleNamespace(
    id=7,
    tenant_id=1,
    email="a@b.co",
    full_name="A B",
    created_at=None,
)


def _status(locked: bool) -> dict:
    return {
        "mfa_enforced": True,
        "mfa_setup_required": locked,
        "mfa_grace_expires_at": "2026-01-01T00:00:00+00:00" if locked else None,
        "mfa_grace_days_remaining": 0 if locked else None,
        "mfa_locked_out": locked,
    }


async def _saml(async_client):
    with patch(
        "apps.api.api.v1.sso.SAMLService.process_saml_response",
        return_value={"id": 7, "tenant_id": 1, "email": "a@b.co"},
    ):
        return await async_client.post(
            "/api/v1/sso/saml/acs/1", form={"SAMLResponse": "fake-saml"}
        )


async def _oidc(async_client):
    with (
        patch(
            "apps.api.api.v1.sso.OIDCService.exchange_code_for_tokens",
            return_value={"id_token": "idt", "access_token": "idp-acc"},
        ),
        patch(
            "apps.api.api.v1.sso.OIDCService.validate_id_token",
            return_value={"sub": "s", "email": "a@b.co"},
        ),
        patch("apps.api.api.v1.sso.OIDCService.get_userinfo", return_value={}),
        patch(
            "apps.api.api.v1.sso.OIDCService.get_idp_config",
            return_value={"tenant_id": 1, "jit_provisioning_enabled": True},
        ),
        patch(
            "apps.api.api.v1.sso.OIDCService.jit_provision_user",
            return_value={"id": 7, "tenant_id": 1, "email": "a@b.co"},
        ),
    ):
        return await async_client.get(
            "/api/v1/sso/oidc/callback",
            query_string={
                "code": "c",
                "idp_id": 1,
                "redirect_uri": "https://app.example/cb",
            },
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("flow", [_saml, _oidc], ids=["saml", "oidc"])
@patch("apps.api.api.v1.sso.user_has_mfa_enrolled", return_value=False)
@patch("apps.api.api.v1.sso._load_portal_user", return_value=_ROW)
@patch("apps.api.api.v1.sso.generate_tokens", return_value=dict(_TOKENS))
async def test_expired_grace_gets_enrollment_only(
    mock_tokens, _row, _enrolled, flow, async_client
):
    with patch(
        "apps.api.api.v1.sso.compute_user_mfa_status", return_value=_status(True)
    ):
        resp = await flow(async_client)

    assert resp.status_code == 200
    body = await resp.get_json()
    assert body["mfaEnrollmentRequired"] is True
    assert body["enrollment_token"]
    assert "access_token" not in body
    assert "refresh_token" not in body
    mock_tokens.assert_not_called()
    joined = "\n".join(resp.headers.get_all("Set-Cookie"))
    assert f"{REFRESH_COOKIE_NAME}=ref-jwt" not in joined


@pytest.mark.asyncio
@pytest.mark.parametrize("flow", [_saml, _oidc], ids=["saml", "oidc"])
@patch("apps.api.api.v1.sso.user_has_mfa_enrolled", return_value=False)
@patch("apps.api.api.v1.sso._load_portal_user", return_value=_ROW)
@patch("apps.api.api.v1.sso.generate_tokens", return_value=dict(_TOKENS))
async def test_within_grace_gets_full_session(
    mock_tokens, _row, _enrolled, flow, async_client
):
    with patch(
        "apps.api.api.v1.sso.compute_user_mfa_status", return_value=_status(False)
    ):
        resp = await flow(async_client)

    assert resp.status_code == 200
    body = await resp.get_json()
    assert body["access_token"] == "acc-jwt"
    assert not body.get("mfaEnrollmentRequired")
    mock_tokens.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("flow", [_saml, _oidc], ids=["saml", "oidc"])
@patch("apps.api.api.v1.sso._load_portal_user", return_value=None)
@patch("apps.api.api.v1.sso.generate_tokens", return_value=dict(_TOKENS))
async def test_missing_user_row_gets_no_session(mock_tokens, _row, flow, async_client):
    resp = await flow(async_client)
    assert resp.status_code == 401
    mock_tokens.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("flow", [_saml, _oidc], ids=["saml", "oidc"])
@patch(
    "apps.api.api.v1.sso.PortalAuthService.create_mfa_challenge",
    return_value="opaque-challenge",
)
@patch(
    "apps.api.api.v1.sso._load_portal_user",
    return_value=SimpleNamespace(**{**vars(_ROW), "mfa_secret": "v1:k:sealed"}),
)
@patch("apps.api.api.v1.sso.generate_tokens", return_value=dict(_TOKENS))
async def test_enrolled_user_gets_mfa_challenge_not_session(
    mock_tokens, _row, mock_challenge, flow, async_client
):
    """SSO does not skip the second factor for a user with TOTP enrolled."""
    resp = await flow(async_client)

    assert resp.status_code == 200
    body = await resp.get_json()
    assert body["mfaRequired"] is True
    assert body["mfa_challenge"] == "opaque-challenge"
    assert "access_token" not in body
    assert "refresh_token" not in body
    assert "user" not in body
    mock_tokens.assert_not_called()
    assert mock_challenge.call_args.args[:2] == (7, 1)
    joined = "\n".join(resp.headers.get_all("Set-Cookie"))
    assert "acc-jwt" not in joined and "ref-jwt" not in joined
