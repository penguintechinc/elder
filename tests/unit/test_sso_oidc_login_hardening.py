"""OIDC login hardening regression tests (real Postgres + Redis, real routes).

Only the *external* OIDC provider is faked (``sso_testkit.FakeOidcProvider``).

regression: oidc-global-role-carry -- a tenant-A admin's own OIDC IdP could
assert the platform admin's email (that user's row lives in tenant A) and the
callback minted a session carrying the stored ``global_role='admin'``.
regression: oidc-null-tenant -- a global OIDC config JIT-created a
``portal_users`` row with a NULL tenant (``idp_config.get('tenant_id', 1)``).
regression: oidc-refresh-userinfo-logout-ownership -- these routes took any
``idp_id`` and called the (other tenant's / global) IdP with its client secret.
regression: oidc-login-csrf -- the callback read ``state`` and discarded it and
never checked the ID token ``nonce``, so an unauthenticated GET set auth cookies.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import jwt
import pytest
import pytest_asyncio
import redis
from quart import current_app
from sso_testkit import (
    AUTHORIZE,
    BINDING_COOKIE,
    CALLBACK,
    CALLBACK_REDIRECT_URI,
    Begun,
    FakeOidcProvider,
    begin,
    build_world,
    finish,
    install_fake_providers,
    login,
    set_tenant_settings,
    teardown_world,
)


@dataclass(slots=True)
class Providers:
    """The three fake external IdPs matching the world's three OIDC configs."""

    a: FakeOidcProvider
    b: FakeOidcProvider
    g: FakeOidcProvider


@pytest.fixture(autouse=True)
def _patch_login_required(mocker):
    """``POST /idp`` also carries ``@login_required``; stub identity auth."""
    mocker.patch(
        "apps.api.auth.decorators.get_current_user",
        return_value=MagicMock(id=1, is_superuser=False),
    )


@pytest_asyncio.fixture
async def world(app):
    """Two tenants + global scope, each with a SCIM config, users and an OIDC IdP."""
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
def providers(monkeypatch) -> Providers:
    """Fake external IdPs for A (attacker-controlled), B and the global IdP."""
    p = Providers(
        a=FakeOidcProvider(
            issuer="https://idp-a.example.test",
            client_id="client-a",
            client_secret="SECRET-A-oidc",
            kid="a-key",
        ),
        b=FakeOidcProvider(
            issuer="https://idp-b.example.test",
            client_id="client-b",
            client_secret="SECRET-B-oidc",
            kid="b-key",
        ),
        g=FakeOidcProvider(
            issuer="https://idp-g.example.test",
            client_id="client-g",
            client_secret="SECRET-G-oidc",
            kid="g-key",
        ),
    )
    install_fake_providers(monkeypatch, [p.a, p.b, p.g])
    return p


@pytest_asyncio.fixture
async def browser(app):
    """A cookie-less client; tests carry the binding cookie explicitly."""
    async with app.test_client(use_cookies=False) as client:
        yield client


def _claims(app, token: str) -> dict[str, Any]:
    """Decode an Elder access token (HS256) issued by the callback."""
    secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
    return jwt.decode(token, secret, algorithms=["HS256"])


async def _null_tenant_users(app) -> int:
    """Count ``portal_users`` rows with a NULL tenant."""
    async with app.app_context():
        db = current_app.db
        return db(db.portal_users.tenant_id == None).count()  # noqa: E711


async def _user_by_email(app, tenant_id: int, email: str) -> Any:
    """The tenant's user row for ``email`` as a dict, or None."""
    async with app.app_context():
        db = current_app.db
        row = (
            db(
                (db.portal_users.email == email.lower())
                & (db.portal_users.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        return row.as_dict() if row else None


def _assert_no_session(resp, body: dict[str, Any]) -> None:
    """A denied login must carry no tokens, user object or auth cookies."""
    assert "access_token" not in body and "user" not in body
    joined = "\n".join(resp.headers.get_all("Set-Cookie"))
    assert "elder_access_token" not in joined
    assert "elder_refresh_token" not in joined


class TestLoginHappyPath:
    """Harness sanity + the shape of a correct login (so denials fail for the right reason)."""

    @pytest.mark.asyncio
    async def test_authorize_issues_state_nonce_pkce_and_binding_cookie(
        self, browser, world, providers
    ):
        b = await begin(browser, world.b_oidc)
        assert b.status == 200
        assert b.cookie and len(b.cookie) >= 32
        url = b.authorization_url
        assert url.startswith("https://idp-b.example.test/authorize?")
        assert "nonce=" in url
        assert "code_challenge=" in url and "code_challenge_method=S256" in url
        assert f"state={b.state}" in url
        assert len(b.state) >= 32

    @pytest.mark.asyncio
    async def test_authorize_response_is_not_cacheable_and_cookie_is_hardened(
        self, browser, world, providers
    ):
        resp = await browser.get(
            f"{AUTHORIZE}/{world.b_oidc}",
            query_string={"redirect_uri": CALLBACK_REDIRECT_URI},
        )
        assert "no-store" in resp.headers.get("Cache-Control", "")
        header = next(
            h
            for h in resp.headers.get_all("Set-Cookie")
            if h.startswith(f"{BINDING_COOKIE}=")
        )
        assert "HttpOnly" in header
        assert "SameSite=Lax" in header
        assert "Path=/api/v1/sso/oidc" in header
        assert "Max-Age=" in header

    @pytest.mark.asyncio
    async def test_tenant_idp_login_for_existing_user(
        self, app, browser, world, providers
    ):
        resp = await login(browser, providers.b, world.b_oidc, world.b_admin_email)
        assert resp.status_code == 200, (await resp.get_data()).decode()
        body = await resp.get_json()
        assert body["user"]["tenant_id"] == world.tenant_b
        assert body["user"]["tenant_role"] == "admin"
        assert body["user"]["global_role"] is None
        claims = _claims(app, body["access_token"])
        assert claims["tenant_id"] == world.tenant_b
        assert claims["global_role"] is None
        joined = "\n".join(resp.headers.get_all("Set-Cookie"))
        assert "elder_access_token=" in joined
        assert "elder_refresh_token=" in joined
        # The single-use binding cookie is cleared once the flow completes.
        assert f"{BINDING_COOKIE}=;" in joined or f'{BINDING_COOKIE}=""' in joined

    @pytest.mark.asyncio
    async def test_callback_idp_id_and_redirect_uri_are_optional(
        self, browser, world, providers
    ):
        b = await begin(browser, world.b_oidc)
        code = providers.b.authenticate(b.authorization_url, world.b_admin_email)
        resp = await finish(browser, b, code, redirect_uri=None)
        assert resp.status_code == 200, (await resp.get_data()).decode()

    @pytest.mark.asyncio
    async def test_tenant_idp_jit_creates_reader_in_idp_tenant(
        self, app, browser, world, providers
    ):
        email = f"newbie@{world.prefix}.test"
        resp = await login(browser, providers.b, world.b_oidc, email)
        assert resp.status_code == 200, (await resp.get_data()).decode()
        row = await _user_by_email(app, world.tenant_b, email)
        assert row["tenant_role"] == "reader"
        assert row["global_role"] is None
        assert row["password_hash"] is None

    @pytest.mark.asyncio
    async def test_jit_disabled_still_logs_in_existing_but_refuses_new_users(
        self, app, browser, world, providers
    ):
        async with app.app_context():
            db = current_app.db
            db(db.idp_configurations.id == world.b_oidc).update(
                jit_provisioning_enabled=False
            )
            db.commit()
        ok = await login(browser, providers.b, world.b_oidc, world.b_member_email)
        assert ok.status_code == 200
        email = f"stranger@{world.prefix}.test"
        denied = await login(browser, providers.b, world.b_oidc, email)
        assert denied.status_code == 403
        body = await denied.get_json()
        assert body["error"] == "oidc_user_not_provisioned"
        _assert_no_session(denied, body)
        assert await _user_by_email(app, world.tenant_b, email) is None

    @pytest.mark.asyncio
    async def test_deactivated_user_cannot_sign_in(
        self, app, browser, world, providers
    ):
        """A SCIM-deprovisioned account must not be revived by an SSO login."""
        async with app.app_context():
            db = current_app.db
            db(db.portal_users.id == world.b_member_id).update(is_active=False)
            db.commit()
        resp = await login(browser, providers.b, world.b_oidc, world.b_member_email)
        assert resp.status_code == 403
        body = await resp.get_json()
        assert body["error"] == "oidc_account_deactivated"
        _assert_no_session(resp, body)

    @pytest.mark.asyncio
    async def test_email_claim_must_be_a_usable_string(self, browser, world, providers):
        b = await begin(browser, world.b_oidc)
        code = providers.b.authenticate(b.authorization_url, "")
        resp = await finish(browser, b, code, idp_id=world.b_oidc)
        assert resp.status_code == 400
        assert (await resp.get_json())["error"] == "oidc_email_claim_missing"

    @pytest.mark.asyncio
    async def test_userinfo_sub_mismatch_is_ignored(self, browser, world, providers):
        """A userinfo document for a different subject must not override the ID token."""
        providers.b.userinfo_claims = {
            "sub": "someone-else",
            "email": world.a_global_admin_email,
        }
        resp = await login(browser, providers.b, world.b_oidc, world.b_admin_email)
        assert resp.status_code == 200
        assert (await resp.get_json())["user"]["email"] == world.b_admin_email

    @pytest.mark.asyncio
    async def test_userinfo_for_same_sub_is_merged(self, browser, world, providers):
        providers.b.userinfo_claims = {
            "sub": f"sub-{world.b_admin_email}",
            "name": "Admin B",
        }
        resp = await login(browser, providers.b, world.b_oidc, world.b_admin_email)
        assert resp.status_code == 200


class TestCallbackStateNonceBinding:
    """regression: oidc-login-csrf."""

    @pytest.mark.asyncio
    async def test_callback_without_state_is_rejected(self, browser, world, providers):
        b = await begin(browser, world.b_oidc)
        code = providers.b.authenticate(b.authorization_url, world.b_admin_email)
        resp = await finish(browser, b, code, idp_id=world.b_oidc, state=None)
        assert resp.status_code == 400, (await resp.get_data()).decode()
        body = await resp.get_json()
        assert body["error"] == "oidc_state_missing"
        _assert_no_session(resp, body)

    @pytest.mark.asyncio
    async def test_login_csrf_victim_browser_without_binding_cookie(
        self, browser, world, providers
    ):
        """Attacker completes a flow, then lures the victim's browser to the callback.

        The victim's browser never started the flow, so it has no binding cookie:
        the attacker's code+state must not log the victim into the attacker's session.
        """
        b = await begin(browser, world.b_oidc)
        code = providers.b.authenticate(b.authorization_url, world.b_admin_email)
        resp = await finish(browser, b, code, idp_id=world.b_oidc, cookie=None)
        assert resp.status_code == 400, (await resp.get_data()).decode()
        body = await resp.get_json()
        assert body["error"] == "oidc_binding_missing"
        _assert_no_session(resp, body)

    @pytest.mark.asyncio
    async def test_binding_cookie_from_another_flow_is_rejected(
        self, browser, world, providers
    ):
        mine = await begin(browser, world.b_oidc)
        theirs = await begin(browser, world.b_oidc)
        code = providers.b.authenticate(mine.authorization_url, world.b_admin_email)
        resp = await finish(
            browser, mine, code, idp_id=world.b_oidc, cookie=theirs.cookie
        )
        assert resp.status_code == 400
        body = await resp.get_json()
        assert body["error"] == "oidc_binding_mismatch"
        _assert_no_session(resp, body)
        # The mismatched attempt burned the state: the genuine owner must restart.
        retry = await finish(browser, mine, code, idp_id=world.b_oidc)
        assert retry.status_code == 400

    @pytest.mark.asyncio
    async def test_forged_state_is_rejected(self, browser, world, providers):
        b = await begin(browser, world.b_oidc)
        code = providers.b.authenticate(b.authorization_url, world.b_admin_email)
        resp = await finish(browser, b, code, idp_id=world.b_oidc, state="x" * 43)
        assert resp.status_code == 400
        assert (await resp.get_json())["error"] == "oidc_state_invalid"

    @pytest.mark.asyncio
    async def test_state_is_single_use(self, browser, world, providers):
        b = await begin(browser, world.b_oidc)
        code = providers.b.authenticate(b.authorization_url, world.b_admin_email)
        first = await finish(browser, b, code, idp_id=world.b_oidc)
        assert first.status_code == 200
        code2 = providers.b.authenticate(b.authorization_url, world.b_admin_email)
        replay = await finish(browser, b, code2, idp_id=world.b_oidc)
        assert replay.status_code == 400
        assert (await replay.get_json())["error"] == "oidc_state_invalid"

    @pytest.mark.asyncio
    async def test_state_is_bound_to_its_idp(self, browser, world, providers):
        b = await begin(browser, world.a_oidc)
        code = providers.a.authenticate(b.authorization_url, world.a_user_email)
        resp = await finish(browser, b, code, idp_id=world.b_oidc)
        assert resp.status_code == 400
        assert (await resp.get_json())["error"] == "oidc_idp_mismatch"

    @pytest.mark.asyncio
    async def test_state_is_bound_to_its_redirect_uri(self, browser, world, providers):
        b = await begin(browser, world.b_oidc)
        code = providers.b.authenticate(b.authorization_url, world.b_admin_email)
        resp = await finish(
            browser,
            b,
            code,
            idp_id=world.b_oidc,
            redirect_uri="https://evil.example.test/cb",
        )
        assert resp.status_code == 400
        assert (await resp.get_json())["error"] == "oidc_redirect_uri_mismatch"

    @pytest.mark.asyncio
    async def test_id_token_nonce_mismatch_is_rejected(self, browser, world, providers):
        providers.b.nonce_override = "attacker-chosen-nonce"
        resp = await login(browser, providers.b, world.b_oidc, world.b_admin_email)
        assert resp.status_code == 400, (await resp.get_data()).decode()
        body = await resp.get_json()
        assert body["error"] == "oidc_nonce_mismatch"
        _assert_no_session(resp, body)

    @pytest.mark.asyncio
    async def test_id_token_without_nonce_is_rejected(self, browser, world, providers):
        providers.b.omit_nonce = True
        resp = await login(browser, providers.b, world.b_oidc, world.b_admin_email)
        assert resp.status_code == 400
        assert (await resp.get_json())["error"] == "oidc_nonce_mismatch"

    @pytest.mark.asyncio
    async def test_pkce_verifier_is_sent_at_token_exchange(
        self, browser, world, providers
    ):
        """The fake IdP rejects the exchange unless the verifier matches the challenge."""
        b = await begin(browser, world.b_oidc)
        grant_code = providers.b.authenticate(b.authorization_url, world.b_admin_email)
        grant = providers.b.grants[grant_code]
        assert grant["code_challenge"] and grant["code_challenge_method"] == "S256"
        resp = await finish(browser, b, grant_code, idp_id=world.b_oidc)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_state_record_never_stores_raw_state_or_binding(
        self, app, browser, world, providers
    ):
        b = await begin(browser, world.b_oidc)
        redis_client = redis.from_url(os.environ["REDIS_URL"])
        keys = [k.decode() for k in redis_client.scan_iter("elder:oidc:login:*")]
        assert keys, "login state must live in Redis"
        assert all(b.state not in k for k in keys)
        values = [redis_client.get(k).decode() for k in keys]
        assert all(b.cookie not in v for v in values)
        assert all(b.state not in v for v in values)
        ttl = min(redis_client.ttl(k) for k in keys)
        assert 0 < ttl <= 600

    @pytest.mark.asyncio
    async def test_client_supplied_state_is_not_used(self, browser, world, providers):
        b = await begin(browser, world.b_oidc, state="attacker-chosen-state")
        assert b.status == 200
        assert b.state != "attacker-chosen-state"
        assert "attacker-chosen-state" not in b.authorization_url

    @pytest.mark.asyncio
    async def test_callback_requires_code_and_surfaces_idp_errors_generically(
        self, browser, world, providers
    ):
        b = await begin(browser, world.b_oidc)
        resp = await browser.get(
            CALLBACK,
            query_string={
                "error": "access_denied",
                "error_description": "<script>alert(1)</script>",
                "state": b.state,
            },
        )
        assert resp.status_code == 400
        raw = (await resp.get_data()).decode()
        assert "<script>" not in raw
        missing = await finish(browser, b, "", idp_id=world.b_oidc)
        assert missing.status_code == 400

    @pytest.mark.asyncio
    async def test_state_store_down_fails_closed_on_authorize_and_callback(
        self, app, browser, world, providers, monkeypatch
    ):
        from apps.api.services.sso import oidc_state

        b = await begin(browser, world.b_oidc)
        code = providers.b.authenticate(b.authorization_url, world.b_admin_email)
        monkeypatch.setitem(app.config, "REDIS_URL", "redis://127.0.0.1:1/0")
        monkeypatch.delitem(app.extensions, oidc_state.STATE_STORE_EXT, raising=False)
        down_authorize = await begin(browser, world.b_oidc)
        assert down_authorize.status == 503
        assert down_authorize.body["error"] == "oidc_state_store_unavailable"
        down_callback = await finish(browser, b, code, idp_id=world.b_oidc)
        assert down_callback.status_code == 503
        _assert_no_session(down_callback, await down_callback.get_json())

    @pytest.mark.asyncio
    async def test_state_store_failure_drops_cached_client_and_recovers(
        self, app, browser, world, providers, monkeypatch
    ):
        from redis.exceptions import ConnectionError as RedisConnectionError

        from apps.api.services.sso import oidc_state

        class Broken:
            def pipeline(self, *_a: Any, **_k: Any) -> Any:
                raise RedisConnectionError("boom")

            def set(self, *_a: Any, **_k: Any) -> Any:
                raise RedisConnectionError("boom")

        monkeypatch.setitem(app.extensions, oidc_state.STATE_STORE_EXT, Broken())
        down = await begin(browser, world.b_oidc)
        assert down.status == 503
        assert oidc_state.STATE_STORE_EXT not in app.extensions
        up = await begin(browser, world.b_oidc)
        assert up.status == 200

    @pytest.mark.asyncio
    async def test_redis_url_missing_fails_closed(
        self, app, browser, world, providers, monkeypatch
    ):
        from apps.api.services.sso import oidc_state

        monkeypatch.setitem(app.config, "REDIS_URL", "")
        monkeypatch.delitem(app.extensions, oidc_state.STATE_STORE_EXT, raising=False)
        resp = await begin(browser, world.b_oidc)
        assert resp.status == 503

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "redirect_uri",
        ["javascript:alert(1)", "/relative/path", "ftp://x.example.test/cb", "http://"],
    )
    async def test_authorize_rejects_unsafe_redirect_uri(
        self, browser, world, providers, redirect_uri
    ):
        resp = await browser.get(
            f"{AUTHORIZE}/{world.b_oidc}", query_string={"redirect_uri": redirect_uri}
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_authorize_requires_redirect_uri_and_known_idp(
        self, browser, world, providers
    ):
        missing = await browser.get(f"{AUTHORIZE}/{world.b_oidc}")
        assert missing.status_code == 400
        unknown = await browser.get(
            f"{AUTHORIZE}/2000000000",
            query_string={"redirect_uri": CALLBACK_REDIRECT_URI},
        )
        assert unknown.status_code == 404

    @pytest.mark.asyncio
    async def test_idp_deactivated_between_authorize_and_callback(
        self, app, browser, world, providers
    ):
        b = await begin(browser, world.b_oidc)
        code = providers.b.authenticate(b.authorization_url, world.b_admin_email)
        async with app.app_context():
            db = current_app.db
            db(db.idp_configurations.id == world.b_oidc).update(is_active=False)
            db.commit()
        resp = await finish(browser, b, code, idp_id=world.b_oidc)
        assert resp.status_code == 404
        assert (await resp.get_json())["error"] == "oidc_idp_not_found"

    @pytest.mark.asyncio
    async def test_upstream_failures_never_reflect_provider_text(
        self, browser, world, providers
    ):
        """A bad code at the token endpoint yields a stable code, not IdP error text."""
        b = await begin(browser, world.b_oidc)
        resp = await finish(browser, b, "bogus-code", idp_id=world.b_oidc)
        assert resp.status_code == 400
        body = await resp.get_json()
        assert body["error"] == "oidc_token_exchange_failed"
        assert "invalid_grant" not in json.dumps(body)


class TestEmailMustBeVerifiedByTheIdp:
    """regression: oidc-email-unverified.

    The callback matched an existing account on the asserted email, and JIT
    stamped ``email_verified=True``, without ever checking the IdP's own
    ``email_verified`` claim -- so an IdP that does not verify addresses could
    assert a victim's email and take over that account.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "claim",
        [False, None, "true", "True", 1, "yes", [True], {"v": True}],
        ids=[
            "false",
            "absent",
            "str-true",
            "str-True",
            "int-1",
            "str-yes",
            "list",
            "dict",
        ],
    )
    async def test_existing_account_is_not_matched_without_a_strict_true(
        self, app, browser, world, providers, claim
    ):
        providers.b.claim_overrides = {"email_verified": claim}
        resp = await login(browser, providers.b, world.b_oidc, world.b_admin_email)
        assert resp.status_code == 403, (await resp.get_data()).decode()
        body = await resp.get_json()
        assert body["error"] == "oidc_email_not_verified"
        _assert_no_session(resp, body)
        # The account was neither matched nor touched.
        row = await _user_by_email(app, world.tenant_b, world.b_admin_email)
        assert row["last_login_at"] is None

    @pytest.mark.asyncio
    async def test_jit_does_not_create_a_user_for_an_unverified_email(
        self, app, browser, world, providers
    ):
        providers.b.claim_overrides = {"email_verified": False}
        email = f"unverified-newbie@{world.prefix}.test"
        resp = await login(browser, providers.b, world.b_oidc, email)
        assert resp.status_code == 403
        assert (await resp.get_json())["error"] == "oidc_email_not_verified"
        assert await _user_by_email(app, world.tenant_b, email) is None

    @pytest.mark.asyncio
    async def test_verified_true_logs_in_and_jit_records_the_claim(
        self, app, browser, world, providers
    ):
        ok = await login(browser, providers.b, world.b_oidc, world.b_admin_email)
        assert ok.status_code == 200
        email = f"verified-newbie@{world.prefix}.test"
        created = await login(browser, providers.b, world.b_oidc, email)
        assert created.status_code == 200
        row = await _user_by_email(app, world.tenant_b, email)
        assert row["email_verified"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("id_token_claim", [False, None])
    async def test_verified_flag_is_taken_from_the_id_token_not_userinfo(
        self, browser, world, providers, id_token_claim
    ):
        providers.b.claim_overrides = {"email_verified": id_token_claim}
        providers.b.userinfo_claims = {
            "sub": f"sub-{world.b_admin_email}",
            "email": world.b_admin_email,
            "email_verified": True,
        }
        resp = await login(browser, providers.b, world.b_oidc, world.b_admin_email)
        assert resp.status_code == 403
        assert (await resp.get_json())["error"] == "oidc_email_not_verified"

    @pytest.mark.asyncio
    async def test_userinfo_cannot_substitute_the_email_the_flag_vouches_for(
        self, app, browser, world, providers
    ):
        """Attacker's verified email in the ID token; userinfo names the victim."""
        attacker_email = f"attacker@{world.prefix}.test"
        providers.b.userinfo_claims = {
            "sub": f"sub-{attacker_email}",
            "email": world.b_admin_email,
            "email_verified": True,
        }
        resp = await login(browser, providers.b, world.b_oidc, attacker_email)
        assert resp.status_code == 200, (await resp.get_data()).decode()
        user = (await resp.get_json())["user"]
        assert user["email"] == attacker_email
        assert user["tenant_role"] == "reader"
        victim = await _user_by_email(app, world.tenant_b, world.b_admin_email)
        assert victim["last_login_at"] is None

    @pytest.mark.asyncio
    async def test_global_idp_fallback_requires_a_verified_email_too(
        self, app, browser, world, providers
    ):
        async with app.app_context():
            set_tenant_settings(world.tenant_a, {"oidc_allow_global_idp": True})
        providers.g.claim_overrides = {"email_verified": False}
        resp = await login(
            browser,
            providers.g,
            world.g_oidc,
            world.a_user_email,
            tenant_id=world.tenant_a,
        )
        assert resp.status_code == 403
        assert (await resp.get_json())["error"] == "oidc_email_not_verified"

    @pytest.mark.asyncio
    async def test_denial_echoes_no_claims_in_body_or_logs(
        self, browser, world, providers, caplog
    ):
        import logging

        providers.b.claim_overrides = {"email_verified": False}
        with caplog.at_level(logging.DEBUG):
            resp = await login(browser, providers.b, world.b_oidc, world.b_admin_email)
        assert resp.status_code == 403
        raw = (await resp.get_data()).decode()
        body = json.loads(raw)
        assert set(body) == {"error", "message"}
        assert world.b_admin_email not in raw
        assert f"sub-{world.b_admin_email}" not in raw
        # Prove the denial really was logged (non-vacuous), then that the log
        # carries the stable code only -- no email, subject or claim values.
        assert "oidc_login_denied code=oidc_email_not_verified" in caplog.text
        assert world.b_admin_email not in caplog.text
        assert f"sub-{world.b_admin_email}" not in caplog.text


class TestTenantIdpCannotCarryGlobalRole:
    """regression: oidc-global-role-carry."""

    @pytest.mark.asyncio
    async def test_attacker_idp_cannot_assert_the_global_admin(
        self, app, browser, world, providers
    ):
        resp = await login(
            browser, providers.a, world.a_oidc, world.a_global_admin_email
        )
        assert resp.status_code == 403, (await resp.get_data()).decode()
        body = await resp.get_json()
        assert body["error"] == "oidc_tenant_idp_global_role_forbidden"
        _assert_no_session(resp, body)

    @pytest.mark.asyncio
    async def test_full_chain_tenant_admin_creates_own_idp_and_tries_the_global_admin(
        self, app, async_client, browser, world, monkeypatch
    ):
        """The reproduced exploit: create an IdP via the API, assert the platform admin."""
        evil = FakeOidcProvider(
            issuer="https://evil.example.test",
            client_id="evil-client",
            client_secret="evil-secret",
            kid="evil-key",
        )
        install_fake_providers(monkeypatch, [evil])
        created = await async_client.post(
            "/api/v1/sso/idp",
            json={
                "name": f"{world.prefix}-evil",
                "idp_type": "oidc",
                "oidc_client_id": "evil-client",
                "oidc_client_secret": "evil-secret",
                "oidc_issuer_url": "https://evil.example.test",
            },
            headers=world.auth("a_admin"),
        )
        assert created.status_code == 201, (await created.get_data()).decode()
        evil_id = (await created.get_json())["id"]
        resp = await login(browser, evil, evil_id, world.a_global_admin_email)
        assert resp.status_code == 403
        body = await resp.get_json()
        assert body["error"] == "oidc_tenant_idp_global_role_forbidden"
        _assert_no_session(resp, body)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stored_role", ["admin", "support", "maintainer"])
    async def test_any_stored_global_role_is_refused_on_a_tenant_idp(
        self, app, browser, world, providers, stored_role
    ):
        async with app.app_context():
            db = current_app.db
            db(db.portal_users.id == world.a_user_id).update(global_role=stored_role)
            db.commit()
        resp = await login(browser, providers.a, world.a_oidc, world.a_user_email)
        assert resp.status_code == 403
        assert (await resp.get_json())["error"] == (
            "oidc_tenant_idp_global_role_forbidden"
        )

    @pytest.mark.asyncio
    async def test_ordinary_tenant_user_session_has_no_global_role(
        self, app, browser, world, providers
    ):
        resp = await login(browser, providers.a, world.a_oidc, world.a_user_email)
        assert resp.status_code == 200
        body = await resp.get_json()
        assert body["user"]["global_role"] is None
        assert _claims(app, body["access_token"])["global_role"] is None

    @pytest.mark.asyncio
    async def test_global_idp_session_never_carries_stored_global_role(
        self, app, browser, world, providers
    ):
        async with app.app_context():
            set_tenant_settings(world.tenant_a, {"oidc_allow_global_idp": True})
        resp = await login(
            browser,
            providers.g,
            world.g_oidc,
            world.a_global_admin_email,
            tenant_id=world.tenant_a,
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()
        body = await resp.get_json()
        assert body["user"]["tenant_id"] == world.tenant_a
        assert body["user"]["global_role"] is None
        claims = _claims(app, body["access_token"])
        assert claims["global_role"] is None
        assert claims["tenant_id"] == world.tenant_a


class TestGlobalIdpRequiresTenantOptIn:
    """regression: oidc-null-tenant -- a global OIDC IdP needs an explicit target tenant."""

    @pytest.mark.asyncio
    async def test_global_idp_without_tenant_fails_closed(
        self, app, browser, world, providers
    ):
        before = await _null_tenant_users(app)
        b = await begin(browser, world.g_oidc)
        assert b.status == 400
        assert b.body["error"] == "oidc_tenant_required"
        assert b.cookie is None
        assert await _null_tenant_users(app) == before

    @pytest.mark.asyncio
    async def test_global_idp_unknown_tenant_looks_like_not_opted_in(
        self, browser, world, providers
    ):
        """No tenant-existence oracle on the unauthenticated authorize endpoint."""
        unknown = await begin(browser, world.g_oidc, tenant_id=2_000_000_000)
        known = await begin(browser, world.g_oidc, tenant_id=world.tenant_a)
        assert unknown.status == known.status == 403
        assert unknown.body == known.body
        assert unknown.body["error"] == "oidc_global_idp_not_enabled"

    @pytest.mark.asyncio
    async def test_global_idp_inactive_tenant_is_refused_even_if_opted_in(
        self, app, browser, world, providers
    ):
        async with app.app_context():
            db = current_app.db
            db(db.tenants.id == world.tenant_a).update(
                is_active=False, settings={"oidc_allow_global_idp": True}
            )
            db.commit()
        b = await begin(browser, world.g_oidc, tenant_id=world.tenant_a)
        assert b.status == 403
        assert b.body["error"] == "oidc_global_idp_not_enabled"

    @pytest.mark.asyncio
    async def test_global_idp_tenant_without_opt_in_is_403(
        self, browser, world, providers
    ):
        b = await begin(browser, world.g_oidc, tenant_id=world.tenant_a)
        assert b.status == 403
        assert b.body["error"] == "oidc_global_idp_not_enabled"
        assert b.cookie is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "value", ["true", "True", 1, "yes", "1", [True], {"a": 1}, None, False]
    )
    async def test_opt_in_must_be_the_strict_boolean_true(
        self, app, browser, world, providers, value
    ):
        async with app.app_context():
            set_tenant_settings(world.tenant_a, {"oidc_allow_global_idp": value})
        b = await begin(browser, world.g_oidc, tenant_id=world.tenant_a)
        assert b.status == 403
        assert b.body["error"] == "oidc_global_idp_not_enabled"

    @pytest.mark.asyncio
    async def test_saml_opt_in_key_does_not_enable_oidc(
        self, app, browser, world, providers
    ):
        async with app.app_context():
            set_tenant_settings(world.tenant_a, {"saml_allow_global_idp": True})
        b = await begin(browser, world.g_oidc, tenant_id=world.tenant_a)
        assert b.status == 403

    @pytest.mark.asyncio
    async def test_non_dict_tenant_settings_fail_closed(
        self, app, browser, world, providers
    ):
        async with app.app_context():
            set_tenant_settings(world.tenant_a, ["oidc_allow_global_idp"])  # type: ignore[arg-type]
        b = await begin(browser, world.g_oidc, tenant_id=world.tenant_a)
        assert b.status == 403

    @pytest.mark.asyncio
    async def test_opted_in_tenant_logs_in_existing_user_only(
        self, app, browser, world, providers
    ):
        async with app.app_context():
            set_tenant_settings(world.tenant_a, {"oidc_allow_global_idp": True})
        ok = await login(
            browser,
            providers.g,
            world.g_oidc,
            world.a_user_email,
            tenant_id=world.tenant_a,
        )
        assert ok.status_code == 200, (await ok.get_data()).decode()
        assert (await ok.get_json())["user"]["tenant_id"] == world.tenant_a

        before = await _null_tenant_users(app)
        email = f"fallback-newbie@{world.prefix}.test"
        denied = await login(
            browser, providers.g, world.g_oidc, email, tenant_id=world.tenant_a
        )
        assert denied.status_code == 403
        body = await denied.get_json()
        assert body["error"] == "oidc_user_not_provisioned"
        _assert_no_session(denied, body)
        # No JIT on the fallback: no row in A, and never a NULL-tenant row.
        assert await _user_by_email(app, world.tenant_a, email) is None
        assert await _null_tenant_users(app) == before

    @pytest.mark.asyncio
    async def test_global_idp_never_creates_users_even_with_jit_enabled(
        self, app, browser, world, providers
    ):
        async with app.app_context():
            set_tenant_settings(world.tenant_b, {"oidc_allow_global_idp": True})
            db = current_app.db
            db(db.idp_configurations.id == world.g_oidc).update(
                jit_provisioning_enabled=True
            )
            db.commit()
        email = f"jit-attempt@{world.prefix}.test"
        resp = await login(
            browser, providers.g, world.g_oidc, email, tenant_id=world.tenant_b
        )
        assert resp.status_code == 403
        assert await _user_by_email(app, world.tenant_b, email) is None

    @pytest.mark.asyncio
    async def test_target_tenant_comes_from_the_bound_state_not_the_callback(
        self, app, browser, world, providers
    ):
        async with app.app_context():
            set_tenant_settings(world.tenant_a, {"oidc_allow_global_idp": True})
        b = await begin(browser, world.g_oidc, tenant_id=world.tenant_a)
        code = providers.g.authenticate(b.authorization_url, world.a_user_email)
        resp = await finish(
            browser,
            b,
            code,
            idp_id=world.g_oidc,
            extra={"tenant_id": world.tenant_b},
        )
        assert resp.status_code == 200
        assert (await resp.get_json())["user"]["tenant_id"] == world.tenant_a

    @pytest.mark.asyncio
    async def test_opt_in_revoked_between_authorize_and_callback(
        self, app, browser, world, providers
    ):
        async with app.app_context():
            set_tenant_settings(world.tenant_a, {"oidc_allow_global_idp": True})
        b = await begin(browser, world.g_oidc, tenant_id=world.tenant_a)
        code = providers.g.authenticate(b.authorization_url, world.a_user_email)
        async with app.app_context():
            set_tenant_settings(world.tenant_a, {})
        resp = await finish(browser, b, code, idp_id=world.g_oidc)
        assert resp.status_code == 403
        assert (await resp.get_json())["error"] == "oidc_global_idp_not_enabled"

    @pytest.mark.asyncio
    async def test_tenant_scoped_idp_rejects_a_different_requested_tenant(
        self, browser, world, providers
    ):
        b = await begin(browser, world.b_oidc, tenant_id=world.tenant_a)
        assert b.status == 400
        assert b.body["error"] == "oidc_tenant_mismatch"
        same = await begin(browser, world.b_oidc, tenant_id=world.tenant_b)
        assert same.status == 200

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", ["abc", "-4", "0", "1.5", ""])
    async def test_malformed_tenant_id_is_400(self, browser, world, providers, bad):
        resp = await browser.get(
            f"{AUTHORIZE}/{world.g_oidc}",
            query_string={"redirect_uri": CALLBACK_REDIRECT_URI, "tenant_id": bad},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_no_null_tenant_user_is_ever_created(
        self, app, browser, world, providers
    ):
        """Drive every login shape and assert the NULL-tenant row count never moves."""
        before = await _null_tenant_users(app)
        async with app.app_context():
            set_tenant_settings(world.tenant_a, {"oidc_allow_global_idp": True})
        for provider, idp_id, tenant, email in (
            (providers.g, world.g_oidc, None, f"n1@{world.prefix}.test"),
            (providers.g, world.g_oidc, world.tenant_a, f"n2@{world.prefix}.test"),
            (providers.a, world.a_oidc, None, f"n3@{world.prefix}.test"),
            (providers.b, world.b_oidc, None, f"n4@{world.prefix}.test"),
        ):
            b = await begin(browser, idp_id, tenant_id=tenant)
            if b.status == 200:
                code = provider.authenticate(b.authorization_url, email)
                await finish(browser, b, code, idp_id=idp_id)
        assert await _null_tenant_users(app) == before

    @pytest.mark.asyncio
    async def test_jit_refuses_a_missing_tenant_directly(self, app, world):
        """Defense in depth: the service never inserts with no/invalid tenant."""
        from apps.api.services.sso import OIDCService
        from apps.api.services.sso.oidc_state import OIDCLoginError

        async with app.app_context():
            before = current_app.db(current_app.db.portal_users.id > 0).count()
            for bad in (None, 0, -1, True, "5"):
                with pytest.raises(OIDCLoginError) as exc:
                    OIDCService.jit_provision_user(
                        tenant_id=bad,  # type: ignore[arg-type]
                        idp_config={"tenant_id": None, "default_role": "reader"},
                        id_token_claims={"email": "x@example.test", "sub": "s"},
                    )
                assert exc.value.code == "oidc_tenant_required"
            assert current_app.db(current_app.db.portal_users.id > 0).count() == before


class TestRefreshUserinfoLogoutOwnership:
    """regression: oidc-refresh-userinfo-logout-ownership."""

    @pytest.mark.asyncio
    async def test_reader_cannot_refresh_against_another_tenants_idp(
        self, async_client, world, providers
    ):
        providers.b.refresh_tokens.add("b-refresh")
        resp = await async_client.post(
            f"/api/v1/sso/oidc/refresh/{world.b_oidc}",
            json={"refresh_token": "b-refresh"},
            headers=world.auth("a_reader"),
        )
        assert resp.status_code == 404, (await resp.get_data()).decode()
        # B's token endpoint (and so B's client secret) was never touched.
        assert providers.b.token_calls == []
        assert providers.b.discovery_calls == 0
        assert "access_token" not in (await resp.get_data()).decode()

    @pytest.mark.asyncio
    async def test_reader_cannot_use_another_tenants_userinfo(
        self, async_client, world, providers
    ):
        providers.b.userinfo_claims = {"sub": "x", "email": world.b_admin_email}
        resp = await async_client.get(
            f"/api/v1/sso/oidc/userinfo/{world.b_oidc}",
            headers={**world.auth("a_reader"), "X-OIDC-Access-Token": "tok"},
        )
        assert resp.status_code == 404
        assert providers.b.userinfo_calls == []
        assert providers.b.discovery_calls == 0

    @pytest.mark.asyncio
    async def test_reader_cannot_logout_via_another_tenants_idp(
        self, async_client, world, providers
    ):
        resp = await async_client.post(
            f"/api/v1/sso/oidc/logout/{world.b_oidc}",
            json={"id_token_hint": "h"},
            headers=world.auth("a_reader"),
        )
        assert resp.status_code == 404
        assert providers.b.discovery_calls == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("who", ["a_reader", "a_admin"])
    async def test_global_config_is_invisible_to_non_global_admins(
        self, async_client, world, providers, who
    ):
        for method, path, kwargs in (
            (
                "post",
                f"/api/v1/sso/oidc/refresh/{world.g_oidc}",
                {"json": {"refresh_token": "r"}},
            ),
            ("post", f"/api/v1/sso/oidc/logout/{world.g_oidc}", {"json": {}}),
            (
                "get",
                f"/api/v1/sso/oidc/userinfo/{world.g_oidc}",
                {"headers": {"X-OIDC-Access-Token": "t"}},
            ),
        ):
            headers = {**world.auth(who), **kwargs.pop("headers", {})}
            resp = await getattr(async_client, method)(path, headers=headers, **kwargs)
            assert resp.status_code == 404, (method, path)
        assert providers.g.discovery_calls == 0
        assert providers.g.token_calls == []

    @pytest.mark.asyncio
    async def test_404_body_matches_a_nonexistent_config(
        self, async_client, world, providers
    ):
        other = await async_client.post(
            f"/api/v1/sso/oidc/logout/{world.b_oidc}",
            json={},
            headers=world.auth("a_reader"),
        )
        missing = await async_client.post(
            f"/api/v1/sso/oidc/logout/{world.b_oidc + 100_000}",
            json={},
            headers=world.auth("a_reader"),
        )
        assert other.status_code == missing.status_code == 404
        a, m = await other.get_json(), await missing.get_json()
        assert a["error"].replace(str(world.b_oidc), "X") == m["error"].replace(
            str(world.b_oidc + 100_000), "X"
        )

    @pytest.mark.asyncio
    async def test_global_admin_may_use_any_config(
        self, async_client, world, providers
    ):
        providers.b.refresh_tokens.add("b-refresh")
        refreshed = await async_client.post(
            f"/api/v1/sso/oidc/refresh/{world.b_oidc}",
            json={"refresh_token": "b-refresh"},
            headers=world.auth("global_admin"),
        )
        assert refreshed.status_code == 200
        logout = await async_client.post(
            f"/api/v1/sso/oidc/logout/{world.g_oidc}",
            json={},
            headers=world.auth("global_admin"),
        )
        assert logout.status_code == 200
        assert (await logout.get_json())["end_session_endpoint"].startswith(
            "https://idp-g.example.test/logout"
        )

    @pytest.mark.asyncio
    async def test_own_tenant_flows_still_work(self, async_client, world, providers):
        providers.a.refresh_tokens.add("a-refresh")
        providers.a.userinfo_claims = {"sub": "s", "email": world.a_user_email}
        refresh = await async_client.post(
            f"/api/v1/sso/oidc/refresh/{world.a_oidc}",
            json={"refresh_token": "a-refresh"},
            headers=world.auth("a_reader"),
        )
        assert refresh.status_code == 200
        userinfo = await async_client.get(
            f"/api/v1/sso/oidc/userinfo/{world.a_oidc}",
            headers={**world.auth("a_reader"), "X-OIDC-Access-Token": "tok"},
        )
        assert userinfo.status_code == 200
        assert (await userinfo.get_json())["email"] == world.a_user_email
        logout = await async_client.post(
            f"/api/v1/sso/oidc/logout/{world.a_oidc}",
            json={
                "id_token_hint": "h",
                "post_logout_redirect_uri": "https://app.example.test/",
            },
            headers=world.auth("a_reader"),
        )
        assert logout.status_code == 200

    @pytest.mark.asyncio
    async def test_upstream_error_text_is_never_reflected(
        self, async_client, world, providers
    ):
        resp = await async_client.post(
            f"/api/v1/sso/oidc/refresh/{world.a_oidc}",
            json={"refresh_token": "not-a-real-refresh-token"},
            headers=world.auth("a_reader"),
        )
        assert resp.status_code == 400
        body = await resp.get_json()
        assert body == {"error": "oidc_refresh_failed"}

    @pytest.mark.asyncio
    async def test_userinfo_and_logout_errors_are_generic(
        self, async_client, world, providers, monkeypatch
    ):
        import requests

        def boom(url: str, **_kwargs: Any):
            raise requests.ConnectionError(f"secret-internal-host {url}")

        monkeypatch.setattr("apps.api.services.sso.oidc_service.requests.get", boom)
        userinfo = await async_client.get(
            f"/api/v1/sso/oidc/userinfo/{world.a_oidc}",
            headers={**world.auth("a_reader"), "X-OIDC-Access-Token": "tok"},
        )
        logout = await async_client.post(
            f"/api/v1/sso/oidc/logout/{world.a_oidc}",
            json={},
            headers=world.auth("a_reader"),
        )
        for resp in (userinfo, logout):
            assert resp.status_code == 400
            assert "secret-internal-host" not in (await resp.get_data()).decode()

    @pytest.mark.asyncio
    async def test_token_without_tenant_claim_fails_closed(
        self, async_client, world, providers
    ):
        from sso_testkit import mint_token

        token = mint_token(
            async_client.app, user_id=140, tenant_id=None, tenant_role="reader"
        )
        resp = await async_client.post(
            f"/api/v1/sso/oidc/logout/{world.a_oidc}",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_unauthenticated_is_401(self, async_client, world, providers):
        resp = await async_client.post(
            f"/api/v1/sso/oidc/logout/{world.a_oidc}", json={}
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_userinfo_requires_access_token_header(
        self, async_client, world, providers
    ):
        resp = await async_client.get(
            f"/api/v1/sso/oidc/userinfo/{world.a_oidc}", headers=world.auth("a_reader")
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_refresh_requires_a_body_and_token(
        self, async_client, world, providers
    ):
        no_body = await async_client.post(
            f"/api/v1/sso/oidc/refresh/{world.a_oidc}",
            headers=world.auth("a_reader"),
        )
        no_token = await async_client.post(
            f"/api/v1/sso/oidc/refresh/{world.a_oidc}",
            json={},
            headers=world.auth("a_reader"),
        )
        assert no_body.status_code == 400
        assert no_token.status_code == 400
