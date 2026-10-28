"""OIDC service error paths and ID-token validation (real DB/Redis, fake external IdP).

Complements ``test_sso_oidc_login_hardening.py``: every failure here must come
back as a stable machine code -- never upstream exception text -- and an ID
token that is expired, mis-addressed, unsigned-by-the-JWKS-key or missing a
mandatory claim must never produce a session.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from quart import current_app
from sso_testkit import (
    AUTHORIZE,
    CALLBACK_REDIRECT_URI,
    FakeOidcProvider,
    begin,
    build_world,
    finish,
    install_fake_providers,
    login,
    teardown_world,
)

from apps.api.services.sso import OIDCService
from apps.api.services.sso.oidc_state import OIDCLoginError


@pytest_asyncio.fixture
async def world(app):
    """Two tenants + global scope with OIDC IdPs and users."""
    async with app.app_context():
        w = build_world(
            app,
            issuer_a="https://idp-a.example.test",
            issuer_b="https://idp-b.example.test",
            issuer_g="https://idp-g.example.test",
        )
    yield w
    async with app.app_context():
        teardown_world(w)


@pytest.fixture
def idp_b(monkeypatch) -> FakeOidcProvider:
    """The fake external IdP behind tenant B's OIDC config."""
    provider = FakeOidcProvider(
        issuer="https://idp-b.example.test",
        client_id="client-b",
        client_secret="SECRET-B-oidc",
        kid="b-key",
    )
    install_fake_providers(monkeypatch, [provider])
    return provider


@pytest_asyncio.fixture
async def browser(app):
    """A cookie-less client; tests carry the binding cookie explicitly."""
    async with app.test_client(use_cookies=False) as client:
        yield client


async def _config(app, idp_id: int) -> dict[str, Any]:
    """The IdP config dict as the service layer sees it."""
    async with app.app_context():
        config = OIDCService.get_idp_config(idp_id)
        assert config is not None
        return config


class TestIdTokenValidationThroughTheCallback:
    """A bad ID token is a stable 400 code and never a session."""

    @staticmethod
    async def _expect(browser, world, idp_b, code: str):
        resp = await login(browser, idp_b, world.b_oidc, world.b_admin_email)
        assert resp.status_code == 400, (await resp.get_data()).decode()
        body = await resp.get_json()
        assert body["error"] == code
        assert "access_token" not in body and "user" not in body
        assert "elder_access_token" not in "\n".join(resp.headers.get_all("Set-Cookie"))

    @pytest.mark.asyncio
    async def test_expired_id_token(self, browser, world, idp_b):
        idp_b.claim_overrides = {"exp": datetime.now(UTC) - timedelta(minutes=10)}
        await self._expect(browser, world, idp_b, "oidc_id_token_expired")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "overrides",
        [
            {"aud": "some-other-client"},
            {"iss": "https://evil.example.test"},
            {"exp": None},
            {"sub": None},
        ],
        ids=["wrong-aud", "wrong-iss", "no-exp", "no-sub"],
    )
    async def test_misaddressed_or_incomplete_id_token(
        self, browser, world, idp_b, overrides
    ):
        idp_b.claim_overrides = overrides
        await self._expect(browser, world, idp_b, "oidc_id_token_invalid")

    @pytest.mark.asyncio
    async def test_id_token_signed_by_a_key_not_in_the_jwks(
        self, browser, world, idp_b
    ):
        idp_b.signing_key = rsa.generate_private_key(65537, 2048)
        await self._expect(browser, world, idp_b, "oidc_id_token_invalid")

    @pytest.mark.asyncio
    async def test_unknown_signing_key_id(self, browser, world, idp_b):
        idp_b.jwks_kid = "rotated-away"
        await self._expect(browser, world, idp_b, "oidc_signing_key_not_found")

    @pytest.mark.asyncio
    async def test_token_response_without_an_id_token(self, browser, world, idp_b):
        idp_b.omit_id_token = True
        await self._expect(browser, world, idp_b, "oidc_id_token_missing")


class TestRoutesOnMisconfiguredProviders:
    """Provider-side gaps surface as stable codes on the real routes."""

    @pytest.mark.asyncio
    async def test_authorize_without_authorization_endpoint(
        self, browser, world, idp_b
    ):
        idp_b.omit_endpoints = {"authorization_endpoint"}
        b = await begin(browser, world.b_oidc)
        assert b.status == 400
        assert b.body["error"] == "oidc_provider_misconfigured"

    @pytest.mark.asyncio
    async def test_authorize_with_unreachable_provider(
        self, browser, world, monkeypatch
    ):
        def down(url: str, **_kwargs: Any):
            raise requests.ConnectionError("internal-host-secret")

        monkeypatch.setattr("apps.api.services.sso.oidc_service.requests.get", down)
        b = await begin(browser, world.b_oidc)
        assert b.status == 400
        assert b.body["error"] == "oidc_provider_unavailable"
        assert "internal-host-secret" not in str(b.body)

    @pytest.mark.asyncio
    async def test_logout_when_provider_has_no_end_session_endpoint(
        self, async_client, world, idp_b
    ):
        from sso_testkit import mint_token

        idp_b.omit_endpoints = {"end_session_endpoint"}
        token = mint_token(
            async_client.app,
            user_id=150,
            tenant_id=world.tenant_b,
            tenant_role="reader",
        )
        resp = await async_client.post(
            f"/api/v1/sso/oidc/logout/{world.b_oidc}",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert (await resp.get_json())["error"] == "oidc_end_session_unsupported"

    @pytest.mark.asyncio
    async def test_logout_url_carries_only_supplied_params(
        self, async_client, world, idp_b
    ):
        from sso_testkit import mint_token

        token = mint_token(
            async_client.app,
            user_id=151,
            tenant_id=world.tenant_b,
            tenant_role="reader",
        )
        bare = await async_client.post(
            f"/api/v1/sso/oidc/logout/{world.b_oidc}",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert (await bare.get_json())["end_session_endpoint"] == (
            "https://idp-b.example.test/logout"
        )

    @pytest.mark.asyncio
    async def test_authorize_unknown_idp_is_404_with_stable_code(
        self, browser, world, idp_b
    ):
        resp = await browser.get(
            f"{AUTHORIZE}/2000000000",
            query_string={"redirect_uri": CALLBACK_REDIRECT_URI},
        )
        assert resp.status_code == 404
        assert (await resp.get_json())["error"] == "oidc_idp_not_found"

    @pytest.mark.asyncio
    async def test_callback_for_a_tenant_scoped_idp_cannot_be_retargeted(
        self, browser, world, idp_b
    ):
        """The bound tenant always equals the config's tenant for a scoped IdP."""
        b = await begin(browser, world.b_oidc, tenant_id=world.tenant_b)
        code = idp_b.authenticate(b.authorization_url, world.b_admin_email)
        resp = await finish(browser, b, code, idp_id=world.b_oidc)
        assert resp.status_code == 200
        assert (await resp.get_json())["user"]["tenant_id"] == world.tenant_b


class TestServiceMissingEndpointsAndOutages:
    """Direct service calls: stable codes for every provider failure mode."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("endpoint", "call"),
        [
            ("token_endpoint", "exchange"),
            ("jwks_uri", "validate"),
            ("userinfo_endpoint", "userinfo"),
            ("token_endpoint", "refresh"),
            ("end_session_endpoint", "logout"),
        ],
    )
    async def test_missing_discovery_endpoint(self, app, world, idp_b, endpoint, call):
        idp_b.omit_endpoints = {endpoint}
        async with app.app_context():
            results = {
                "exchange": lambda: OIDCService.exchange_code_for_tokens(
                    world.b_oidc, "c", CALLBACK_REDIRECT_URI, code_verifier="v"
                ),
                "validate": lambda: OIDCService.validate_id_token(
                    world.b_oidc, "x.y.z", expected_nonce="n"
                ),
                "userinfo": lambda: OIDCService.get_userinfo(world.b_oidc, "tok"),
                "refresh": lambda: OIDCService.refresh_tokens(world.b_oidc, "r"),
                "logout": lambda: OIDCService.logout(world.b_oidc),
            }
            result = results[call]()
        expected = (
            "oidc_end_session_unsupported"
            if call == "logout"
            else "oidc_provider_misconfigured"
        )
        assert result == {"error": expected}

    @pytest.mark.asyncio
    async def test_unknown_idp_id_is_a_stable_error_for_every_call(self, app, world):
        async with app.app_context():
            missing = 2_000_000_000
            results = [
                OIDCService.exchange_code_for_tokens(
                    missing, "c", CALLBACK_REDIRECT_URI, code_verifier="v"
                ),
                OIDCService.validate_id_token(missing, "x.y.z", expected_nonce="n"),
                OIDCService.get_userinfo(missing, "tok"),
                OIDCService.refresh_tokens(missing, "r"),
                OIDCService.logout(missing),
            ]
        assert results == [{"error": "oidc_idp_not_found"}] * 5

    @pytest.mark.asyncio
    async def test_provider_outage_never_leaks_exception_text(
        self, app, world, monkeypatch
    ):
        def down(url: str, **_kwargs: Any):
            raise requests.ConnectionError("internal-host-secret-9")

        monkeypatch.setattr("apps.api.services.sso.oidc_service.requests.get", down)
        async with app.app_context():
            results = {
                "exchange": OIDCService.exchange_code_for_tokens(
                    world.b_oidc, "c", CALLBACK_REDIRECT_URI, code_verifier="v"
                ),
                "validate": OIDCService.validate_id_token(
                    world.b_oidc, "x.y.z", expected_nonce="n"
                ),
                "userinfo": OIDCService.get_userinfo(world.b_oidc, "tok"),
                "refresh": OIDCService.refresh_tokens(world.b_oidc, "r"),
                "logout": OIDCService.logout(world.b_oidc),
            }
        assert results == {
            "exchange": {"error": "oidc_token_exchange_failed"},
            "validate": {"error": "oidc_id_token_invalid"},
            "userinfo": {"error": "oidc_userinfo_failed"},
            "refresh": {"error": "oidc_refresh_failed"},
            "logout": {"error": "oidc_logout_failed"},
        }
        assert "internal-host-secret-9" not in str(results)

    @pytest.mark.asyncio
    async def test_tenant_lookup_helpers(self, app, world):
        async with app.app_context():
            own = OIDCService.get_idp_config_by_tenant(world.tenant_a)
            assert own is not None and own["tenant_id"] == world.tenant_a
            fallback = OIDCService.get_idp_config_by_tenant(world.tenant_sys)
            assert fallback is not None and fallback["tenant_id"] is None
            assert OIDCService.get_idp_config_by_tenant(None)["tenant_id"] is None


class TestResolveLoginTenantAndJit:
    """Argument validation and attribute-mapping behavior of the policy functions."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", [None, 0, -1, True, "5", 1.5])
    async def test_global_config_needs_a_positive_int_tenant(self, app, world, bad):
        async with app.app_context():
            config = {"tenant_id": None}
            with pytest.raises(OIDCLoginError) as exc:
                OIDCService.resolve_login_tenant(config, bad)
            assert exc.value.code == "oidc_tenant_required"
            assert exc.value.status == 400

    @pytest.mark.asyncio
    async def test_scoped_config_resolves_to_its_own_tenant(self, app, world):
        async with app.app_context():
            assert (
                OIDCService.resolve_login_tenant({"tenant_id": world.tenant_b}, None)
                == world.tenant_b
            )

    @pytest.mark.asyncio
    async def test_jit_refuses_an_idp_of_another_tenant(self, app, world):
        async with app.app_context():
            with pytest.raises(OIDCLoginError) as exc:
                OIDCService.jit_provision_user(
                    tenant_id=world.tenant_a,
                    idp_config={"tenant_id": world.tenant_b},
                    id_token_claims={"email": "x@example.test", "email_verified": True},
                )
        assert exc.value.code == "oidc_tenant_mismatch"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "claims",
        [{}, {"email": ""}, {"email": "   "}, {"email": 42}, {"email": "a" * 300}],
    )
    async def test_jit_requires_a_usable_email_claim(self, app, world, claims):
        async with app.app_context():
            with pytest.raises(OIDCLoginError) as exc:
                OIDCService.jit_provision_user(
                    tenant_id=world.tenant_b,
                    idp_config={"tenant_id": world.tenant_b},
                    id_token_claims=claims,
                )
        assert exc.value.code == "oidc_email_claim_missing"

    @pytest.mark.asyncio
    async def test_jit_honors_attribute_mappings_and_bounds_the_name(self, app, world):
        email = f"Mapped.User@{world.prefix}.TEST"
        async with app.app_context():
            user = OIDCService.jit_provision_user(
                tenant_id=world.tenant_b,
                idp_config={
                    "tenant_id": world.tenant_b,
                    "jit_provisioning_enabled": True,
                    "default_role": "maintainer",
                    "attribute_mappings": {"email": "mail", "full_name": "cn"},
                },
                id_token_claims={
                    "mail": f"  {email}  ",
                    "email_verified": True,
                    "cn": "N" * 400,
                },
            )
            db = current_app.db
            row = db(db.portal_users.id == user["id"]).select().first()
        assert user["email"] == email.lower()
        assert user["tenant_role"] == "maintainer"
        assert user["global_role"] is None and user["jit_provisioned"] is True
        assert row.tenant_id == world.tenant_b
        assert len(row.full_name) == 255

    @pytest.mark.asyncio
    async def test_jit_ignores_a_non_string_name_claim(self, app, world):
        email = f"noname@{world.prefix}.test"
        async with app.app_context():
            user = OIDCService.jit_provision_user(
                tenant_id=world.tenant_b,
                idp_config={
                    "tenant_id": world.tenant_b,
                    "jit_provisioning_enabled": True,
                },
                id_token_claims={
                    "email": email,
                    "email_verified": True,
                    "name": {"nested": "object"},
                },
            )
            db = current_app.db
            row = db(db.portal_users.id == user["id"]).select().first()
        assert row.full_name is None
        assert row.tenant_role == "reader"
