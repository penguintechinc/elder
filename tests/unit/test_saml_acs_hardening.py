# ruff: noqa: F811  (pytest fixtures imported from the sibling module are re-declared as test params)
"""Regression tests for the adversarial-review findings on the SAML ACS (F2-F9).

Same approach as ``test_saml_acs_processing.py``: real xmlsec-signed assertions
from the in-process IdP, real Redis and Postgres. Each class pins one finding
so it cannot silently regress.
"""

import copy
import time

import jwt
import pytest
from lxml import etree

from apps.api.api.v1.portal_auth import generate_tokens
from apps.api.services.sso.saml_service import (
    _RETURN_TO_RE,
    SAMLError,
    SAMLService,
)
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
    """A tenant-scoped IdP never yields a session that can carry a global_role."""

    async def test_refresh_keeps_global_role_stripped_for_tenant_idp_sessions(
        self, env, keys, async_client, enterprise_tier
    ):
        uid = _user(env, "jane.doe@example.com")
        resp = await _saml_login(env, async_client, "jane.doe@example.com", keys)
        assert resp.status_code == 200
        out = await resp.get_json()
        secret = _jwt_secret(env)
        assert jwt.decode(out["refresh_token"], secret, algorithms=["HS256"])[
            "sso_tenant_scoped"
        ]

        # A platform role granted AFTER the SSO login must not leak into refresh.
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
        assert not cfg.get("global_fallback")
        assert cfg["jit_provisioning_enabled"] is True


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
            "/a b",
            "/" + "a" * 2047,
        ],
    )
    async def test_ordinary_relative_paths_accepted(self, env, value):
        async with env.app.app_context():
            started = SAMLService.begin_login(env.tenant_a, return_to=value)
        assert started["state_token"]

    async def test_trailing_state_token_newline_is_not_a_valid_token(self, env):
        async with env.app.app_context():
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
