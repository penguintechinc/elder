# ruff: noqa: F811  (pytest fixtures imported from the sibling module are re-declared as test params)
"""Regression tests for the adversarial-review findings on the SAML ACS (F2-F9).

Same approach as ``test_saml_acs_processing.py``: real xmlsec-signed assertions
from the in-process IdP, real Redis and Postgres. Each class pins one finding
so it cannot silently regress.
"""

import asyncio
import copy
import os
import socket
import time
from unittest.mock import patch

import jwt
import pytest
import redis as redis_lib
from lxml import etree

from apps.api.api.v1.portal_auth import generate_tokens
from apps.api.services.sso import saml_service as saml_service_module
from apps.api.services.sso.saml_service import (
    _RETURN_TO_RE,
    GLOBAL_IDP_OPT_IN_KEY,
    SAMLError,
    SAMLService,
)
from apps.api.utils.async_utils import run_in_threadpool
from tests.unit import saml_test_idp as idp
from tests.unit.test_saml_acs_processing import (  # noqa: F401
    SP_BASE,
    STATE_COOKIE,
    _cookie_value,
    _set_cookies,
    _spec,
    _use_global_idp,
    _user,
    _validate,
    enterprise_tier,
    env,
    keys,
)

NS = idp.NS_SAML
BEARER = idp.BEARER
HOLDER_OF_KEY = "urn:oasis:names:tc:SAML:2.0:cm:holder-of-key"


async def _saml_login(env, client, email, keys, **spec_kw):
    """Run a full SP-initiated login for tenant A and return the ACS response."""
    login = await client.get(f"/api/v1/sso/saml/login/{env.tenant_a}")
    assert login.status_code == 200, await login.get_data()
    token = _cookie_value(_set_cookies(login)[STATE_COOKIE])
    request_id = idp.request_id_from_redirect((await login.get_json())["sso_url"])
    spec = _spec(
        env,
        request_id,
        name_id=email,
        attributes={"email": [email]},
        **spec_kw,
    )
    return await client.post(
        f"/api/v1/sso/saml/acs/{env.tenant_a}",
        form={"SAMLResponse": idp.signed_response_b64(spec, keys)},
        headers={"Cookie": f"{STATE_COOKIE}={token}"},
    )


def _jwt_secret(env) -> str:
    return env.app.config.get("JWT_SECRET_KEY") or env.app.config["SECRET_KEY"]


@pytest.mark.asyncio
class TestF2TenantScopedIdpCannotMintGlobalRole:
    """A tenant-scoped IdP can never authenticate a user holding a global_role.

    The REAL control is the refusal at the ACS (``jit_provision_user``). The
    ``sso_tenant_scoped`` refresh marker only keeps the ``global_role`` *claim*
    stripped for claims-based endpoints; ``jwt_handler.get_current_user``
    authorizes from the live DB role and ignores it, so a role granted after a
    tenant-IdP session starts still applies through that path (follow-up:
    tokens_valid_after / role-version control).
    """

    async def test_refresh_keeps_global_role_claim_stripped_for_tenant_idp_sessions(
        self, env, keys, async_client, enterprise_tier
    ):
        """Claims only: the marker survives rotation; DB-based authz is untouched."""
        uid = _user(env, "jane.doe@example.com")
        resp = await _saml_login(env, async_client, "jane.doe@example.com", keys)
        assert resp.status_code == 200
        out = await resp.get_json()
        secret = _jwt_secret(env)
        assert jwt.decode(out["refresh_token"], secret, algorithms=["HS256"])[
            "sso_tenant_scoped"
        ]

        # A platform role granted AFTER the SSO login must not reappear as a
        # *claim* on refresh (get_current_user's DB lookup is out of scope here).
        env.db(env.db.portal_users.id == uid).update(global_role="admin")
        env.db.commit()
        token = out["refresh_token"]
        for _ in range(2):  # the marker must survive rotation
            # A fresh cookie-less (non-browser) client per call: refresh responses
            # set auth cookies, which would trigger the double-submit CSRF check.
            async with env.app.test_client() as api:
                refreshed = await api.post(
                    "/api/v1/portal-auth/refresh", json={"refresh_token": token}
                )
            assert refreshed.status_code == 200, await refreshed.get_data()
            body = await refreshed.get_json()
            access = jwt.decode(body["access_token"], secret, algorithms=["HS256"])
            token = body["refresh_token"]
            assert access["global_role"] is None
            assert access["sub"] == str(uid)
            assert jwt.decode(token, secret, algorithms=["HS256"])["sso_tenant_scoped"]

    async def test_ordinary_refresh_is_unchanged(self, env, async_client):
        """Control: sessions without the marker still resolve global_role as before."""
        uid = _user(env, "admin@example.com", global_role="admin")
        async with env.app.app_context():
            tokens = generate_tokens(
                {
                    "id": uid,
                    "email": "admin@example.com",
                    "tenant_id": env.tenant_a,
                    "tenant_role": "reader",
                    "global_role": None,
                }
            )
        secret = _jwt_secret(env)
        assert "sso_tenant_scoped" not in jwt.decode(
            tokens["refresh_token"], secret, algorithms=["HS256"]
        )
        refreshed = await async_client.post(
            "/api/v1/portal-auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        access = jwt.decode(
            (await refreshed.get_json())["access_token"], secret, algorithms=["HS256"]
        )
        assert access["global_role"] == "admin"

    async def test_login_refused_for_user_with_stored_global_role(
        self, env, keys, async_client, enterprise_tier
    ):
        _user(env, "root@example.com", global_role="admin")
        resp = await _saml_login(env, async_client, "root@example.com", keys)
        assert resp.status_code == 403
        assert (await resp.get_json())["error"] == (
            "saml_tenant_idp_global_role_forbidden"
        )
        assert "elder_access_token" not in "\n".join(resp.headers.get_all("Set-Cookie"))

    @pytest.mark.parametrize("role", ["admin", "support", "superadmin", "x"])
    async def test_any_truthy_stored_global_role_is_refused(
        self, env, keys, async_client, enterprise_tier, role
    ):
        """Not just ``admin``: every non-empty stored global_role is refused."""
        uid = _user(env, "root@example.com", global_role=role)
        resp = await _saml_login(env, async_client, "root@example.com", keys)
        assert resp.status_code == 403
        assert (await resp.get_json())["error"] == (
            "saml_tenant_idp_global_role_forbidden"
        )
        row = env.db(env.db.portal_users.id == uid).select().first()
        assert row.last_login_at is None

    async def test_global_idp_sessions_carry_no_marker(
        self, env, keys, async_client, enterprise_tier
    ):
        _use_global_idp(env, keys, opt_in=True)
        _user(env, "root@example.com", global_role="admin")
        resp = await _saml_login(env, async_client, "root@example.com", keys)
        assert resp.status_code == 200
        out = await resp.get_json()
        assert out["user"]["global_role"] == "admin"
        assert "sso_tenant_scoped" not in jwt.decode(
            out["refresh_token"], _jwt_secret(env), algorithms=["HS256"]
        )


@pytest.mark.asyncio
class TestF3GlobalIdpNeedsTenantOptIn:
    """The global IdP only serves tenants that explicitly opted in, never via JIT."""

    @pytest.mark.parametrize(
        "settings",
        [
            None,
            {},
            {"saml_allow_global_idp": False},
            {"saml_allow_global_idp": "true"},
            {"saml_allow_global_idp": 1},
            {"unrelated": True},
        ],
    )
    async def test_fallback_refused_without_a_strict_true_opt_in(
        self, env, keys, settings
    ):
        _use_global_idp(env, keys, opt_in=False, settings=settings)
        async with env.app.app_context():
            with pytest.raises(SAMLError) as exc:
                SAMLService.begin_login(env.tenant_a)
            acs = await SAMLService.process_saml_response(env.tenant_a, "AA", "T" * 43)
        assert (exc.value.code, exc.value.status_code) == (
            "saml_global_idp_not_enabled",
            403,
        )
        assert acs["error"] == "saml_global_idp_not_enabled"
        assert acs["status_code"] == 403

    async def test_http_login_fails_closed_with_specific_403(
        self, env, keys, async_client, enterprise_tier
    ):
        _use_global_idp(env, keys, opt_in=False)
        resp = await async_client.get(f"/api/v1/sso/saml/login/{env.tenant_a}")
        assert resp.status_code == 403
        assert (await resp.get_json())["error"] == "saml_global_idp_not_enabled"

    async def test_opt_in_serves_existing_users_but_never_provisions(
        self, env, keys, async_client, enterprise_tier
    ):
        _use_global_idp(env, keys, opt_in=True, jit=True)  # global row allows JIT
        uid = _user(env, "jane.doe@example.com")
        known = await _saml_login(env, async_client, "jane.doe@example.com", keys)
        assert known.status_code == 200
        assert (await known.get_json())["user"]["id"] == uid

        stranger = await _saml_login(env, async_client, "stranger@corp.example", keys)
        assert stranger.status_code == 403
        assert (await stranger.get_json())["error"] == "saml_jit_disabled"
        rows = env.db(env.db.portal_users.tenant_id == env.tenant_a).select()
        assert [r.email for r in rows] == ["jane.doe@example.com"]

    async def test_one_tenants_opt_in_does_not_open_another(self, env, keys):
        _use_global_idp(env, keys, opt_in=True)  # opts tenant A in only
        async with env.app.app_context():
            SAMLService._load_saml_idp(env.tenant_a)
            with pytest.raises(SAMLError) as exc:
                SAMLService._load_saml_idp(env.tenant_b)
        assert exc.value.code == "saml_global_idp_not_enabled"

    async def test_tenant_with_its_own_idp_needs_no_opt_in(self, env):
        async with env.app.app_context():
            cfg = SAMLService._load_saml_idp(env.tenant_a)
        assert cfg["tenant_id"] == env.tenant_a
        assert cfg["jit_provisioning_enabled"] is True

    async def test_global_fallback_forces_jit_off_in_the_loaded_config(self, env, keys):
        _use_global_idp(env, keys, opt_in=True, jit=True)
        async with env.app.app_context():
            cfg = SAMLService._load_saml_idp(env.tenant_a)
        assert cfg["tenant_id"] is None
        assert cfg["jit_provisioning_enabled"] is False

    async def test_opt_in_key_name_is_part_of_the_contract(self):
        """Operators set this literal key in tenant settings (docs/USAGE.md)."""
        assert GLOBAL_IDP_OPT_IN_KEY == "saml_allow_global_idp"

    async def test_jit_provision_user_rechecks_the_jit_switch_itself(self, env, keys):
        """Defence in depth: no user is created when the config forbids JIT."""
        cfg = {"tenant_id": env.tenant_a, "jit_provisioning_enabled": False}
        existing = _user(env, "known@example.com")
        async with env.app.app_context():
            created = SAMLService.jit_provision_user(
                env.tenant_a, "new@example.com", cfg, {"email": ["new@example.com"]}
            )
            known = SAMLService.jit_provision_user(
                env.tenant_a, "known@example.com", cfg, {}
            )
        assert created["error"] == "saml_jit_disabled"
        assert created["status_code"] == 403
        assert known["id"] == existing  # existing users are unaffected
        rows = env.db(env.db.portal_users.tenant_id == env.tenant_a).select()
        assert [r.email for r in rows] == ["known@example.com"]


@pytest.mark.asyncio
class TestF4InResponseToMustBeInsideTheSignedAssertion:
    """Response@InResponseTo is unsigned; the signed SubjectConfirmationData decides."""

    async def test_rebinding_a_captured_assertion_to_another_request_is_rejected(
        self, env, keys
    ):
        async with env.app.app_context():
            root = idp.build_signed(
                _spec(env, "_orig_req", scd_in_response_to=None), keys
            )
            root.set("InResponseTo", "_attacker_req")  # the Response is not signed
            with pytest.raises(SAMLError) as exc:
                _validate(env, keys, idp.encode(root), request_id="_attacker_req")
        assert exc.value.code == "saml_in_response_to_mismatch"

    async def test_absent_scd_in_response_to_rejected_even_when_consistent(
        self, env, keys
    ):
        async with env.app.app_context():
            b64 = idp.signed_response_b64(
                _spec(env, "_req1", scd_in_response_to=None), keys
            )
            with pytest.raises(SAMLError) as exc:
                _validate(env, keys, b64)
        assert exc.value.code == "saml_in_response_to_mismatch"


def _scs_response(env, keys, first: dict, second: dict | None = None) -> str:
    """Sign a response whose Subject has one or two SubjectConfirmations.

    ``first``/``second`` map SubjectConfirmationData attribute -> value (None
    removes it); the special key ``"Method"`` sets the SubjectConfirmation's
    Method. Unspecified attributes keep their valid defaults.
    """
    root = idp.build_response_xml(_spec(env, "_req1"))
    assertion = idp.assertion_of(root)
    first_sc = assertion.find(f"{{{NS}}}Subject/{{{NS}}}SubjectConfirmation")
    scs = [(first_sc, first)]
    if second is not None:
        second_sc = copy.deepcopy(first_sc)
        first_sc.addnext(second_sc)
        scs.append((second_sc, second))
    for sc, overrides in scs:
        scd = sc.find(f"{{{NS}}}SubjectConfirmationData")
        for name, value in overrides.items():
            target = sc if name == "Method" else scd
            if value is None:
                target.attrib.pop(name, None)
            else:
                target.set(name, value)
    idp.sign_element(assertion, keys)
    return idp.encode(root)


@pytest.mark.asyncio
class TestF6EveryCheckRunsOnTheSameSubjectConfirmation:
    """python3-saml may validate a different confirmation than the one we check."""

    async def test_expired_exact_recipient_plus_valid_substring_recipient_rejected(
        self, env, keys
    ):
        async with env.app.app_context():
            b64 = _scs_response(
                env,
                keys,
                {"Recipient": env.acs + "?x=1"},  # python3-saml accepts this one
                {"NotOnOrAfter": idp.fmt_time(time.time() - 3600)},
            )
            with pytest.raises(SAMLError) as exc:
                _validate(env, keys, b64)
        assert exc.value.code == "saml_assertion_expired"

    async def test_exact_recipient_without_in_response_to_plus_valid_other_rejected(
        self, env, keys
    ):
        async with env.app.app_context():
            b64 = _scs_response(
                env, keys, {"Recipient": env.acs + "?x=1"}, {"InResponseTo": None}
            )
            with pytest.raises(SAMLError) as exc:
                _validate(env, keys, b64)
        assert exc.value.code == "saml_in_response_to_mismatch"

    async def test_exact_recipient_not_yet_valid_plus_valid_substring_recipient_rejected(
        self, env, keys
    ):
        async with env.app.app_context():
            b64 = _scs_response(
                env,
                keys,
                {"Recipient": env.acs + "?x=1"},
                {"NotBefore": idp.fmt_time(time.time() + 3600)},
            )
            with pytest.raises(SAMLError) as exc:
                _validate(env, keys, b64)
        assert exc.value.code == "saml_assertion_not_yet_valid"

    async def test_only_bearer_confirmations_count(self, env, keys):
        async with env.app.app_context():
            b64 = _scs_response(
                env,
                keys,
                {"Method": HOLDER_OF_KEY},  # exact + valid, but not bearer
                {"Recipient": env.acs + "?x=1"},  # bearer, substring recipient
            )
            with pytest.raises(SAMLError) as exc:
                _validate(env, keys, b64)
        assert exc.value.code == "saml_recipient_mismatch"

    async def test_a_later_fully_valid_confirmation_is_accepted(self, env, keys):
        async with env.app.app_context():
            b64 = _scs_response(
                env, keys, {"Recipient": "https://other.example/acs"}, {}
            )
            assert _validate(env, keys, b64).name_id == "jane.doe@example.com"

    async def test_no_bearer_confirmation_at_all_rejected(self, env, keys):
        async with env.app.app_context():
            b64 = _scs_response(env, keys, {"Method": HOLDER_OF_KEY})
            with pytest.raises(SAMLError) as exc:
                _validate(env, keys, b64)
        assert exc.value.code == "saml_subject_confirmation_invalid"


def _audience_response(env, keys, restrictions: list[list[str]]) -> str:
    """Sign a response whose Conditions has one AudienceRestriction per entry."""
    root = idp.build_response_xml(_spec(env, "_req1"))
    assertion = idp.assertion_of(root)
    conditions = assertion.find(f"{{{NS}}}Conditions")
    for old in conditions.findall(f"{{{NS}}}AudienceRestriction"):
        conditions.remove(old)
    for audiences in restrictions:
        restriction = etree.SubElement(conditions, f"{{{NS}}}AudienceRestriction")
        for audience in audiences:
            etree.SubElement(restriction, f"{{{NS}}}Audience").text = audience
    idp.sign_element(assertion, keys)
    return idp.encode(root)


@pytest.mark.asyncio
class TestF7AudienceRestrictionsAreAnded:
    """SAML requires ALL AudienceRestrictions be satisfied, not their union."""

    @pytest.mark.parametrize("sp_first", [True, False])
    async def test_a_restriction_naming_another_sp_blocks_the_assertion(
        self, env, keys, sp_first
    ):
        other = "https://other-sp.example/"
        async with env.app.app_context():
            order = [[env.entity], [other]] if sp_first else [[other], [env.entity]]
            with pytest.raises(SAMLError) as exc:
                _validate(env, keys, _audience_response(env, keys, order))
        assert exc.value.code == "saml_audience_mismatch"

    async def test_every_restriction_naming_this_sp_is_accepted(self, env, keys):
        async with env.app.app_context():
            b64 = _audience_response(env, keys, [[env.entity], [env.entity]])
            assert _validate(env, keys, b64).name_id == "jane.doe@example.com"

    async def test_audiences_within_one_restriction_are_ored(self, env, keys):
        async with env.app.app_context():
            b64 = _audience_response(
                env, keys, [["https://other-sp.example/", env.entity]]
            )
            assert _validate(env, keys, b64).name_id == "jane.doe@example.com"


@pytest.mark.asyncio
class TestF5ReturnToIsASameOriginRelativePath:
    """Browsers strip tab/newline from Location, so they must never be allowed."""

    @pytest.mark.parametrize(
        "value",
        [
            "/\t/evil.com",
            "/\x0b/evil.com",
            "/\x0c/evil.com",
            "/\r/evil.com",
            "/\n/evil.com",
            "//evil.com",
            "/\\evil.com",
            "/\\/evil.com",
            "/%09/evil.com",
            "/%0b/evil.com",
            "/%2f/evil.com",
            "/%2F/evil.com",
            "/%5c/evil.com",
            "/%5Cevil.com",
            "/%252f/evil.com",
            "/%25",
            "/a%zz",
            "/a%",
            "/a%2",
            "/a b",
            "/a'b",
            '/a"b',
            "/a<b>",
            "/a`b",
            "/a{b}",
            "/a|b",
            "/a^b",
            "/\u00e9",
            "/\u202e/x",
            "/ok%0a",
            "/ok\n",
            "/ok\r\n",
            "/ok\x00",
            "/ok\x7f",
            "https://evil.com",
            "evil.com",
            "",
            "/" + "a" * 2048,
        ],
    )
    async def test_unsafe_values_rejected(self, env, value):
        assert _RETURN_TO_RE.fullmatch(value) is None
        async with env.app.app_context():
            with pytest.raises(SAMLError) as exc:
                SAMLService.begin_login(env.tenant_a, return_to=value)
        assert exc.value.code == "saml_return_to_invalid"

    @pytest.mark.parametrize(
        "value",
        [
            "/",
            "/dashboard",
            "/ok/path?x=1&y=2#frag",
            "/a%20b",
            "/a-b_c.d~e",
            "/a%E2%82%AC",
            "/" + "a" * 2047,
        ],
    )
    async def test_ordinary_relative_paths_accepted(self, env, value):
        async with env.app.app_context():
            started = SAMLService.begin_login(env.tenant_a, return_to=value)
        assert started["state_token"]

    async def test_trailing_state_token_newline_is_rejected_before_redis(self, env):
        """The validator itself must reject it (``$`` would let it reach Redis)."""
        async with env.app.app_context():
            with patch.object(
                SAMLService,
                "_state_store",
                side_effect=AssertionError("Redis was consulted"),
            ):
                with pytest.raises(SAMLError) as exc:
                    SAMLService._consume_login_state(env.tenant_a, "T" * 43 + "\n")
        assert exc.value.code == "saml_login_state_missing"


@pytest.mark.asyncio
class TestF9SingleLogoutIsNotAStub:
    """SLO answers 501 and is no longer advertised anywhere."""

    @pytest.mark.parametrize("method", ["get", "post"])
    async def test_slo_endpoint_returns_501(self, env, async_client, method):
        resp = await getattr(async_client, method)(
            f"/api/v1/sso/saml/slo/{env.tenant_a}"
        )
        assert resp.status_code == 501
        body = await resp.get_json()
        assert body["error"] == "saml_slo_not_implemented"
        assert "Logged out successfully" not in str(body)

    async def test_slo_is_not_advertised(self, env, async_client):
        async with env.app.app_context():
            settings, _entity, _acs = SAMLService._build_saml_settings(
                env.tenant_a,
                {"entity_id": env.idp_entity, "sso_url": "https://idp.example.com/sso"},
            )
            metadata = SAMLService.get_sp_metadata(SP_BASE, env.tenant_a)
        assert "singleLogoutService" not in settings["sp"]
        assert "singleLogoutService" not in settings["idp"]
        assert "SingleLogoutService" not in metadata
        http = await async_client.get(f"/api/v1/sso/saml/metadata/{env.tenant_a}")
        assert "SingleLogoutService" not in (await http.get_data()).decode()


class _StubClient:
    """A minimal Redis client double that records ``close()``."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def blackhole():
    """A TCP listener that completes handshakes but never answers (hung Redis)."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(64)
    yield listener
    listener.close()


@pytest.mark.asyncio
class TestN2RedisOutageDoesNotStarveWorkers:
    """A hung Redis costs one timeout window, not one per request."""

    async def test_blackholed_redis_burst_is_one_timeout_window_then_recovers(
        self, env, blackhole, monkeypatch
    ):
        timeout, burst = 0.5, 12
        monkeypatch.setattr(saml_service_module, "_REDIS_TIMEOUT_SECONDS", timeout)
        monkeypatch.setattr(saml_service_module, "_NEGATIVE_CACHE_SECONDS", 1.0)
        attempts = []
        real_from_url = saml_service_module.redis_from_url

        def counting_from_url(*args, **kwargs):
            attempts.append(1)
            return real_from_url(*args, **kwargs)

        monkeypatch.setattr(saml_service_module, "redis_from_url", counting_from_url)

        async with env.app.app_context():
            env.app.extensions.pop("saml_redis", None)
            env.app.config["REDIS_URL"] = (
                f"redis://127.0.0.1:{blackhole.getsockname()[1]}/0"
            )
            started = time.monotonic()
            results = await asyncio.gather(
                *[run_in_threadpool(SAMLService._state_store) for _ in range(burst)],
                return_exceptions=True,
            )
            elapsed = time.monotonic() - started

            assert all(isinstance(r, SAMLError) for r in results), results
            assert {r.status_code for r in results} == {503}
            assert len(attempts) == 1  # single-flight: one probe, not one per request
            # ~one timeout window; serial behaviour would be burst * timeout (6s)
            assert elapsed < burst * timeout / 2, elapsed

            # Inside the negative-cache window: fail fast, no new connect attempt.
            t0 = time.monotonic()
            with pytest.raises(SAMLError):
                SAMLService._state_store()
            assert time.monotonic() - t0 < 0.1
            assert len(attempts) == 1

            # Redis comes back; once the window elapses the next call reconnects.
            env.app.config["REDIS_URL"] = os.environ["REDIS_URL"]
            await asyncio.sleep(1.1)
            assert SAMLService._state_store().ping()
            assert "saml_redis" in env.app.extensions

    async def test_failed_client_is_closed_but_a_healthy_replacement_is_kept(self, env):
        async with env.app.app_context():
            old, healthy = _StubClient(), _StubClient()
            env.app.extensions["saml_redis"] = old
            error = SAMLService._store_unavailable(old)
            assert error.status_code == 503
            assert old.closed
            assert "saml_redis" not in env.app.extensions

            # Another thread already rebuilt a healthy client: a late failure
            # report about the OLD client must not evict it.
            env.app.extensions["saml_redis"] = healthy
            SAMLService._store_unavailable(_StubClient())
            assert env.app.extensions["saml_redis"] is healthy
            assert not healthy.closed

    async def test_operation_failure_starts_the_negative_cache(self, env, monkeypatch):
        monkeypatch.setattr(saml_service_module, "_NEGATIVE_CACHE_SECONDS", 30.0)
        dead = redis_dead_client()
        async with env.app.app_context():
            env.app.extensions["saml_redis"] = dead
            with pytest.raises(SAMLError):
                SAMLService.begin_login(env.tenant_a)
            assert env.app.extensions["saml_redis_down_until"] > time.monotonic()
            started = time.monotonic()
            with pytest.raises(SAMLError) as exc:  # fails fast, no reconnect probe
                SAMLService.begin_login(env.tenant_a)
            assert time.monotonic() - started < 0.1
            assert exc.value.code == "saml_state_store_unavailable"


def redis_dead_client():
    """A real Redis client pointed at a closed port (connection refused)."""
    return redis_lib.Redis(port=1, socket_connect_timeout=0.2)
