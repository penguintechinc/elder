"""Regression tests for the independent review of the SSO/SCIM/OIDC hardening.

Real Postgres + Redis and the real routes; only the external OIDC provider is
faked (``sso_testkit.FakeOidcProvider``). Fixtures and small helpers are shared
with ``test_sso_oidc_login_hardening.py``.

regression: oidc-redirect-uri, oidc-global-role-refresh, oidc-email-claim-mapping,
plus tenant-predicate and store-failure guards that earlier tests did not pin.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any
from urllib.parse import quote

import pytest
import pytest_asyncio
import redis
from quart import current_app
from sso_testkit import (
    AUTHORIZE,
    CALLBACK_REDIRECT_URI,
    FakeOidcProvider,
    allow_redirect_uris,
    begin,
    build_world,
    finish,
    insert_user,
    install_fake_providers,
    login,
    teardown_world,
)
from test_sso_oidc_login_hardening import (
    Providers,
    _assert_no_session,
    _user_by_email,
)

ALLOWED = CALLBACK_REDIRECT_URI


@pytest.fixture(autouse=True)
def _allowed_redirect_uris(app, monkeypatch):
    """The operator's OIDC redirect allowlist (unset would fail login closed)."""
    allow_redirect_uris(monkeypatch, app)


@pytest_asyncio.fixture
async def world(app):
    """Two tenants + global scope with users, SCIM configs and OIDC IdPs."""
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
    """Fake external IdPs matching the world's A, B and global OIDC configs."""
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


def _login_state_keys() -> int:
    """How many login-state records Redis currently holds."""
    client = redis.from_url(os.environ["REDIS_URL"])
    return len(list(client.scan_iter("elder:oidc:login:*")))


def _binding_cookie_set(resp: Any) -> bool:
    """Whether a response set the login binding cookie."""
    return any(
        h.startswith("elder_oidc_binding=") for h in resp.headers.get_all("Set-Cookie")
    )


class TestRedirectUriAllowlist:
    """regression: oidc-redirect-uri.

    ``/oidc/authorize`` accepted any http(s) ``redirect_uri``. An attacker who
    starts the login themselves (so they hold the state, binding cookie and PKCE
    verifier) with ``redirect_uri=https://attacker.example/cb`` can have a victim
    authenticate through that URL; against an IdP with loose redirect
    registration the code goes to the attacker, who completes ``/callback`` in
    their own browser and gets the victim's session. The server now only ever
    sends exact, operator-allowlisted callback URLs to an IdP.
    """

    @pytest.mark.asyncio
    async def test_attacker_initiated_login_is_refused_before_any_state_exists(
        self, browser, world, providers
    ):
        before = _login_state_keys()
        b = await begin(
            browser, world.b_oidc, redirect_uri="https://attacker.example/cb"
        )
        assert b.status == 400
        assert b.body["error"] == "oidc_redirect_uri_not_allowed"
        assert b.cookie is None
        assert _login_state_keys() == before

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "uri",
        [
            "https://attacker.example/cb",
            "http://elder.example.test/api/v1/sso/oidc/callback",
            "https://elder.example.test.evil.test/api/v1/sso/oidc/callback",
            "https://evil.test/https://elder.example.test/api/v1/sso/oidc/callback",
            "https://elder.example.test:8443/api/v1/sso/oidc/callback",
            "https://elder.example.test:443/api/v1/sso/oidc/callback",
            "https://elder.example.test/api/v1/sso/oidc/callback/",
            "https://elder.example.test/api/v1/sso/oidc/callback/../x",
            "https://elder.example.test/api/v1/sso/oidc/callback?next=https://evil.test",
            "https://elder.example.test/api/v1/sso/oidc/callback?",
            "https://elder.example.test//evil.test/cb",
            "https://elder.example.test@evil.test/api/v1/sso/oidc/callback",
            "https://user:pw@elder.example.test/api/v1/sso/oidc/callback",
            "https://elder.example.test/api/v1/sso/oidc/callback#frag",
            "HTTPS://elder.example.test/api/v1/sso/oidc/callback",
            "https://ELDER.example.test/api/v1/sso/oidc/callback",
            "https://elder.example.test/API/v1/sso/oidc/callback",
            "https://elder.example.test/redirect?url=https://evil.test",
            "https://elder.example.test/api/v1/sso/oidc/callback ",
            "https://elder.example.test/api/v1/sso/oidc/callback\t",
            "https://elder.example.test/api/v1/sso/oidc/callback%0d%0aX: y",
            "javascript:alert(1)",
            "//evil.test/cb",
            "/api/v1/sso/oidc/callback",
            "ftp://elder.example.test/api/v1/sso/oidc/callback",
        ],
    )
    async def test_every_variant_but_the_exact_entry_is_rejected(
        self, browser, world, providers, uri
    ):
        before = _login_state_keys()
        resp = await browser.get(
            f"{AUTHORIZE}/{world.b_oidc}", query_string={"redirect_uri": uri}
        )
        assert resp.status_code == 400
        body = await resp.get_json()
        assert body["error"] == "oidc_redirect_uri_not_allowed"
        assert uri not in json.dumps(body)
        assert _binding_cookie_set(resp) is False
        assert _login_state_keys() == before

    @pytest.mark.asyncio
    async def test_missing_redirect_uri_is_still_a_400(self, browser, world, providers):
        resp = await browser.get(f"{AUTHORIZE}/{world.b_oidc}")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_the_exact_entry_is_accepted(self, browser, world, providers):
        b = await begin(browser, world.b_oidc)
        assert b.status == 200
        assert f"redirect_uri={quote(ALLOWED, safe='')}" in b.authorization_url

    @pytest.mark.asyncio
    async def test_comma_json_and_list_forms_are_all_parsed(
        self, app, browser, world, providers, monkeypatch
    ):
        second = "https://spa.example.test/auth/callback"
        for value in (
            f"{ALLOWED}, {second}",
            json.dumps([ALLOWED, second]),
            [ALLOWED, second],
        ):
            monkeypatch.setitem(app.config, "OIDC_ALLOWED_REDIRECT_URIS", value)
            ok_first = await begin(browser, world.b_oidc)
            ok_second = await begin(browser, world.b_oidc, redirect_uri=second)
            denied = await begin(
                browser, world.b_oidc, redirect_uri="https://evil.example.test/cb"
            )
            assert (ok_first.status, ok_second.status, denied.status) == (200, 200, 400)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "   ",
            ",,",
            "[]",
            "[not json",
            '{"a": 1}',
            "*",
            "https://*.example.test/cb",
            "/relative/cb",
            "ftp://elder.example.test/cb",
            "https://user:pw@elder.example.test/cb",
            "https://elder.example.test/cb#frag",
            [],
            42,
        ],
        ids=repr,
    )
    async def test_unset_or_unusable_allowlist_fails_closed(
        self, app, browser, world, providers, monkeypatch, value
    ):
        monkeypatch.setitem(app.config, "OIDC_ALLOWED_REDIRECT_URIS", value)
        before = _login_state_keys()
        b = await begin(browser, world.b_oidc)
        assert b.status == 503
        assert b.body["error"] == "oidc_redirect_uris_not_configured"
        assert "OIDC_ALLOWED_REDIRECT_URIS" in b.body["message"]
        assert b.cookie is None
        assert _login_state_keys() == before

    @pytest.mark.asyncio
    async def test_invalid_entries_are_dropped_not_trusted(
        self, app, browser, world, providers, monkeypatch
    ):
        """A typo can only shrink the allowlist: the wildcard entry never matches."""
        monkeypatch.setitem(
            app.config,
            "OIDC_ALLOWED_REDIRECT_URIS",
            f"https://*.example.test/cb,{ALLOWED}",
        )
        wildcard = await begin(
            browser, world.b_oidc, redirect_uri="https://a.example.test/cb"
        )
        exact = await begin(browser, world.b_oidc)
        assert wildcard.status == 400
        assert exact.status == 200

    @pytest.mark.asyncio
    async def test_callback_requires_the_stored_uri_to_still_be_allowed(
        self, app, browser, world, providers, monkeypatch
    ):
        b = await begin(browser, world.b_oidc)
        code = providers.b.authenticate(b.authorization_url, world.b_admin_email)
        monkeypatch.setitem(
            app.config, "OIDC_ALLOWED_REDIRECT_URIS", "https://other.example.test/cb"
        )
        resp = await finish(browser, b, code, idp_id=world.b_oidc)
        assert resp.status_code == 400
        body = await resp.get_json()
        assert body["error"] == "oidc_redirect_uri_not_allowed"
        _assert_no_session(resp, body)

    @pytest.mark.asyncio
    async def test_callback_still_requires_the_stored_uri_to_equal_the_one_sent(
        self, app, browser, world, providers, monkeypatch
    ):
        other = "https://spa.example.test/auth/callback"
        monkeypatch.setitem(
            app.config, "OIDC_ALLOWED_REDIRECT_URIS", f"{ALLOWED},{other}"
        )
        b = await begin(browser, world.b_oidc)
        code = providers.b.authenticate(b.authorization_url, world.b_admin_email)
        resp = await finish(browser, b, code, idp_id=world.b_oidc, redirect_uri=other)
        assert resp.status_code == 400
        assert (await resp.get_json())["error"] == "oidc_redirect_uri_mismatch"


class TestEmailClaimMappingIsRefused:
    """regression: oidc-email-claim-mapping.

    ``email_verified`` vouches for the ``email`` claim. With
    ``attribute_mappings.email = preferred_username`` an attacker with a verified
    mailbox of their own and a chosen username signed in as tenant B's admin.
    """

    @staticmethod
    async def _set_mappings(app, idp_id: int, mappings: Any) -> None:
        async with app.app_context():
            db = current_app.db
            db(db.idp_configurations.id == idp_id).update(attribute_mappings=mappings)
            db.commit()

    @pytest.mark.asyncio
    async def test_mapped_username_cannot_pick_the_matched_account(
        self, app, browser, world, providers
    ):
        await self._set_mappings(app, world.b_oidc, {"email": "preferred_username"})
        providers.b.claim_overrides = {"preferred_username": world.b_admin_email}
        attacker = f"attacker@{world.prefix}.test"
        resp = await login(browser, providers.b, world.b_oidc, attacker)
        assert resp.status_code == 403, (await resp.get_data()).decode()
        body = await resp.get_json()
        assert body["error"] == "oidc_email_claim_mapping_unsupported"
        _assert_no_session(resp, body)
        victim = await _user_by_email(app, world.tenant_b, world.b_admin_email)
        assert victim["last_login_at"] is None
        assert await _user_by_email(app, world.tenant_b, attacker) is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "mapping", ["mail", "upn", "Email", "", None, ["email"], {"x": 1}, 7]
    )
    async def test_any_email_mapping_other_than_email_is_refused(
        self, app, browser, world, providers, mapping
    ):
        await self._set_mappings(app, world.b_oidc, {"email": mapping})
        resp = await login(browser, providers.b, world.b_oidc, world.b_admin_email)
        assert resp.status_code == 403
        assert (await resp.get_json())["error"] == (
            "oidc_email_claim_mapping_unsupported"
        )

    @pytest.mark.asyncio
    async def test_explicit_email_mapping_and_other_mappings_still_work(
        self, app, browser, world, providers
    ):
        await self._set_mappings(
            app, world.b_oidc, {"email": "email", "full_name": "nickname"}
        )
        providers.b.claim_overrides = {"nickname": "Nick Name"}
        email = f"mapped-ok@{world.prefix}.test"
        resp = await login(browser, providers.b, world.b_oidc, email)
        assert resp.status_code == 200, (await resp.get_data()).decode()
        row = await _user_by_email(app, world.tenant_b, email)
        assert row["full_name"] == "Nick Name"


class TestJitUserLookupIsTenantScoped:
    """The existing-account lookup must carry the tenant predicate.

    Without it, an email shared by two tenants resolves to whichever row sorts
    first -- here tenant A's -- and tenant B's IdP would sign in A's account.
    """

    @pytest.mark.asyncio
    async def test_shared_email_resolves_to_the_idps_own_tenant_row(
        self, app, browser, world, providers
    ):
        shared = f"shared@{world.prefix}.test"
        async with app.app_context():
            db = current_app.db
            a_id = insert_user(db, world.tenant_a, shared, tenant_role="maintainer")
            b_id = insert_user(db, world.tenant_b, shared, tenant_role="reader")
        resp = await login(browser, providers.b, world.b_oidc, shared)
        assert resp.status_code == 200, (await resp.get_data()).decode()
        user = (await resp.get_json())["user"]
        assert user["id"] == b_id != a_id
        assert user["tenant_id"] == world.tenant_b
        assert user["tenant_role"] == "reader"
        a_row = await _user_by_email(app, world.tenant_a, shared)
        b_row = await _user_by_email(app, world.tenant_b, shared)
        assert a_row["last_login_at"] is None
        assert b_row["last_login_at"] is not None

    @pytest.mark.asyncio
    async def test_email_existing_only_in_another_tenant_is_not_matched(
        self, app, browser, world, providers
    ):
        """a_user lives in tenant A; B's IdP must JIT a new B row, not sign in A's."""
        resp = await login(browser, providers.b, world.b_oidc, world.a_user_email)
        assert resp.status_code == 200, (await resp.get_data()).decode()
        user = (await resp.get_json())["user"]
        assert user["tenant_id"] == world.tenant_b
        assert user["id"] != world.a_user_id
        assert user["jit_provisioned"] is True
        a_row = await _user_by_email(app, world.tenant_a, world.a_user_email)
        assert a_row["last_login_at"] is None


class _FailingPipeline:
    """A Redis pipeline whose EXEC dies (the store failing mid-consume)."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def get(self, *_a: Any, **_k: Any) -> None:
        """Queue nothing."""

    def delete(self, *_a: Any, **_k: Any) -> None:
        """Queue nothing."""

    def execute(self, *_a: Any, **_k: Any) -> Any:
        """Fail like a dropped connection."""
        raise self._exc


class _FailingClient:
    """A cached state-store client whose pipelines fail."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def pipeline(self, *_a: Any, **_k: Any) -> _FailingPipeline:
        """Hand out a pipeline that fails on EXEC."""
        return _FailingPipeline(self._exc)


class TestStateStoreFailureMidConsume:
    """A store failure during the callback is a 503, never a misleading 400."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "exc",
        [
            redis.exceptions.ConnectionError("boom"),
            redis.exceptions.TimeoutError("slow"),
            TimeoutError("socket timed out"),
            ConnectionResetError("reset"),
        ],
        ids=lambda e: type(e).__name__,
    )
    async def test_consume_failure_is_503_and_drops_the_client(
        self, app, browser, world, providers, monkeypatch, exc
    ):
        from apps.api.services.sso import oidc_state

        b = await begin(browser, world.b_oidc)
        code = providers.b.authenticate(b.authorization_url, world.b_admin_email)
        monkeypatch.setitem(
            app.extensions, oidc_state.STATE_STORE_EXT, _FailingClient(exc)
        )
        resp = await finish(browser, b, code, idp_id=world.b_oidc)
        assert resp.status_code == 503, (await resp.get_data()).decode()
        body = await resp.get_json()
        assert body["error"] == "oidc_state_store_unavailable"
        _assert_no_session(resp, body)
        assert oidc_state.STATE_STORE_EXT not in app.extensions

    @pytest.mark.asyncio
    async def test_overlong_state_is_a_400_not_a_store_hit(
        self, browser, world, providers
    ):
        b = await begin(browser, world.b_oidc)
        code = providers.b.authenticate(b.authorization_url, world.b_admin_email)
        resp = await finish(browser, b, code, idp_id=world.b_oidc, state="s" * 300)
        assert resp.status_code == 400
        assert (await resp.get_json())["error"] == "oidc_state_invalid"

    @pytest.mark.asyncio
    async def test_corrupt_state_record_is_a_400(self, browser, world, providers):
        b = await begin(browser, world.b_oidc)
        client = redis.from_url(os.environ["REDIS_URL"])
        # Only this login's record (key = sha256 of its state), keeping its TTL.
        key = f"elder:oidc:login:{hashlib.sha256(b.state.encode()).hexdigest()}"
        assert client.set(key, b"{not json", xx=True, keepttl=True)
        code = providers.b.authenticate(b.authorization_url, world.b_admin_email)
        resp = await finish(browser, b, code, idp_id=world.b_oidc)
        assert resp.status_code == 400
        assert (await resp.get_json())["error"] == "oidc_state_invalid"
