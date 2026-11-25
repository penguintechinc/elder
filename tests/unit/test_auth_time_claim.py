"""`auth_time` is set only by interactive login and survives refresh unchanged.

security review of the privacy change: the "recently signed in" check for
SSO-only subjects read `iat`, and every token refresh mints a new `iat`, so
a refreshed (or stolen-refresh-token) access token always looked fresh.
`auth_time` now records the interactive authentication, is copied -- never
renewed -- by refresh, and is what the erase route checks. A token without
it is treated as not recent.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import jwt
import pytest

from apps.api.api.v1 import portal_auth, privacy
from apps.api.auth.jwt_handler import (
    carried_auth_time,
    generate_token,
    interactive_auth_time,
    verify_token,
)
from apps.api.services.privacy.service import PrivacyService

pytestmark = pytest.mark.asyncio

API_V1 = Path(portal_auth.__file__).parent


def _identity() -> SimpleNamespace:
    """A stand-in identities row for token minting (tenant 7, id 42)."""
    return SimpleNamespace(
        id=42, username="sso-user", tenant_id=7, portal_role="observer", is_active=True
    )


def _portal_user() -> dict:
    """A stand-in portal-user dict as returned by PortalAuthService."""
    return {
        "id": 42,
        "email": "p@example.invalid",
        "tenant_id": 7,
        "tenant_role": "member",
        "global_role": None,
    }


def _decode(app, token: str) -> dict:
    """Decode a token minted by this app without re-validating audience."""
    secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
    return jwt.decode(
        token, secret, algorithms=["HS256"], options={"verify_aud": False}
    )


class TestClaimHelpers:
    """carried_auth_time never invents or renews a value."""

    async def test_interactive_auth_time_is_now(self):
        assert abs(interactive_auth_time() - time.time()) < 2
        assert isinstance(interactive_auth_time(), int)

    async def test_valid_claim_is_carried_verbatim(self):
        past = int(time.time()) - 86_400
        assert carried_auth_time({"auth_time": past, "iat": int(time.time())}) == past

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            {},
            {"iat": 1_900_000_000},
            {"auth_time": None},
            {"auth_time": "1700000000"},
            {"auth_time": True},
            {"auth_time": 1.5},
            {"auth_time": 0},
            {"auth_time": -5},
            {"auth_time": int(time.time()) + 3600},
        ],
    )
    async def test_missing_or_malformed_claim_is_not_carried(self, payload):
        assert carried_auth_time(payload) is None


class TestIdentityTokens:
    """jwt_handler.generate_token + the /auth/refresh carry rule."""

    async def test_login_tokens_carry_auth_time_and_default_tokens_do_not(self, app):
        async with app.app_context():
            stamped = verify_token(
                generate_token(_identity(), "access", auth_time=123456)
            )
            refresh = verify_token(
                generate_token(_identity(), "refresh", auth_time=123456)
            )
            bare = verify_token(generate_token(_identity(), "access"))
        assert stamped["auth_time"] == 123456
        assert refresh["auth_time"] == 123456
        assert "auth_time" not in bare  # fail closed: absent means "not recent"

    async def test_refresh_endpoint_does_not_renew_auth_time(self, app):
        """A refresh an hour after login: iat is new, auth_time is the login's."""
        login_time = int(time.time()) - 3600
        db = MagicMock()
        db.identities.__getitem__.return_value = _identity()
        async with app.app_context():
            refresh = generate_token(_identity(), "refresh", auth_time=login_time)
            with patch.object(app, "db", db):
                client = app.test_client()
                response = await client.post(
                    "/api/v1/auth/refresh", json={"refresh_token": refresh}
                )
                body = await response.get_json()
            assert response.status_code == 200, body
            claims = verify_token(body["access_token"])
        assert claims["auth_time"] == login_time
        assert time.time() - claims["iat"] < 60  # iat IS renewed -- hence useless

    async def test_refresh_of_a_pre_deploy_token_yields_no_auth_time(self, app):
        db = MagicMock()
        db.identities.__getitem__.return_value = _identity()
        async with app.app_context():
            refresh = generate_token(_identity(), "refresh")  # no auth_time
            with patch.object(app, "db", db):
                response = await app.test_client().post(
                    "/api/v1/auth/refresh", json={"refresh_token": refresh}
                )
                body = await response.get_json()
            claims = verify_token(body["access_token"])
        assert "auth_time" not in claims


class TestPortalTokens:
    """portal_auth.generate_tokens + the rotation carry rule."""

    async def test_both_portal_tokens_carry_auth_time(self, app):
        async with app.app_context():
            tokens = portal_auth.generate_tokens(_portal_user(), auth_time=222)
            bare = portal_auth.generate_tokens(_portal_user())
            assert _decode(app, tokens["access_token"])["auth_time"] == 222
            assert _decode(app, tokens["refresh_token"])["auth_time"] == 222
            assert "auth_time" not in _decode(app, bare["access_token"])
            assert "auth_time" not in _decode(app, bare["refresh_token"])

    async def test_rotation_chain_keeps_the_original_auth_time(self, app):
        login_time = int(time.time()) - 7200
        async with app.app_context():
            tokens = portal_auth.generate_tokens(_portal_user(), auth_time=login_time)
            for _ in range(3):  # three successive rotations
                payload = _decode(app, tokens["refresh_token"])
                tokens = portal_auth.generate_tokens(
                    _portal_user(), auth_time=carried_auth_time(payload)
                )
            final = _decode(app, tokens["access_token"])
        assert final["auth_time"] == login_time


class TestEveryIssuingPathIsExplicit:
    """Source guard: no login/refresh path may mint a token without deciding auth_time."""

    @pytest.mark.parametrize(
        ("filename", "interactive", "carried"),
        [("auth.py", 2, 1), ("portal_auth.py", 3, 1), ("sso.py", 2, 0)],
    )
    async def test_call_sites(self, filename, interactive, carried):
        source = (API_V1 / filename).read_text()
        calls = re.findall(
            r"(?<!def )\bgenerate_tokens?\((?:[^()]|\([^()]*\))*\)", source
        )
        assert len(calls) == interactive + carried, calls
        assert all("auth_time" in call for call in calls), calls
        assert sum("carried_auth_time(" in c for c in calls) == carried
        if filename == "auth.py":  # one interactive_auth_time() feeds both tokens
            assert source.count("interactive_auth_time()") == 1
        else:
            assert sum("interactive_auth_time()" in c for c in calls) == interactive
        # A refresh path must never stamp "now".
        refresh_body = source[source.find('"/refresh"') :].split("@bp.route", 1)[0]
        assert "interactive_auth_time" not in refresh_body


class TestEraseRecencyEndToEnd:
    """Real tokens through the real erase route for an SSO-only subject."""

    async def _erase_with(self, app, token: str) -> int:
        db = MagicMock()
        db.return_value.select.return_value.first.return_value = SimpleNamespace(
            legal_hold=False
        )

        async def _allow(*args, **kwargs):
            return True, {}

        with (
            patch.object(app, "db", db),
            patch.object(PrivacyService, "has_local_credential", return_value=False),
            patch.object(
                PrivacyService,
                "erase_identity",
                return_value={"identity_id": 42, "anonymized": True, "receipt": {}},
            ),
            patch("apps.api.api.v1.privacy.check_subject_rate_limit", _allow),
        ):
            async with app.test_request_context(
                "/api/v1/privacy/me/erase",
                method="POST",
                json={"confirm": "ERASE"},
                headers={"Authorization": f"Bearer {token}"},
            ):
                from quart import g

                g.current_user = _identity()
                g.claims = {"tenant": "7"}
                _, status = await privacy.erase_my_data.__wrapped__()
        return status

    async def test_fresh_sso_login_is_allowed(self, app):
        async with app.app_context():
            token = generate_token(
                _identity(), "access", auth_time=interactive_auth_time()
            )
            assert await self._erase_with(app, token) == 200

    async def test_refreshed_token_six_minutes_after_login_is_refused(self, app):
        async with app.app_context():
            refresh = generate_token(
                _identity(), "refresh", auth_time=int(time.time()) - 360
            )
            refreshed = generate_token(
                _identity(),
                "access",
                auth_time=carried_auth_time(verify_token(refresh)),
            )
            assert time.time() - verify_token(refreshed)["iat"] < 60  # looks "fresh"
            assert await self._erase_with(app, refreshed) == 403

    async def test_token_without_auth_time_is_refused(self, app):
        async with app.app_context():
            legacy = generate_token(_identity(), "access")
            assert await self._erase_with(app, legacy) == 403
