"""SAML ACS assertion processing: real signed assertions, real Redis + Postgres.

Nothing in the validation chain is mocked. ``tests/unit/saml_test_idp.py``
generates a real RSA key/X.509 certificate and signs real SAML Responses with
xmlsec; ``SAMLService`` verifies them against the certificate stored in a real
``idp_configurations`` row, keeps AuthnRequest/replay state in the real test
Redis, and resolves users in the real test database. The only patch is the
deployment license tier (Enterprise gate) and, for one test, the admin-seat
limit helper, which has its own test suite.
"""

import base64
import os
import time
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import jwt
import pytest
import pytest_asyncio
import redis as redis_lib
from lxml import etree

from apps.api.auth.portal_cookies import ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME
from apps.api.services.sso.saml_service import SAMLError, SAMLService
from tests.unit import saml_test_idp as idp

SP_BASE = "https://elder.example.com"
STATE_COOKIE = "elder_saml_state"
_ENTERPRISE = (
    "apps.api.common.licensing.tier_gate.resolve_limits",
    ("enterprise", None),
)


@pytest.fixture(scope="module")
def keys() -> idp.IdpKeys:
    """The legitimate IdP's signing identity."""
    return idp.generate_keys("legit-idp")


@pytest.fixture(scope="module")
def attacker_keys() -> idp.IdpKeys:
    """A different signing identity an attacker controls."""
    return idp.generate_keys("attacker-idp")


@pytest.fixture
def enterprise_tier():
    """Pretend the deployment holds an Enterprise license (SAML is Enterprise)."""
    target, value = _ENTERPRISE
    with patch(target, return_value=value):
        yield


@pytest_asyncio.fixture
async def env(app, test_database_url, keys):
    """Seed two tenants, a tenant-scoped SAML IdP, and wire config + Redis."""
    if not test_database_url:
        pytest.skip("DATABASE_URL not set")

    db = app.db
    suffix = uuid.uuid4().hex[:8]
    now = datetime.now(UTC)
    saved_config = {
        k: app.config.get(k) for k in ("SAML_SP_BASE_URL", "SITE_URL", "DEBUG")
    }
    saved_redis = app.extensions.get("module_redis")
    app.config["SAML_SP_BASE_URL"] = SP_BASE
    app.config["DEBUG"] = False
    client = redis_lib.from_url(os.environ["REDIS_URL"])
    app.extensions["module_redis"] = client

    tenant_a = db.tenants.insert(
        name=f"SAML A {suffix}",
        slug=f"saml-a-{suffix}",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    tenant_b = db.tenants.insert(
        name=f"SAML B {suffix}",
        slug=f"saml-b-{suffix}",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    idp_id = db.idp_configurations.insert(
        tenant_id=tenant_a,
        idp_type="saml",
        name="Test IdP",
        entity_id="https://idp.example.com/metadata",
        sso_url="https://idp.example.com/sso",
        slo_url="https://idp.example.com/slo",
        certificate=keys.cert_b64,
        attribute_mappings={},
        jit_provisioning_enabled=True,
        default_role="reader",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.commit()

    entity, acs, _slo = SAMLService.sp_urls(SP_BASE, tenant_a)
    yield SimpleNamespace(
        app=app,
        db=db,
        client=client,
        tenant_a=tenant_a,
        tenant_b=tenant_b,
        idp_id=idp_id,
        entity=entity,
        acs=acs,
        idp_entity="https://idp.example.com/metadata",
    )

    for key in client.scan_iter("elder:saml:*"):
        client.delete(key)
    db(db.portal_users.tenant_id.belongs([tenant_a, tenant_b])).delete()
    db(db.idp_configurations.tenant_id.belongs([tenant_a, tenant_b])).delete()
    db(db.idp_configurations.tenant_id == None).delete()  # noqa: E711
    db(db.tenants.id.belongs([tenant_a, tenant_b])).delete()
    db.commit()
    app.extensions["module_redis"] = saved_redis
    for k, v in saved_config.items():
        if v is None:
            app.config.pop(k, None)
        else:
            app.config[k] = v


def _idp_config(env, keys, **overrides) -> dict:
    """The dict ``SAMLService`` would load for the seeded IdP."""
    cfg = {
        "id": env.idp_id,
        "tenant_id": env.tenant_a,
        "idp_type": "saml",
        "entity_id": env.idp_entity,
        "sso_url": "https://idp.example.com/sso",
        "certificate": keys.cert_b64,
        "attribute_mappings": {},
        "jit_provisioning_enabled": True,
        "default_role": "reader",
    }
    cfg.update(overrides)
    return cfg


def _spec(env, request_id="_req1", **kw) -> idp.ResponseSpec:
    """Build a ResponseSpec addressed to the seeded tenant's SP, overridable per test."""
    return idp.ResponseSpec(
        sp_entity_id=env.entity, acs_url=env.acs, in_response_to=request_id, **kw
    )


def _validate(env, keys, b64, request_id="_req1"):
    """Run the real validation chain against the seeded IdP config."""
    return SAMLService._validate_response(
        env.tenant_a, _idp_config(env, keys), b64, request_id
    )


def _login(env) -> tuple[str, str]:
    """Run begin_login for tenant A; return ``(state_token, request_id)``."""
    started = SAMLService.begin_login(env.tenant_a)
    return started["state_token"], idp.request_id_from_redirect(started["sso_url"])


def _user(env, email, *, tenant=None, active=True, role="reader", global_role=None):
    """Insert a real portal_users row and return its id."""
    now = datetime.now(UTC)
    uid = env.db.portal_users.insert(
        tenant_id=tenant or env.tenant_a,
        email=email,
        password_hash=None,
        is_active=active,
        email_verified=True,
        tenant_role=role,
        global_role=global_role,
        created_at=now,
        updated_at=now,
    )
    env.db.commit()
    return uid


@pytest.mark.asyncio
class TestAssertionValidation:
    """Every accept/reject decision of the assertion validation chain."""

    async def test_valid_signed_assertion_accepted(self, env, keys):
        async with env.app.app_context():
            b64 = idp.signed_response_b64(_spec(env), keys)
            out = _validate(env, keys, b64)
        assert out.name_id == "jane.doe@example.com"
        assert out.attributes["email"] == ["jane.doe@example.com"]
        assert out.session_index == "_sess1"
        assert out.in_response_to == "_req1"
        assert out.assertion_id.startswith("_a")

    async def test_mime_wrapped_base64_accepted(self, env, keys):
        async with env.app.app_context():
            b64 = idp.signed_response_b64(_spec(env), keys)
            wrapped = "\r\n".join(b64[i : i + 76] for i in range(0, len(b64), 76))
            assert _validate(env, keys, wrapped).name_id == "jane.doe@example.com"

    @pytest.mark.parametrize(
        ("overrides", "code"),
        [
            pytest.param({"sign": "none"}, "saml_unsigned", id="unsigned"),
            pytest.param(
                {"sign": "response"}, "saml_unsigned", id="only-response-signed"
            ),
            pytest.param(
                {"sig_alg": "sha1"}, "saml_signature_invalid", id="deprecated-sha1"
            ),
            pytest.param(
                {
                    "not_on_or_after": time.time() - 3600,
                    "scd_not_on_or_after": time.time() + 300,
                },
                "saml_assertion_expired",
                id="conditions-expired",
            ),
            pytest.param(
                {
                    "not_on_or_after": time.time() - 200,
                    "scd_not_on_or_after": time.time() + 300,
                },
                "saml_assertion_expired",
                id="expired-beyond-our-120s-skew",
            ),
            pytest.param(
                {"scd_not_on_or_after": time.time() - 3600},
                "saml_subject_confirmation_invalid",
                id="subject-confirmation-expired",
            ),
            pytest.param(
                {"not_before": time.time() + 3600},
                "saml_assertion_not_yet_valid",
                id="not-yet-valid",
            ),
            pytest.param(
                {"not_before": time.time() + 200},
                "saml_assertion_not_yet_valid",
                id="early-beyond-our-120s-skew",
            ),
            pytest.param(
                {"audience": "https://elder.example.com/saml/metadata/99"},
                "saml_audience_mismatch",
                id="wrong-audience",
            ),
            pytest.param(
                {"audience": "__sp__2"},
                "saml_audience_mismatch",
                id="audience-numeric-prefix-sibling",
            ),
            pytest.param(
                {"audience": None},
                "saml_audience_mismatch",
                id="no-audience-restriction",
            ),
            pytest.param(
                {"destination": "https://evil.example.com/api/v1/sso/saml/acs/1"},
                "saml_destination_mismatch",
                id="wrong-destination-host",
            ),
            pytest.param(
                {"destination": "__acs__2"},
                "saml_destination_mismatch",
                id="destination-numeric-prefix-sibling",
            ),
            pytest.param(
                {"recipient": "__acs__2"},
                "saml_recipient_mismatch",
                id="recipient-numeric-prefix-sibling",
            ),
            pytest.param(
                {"recipient": "https://evil.example.com/acs"},
                "saml_subject_confirmation_invalid",
                id="recipient-other-host",
            ),
            pytest.param(
                {"recipient": None}, "saml_recipient_mismatch", id="recipient-absent"
            ),
            pytest.param(
                {"scd_in_response_to": "_other"},
                "saml_subject_confirmation_invalid",
                id="scd-in-response-to-mismatch",
            ),
            pytest.param(
                {"idp_entity_id": "https://evil-idp.example.com"},
                "saml_issuer_mismatch",
                id="wrong-issuer",
            ),
            pytest.param(
                {"status": "urn:oasis:names:tc:SAML:2.0:status:Responder"},
                "saml_idp_error_status",
                id="idp-error-status",
            ),
            pytest.param(
                {"include_conditions_not_on_or_after": False},
                "saml_assertion_unbounded",
                id="conditions-unbounded",
            ),
            pytest.param(
                {
                    "not_on_or_after": time.time() + 3 * 86400,
                    "scd_not_on_or_after": time.time() + 3 * 86400,
                },
                "saml_assertion_lifetime_invalid",
                id="lifetime-too-long",
            ),
            pytest.param(
                {"include_authn_statement": False},
                "saml_assertion_structure_invalid",
                id="no-authn-statement",
            ),
            pytest.param(
                {"include_scd_not_on_or_after": False},
                "saml_assertion_unbounded",
                id="subject-confirmation-unbounded",
            ),
            pytest.param(
                {"include_name_id": False},
                "saml_assertion_structure_invalid",
                id="no-name-id",
            ),
            pytest.param(
                {"name_id": "a" * 1100 + "@example.com"},
                "saml_assertion_structure_invalid",
                id="oversized-name-id",
            ),
        ],
    )
    async def test_rejections(self, env, keys, overrides, code):
        async with env.app.app_context():
            b64 = idp.signed_response_b64(_spec(env, **overrides), keys)
            with pytest.raises(SAMLError) as exc:
                _validate(env, keys, b64)
        assert exc.value.code == code
        assert exc.value.status_code == 401

    async def test_skew_within_120s_is_tolerated(self, env, keys):
        async with env.app.app_context():
            spec = _spec(
                env,
                not_before=time.time() + 60,
                not_on_or_after=time.time() - 60,
                scd_not_on_or_after=time.time() + 300,
            )
            assert _validate(env, keys, idp.signed_response_b64(spec, keys)).name_id

    async def test_wrong_signer_with_embedded_attacker_cert_rejected(
        self, env, keys, attacker_keys
    ):
        """A self-consistent assertion signed by the attacker (own KeyInfo cert)."""
        async with env.app.app_context():
            b64 = idp.signed_response_b64(_spec(env), attacker_keys)
            with pytest.raises(SAMLError) as exc:
                _validate(env, keys, b64)
        assert exc.value.code == "saml_signature_invalid"

    async def test_tampered_after_signing_rejected(self, env, keys):
        async with env.app.app_context():
            root = idp.build_signed(_spec(env), keys)
            idp.assertion_of(root).find(
                f".//{{{idp.NS_SAML}}}NameID"
            ).text = "admin@example.com"
            with pytest.raises(SAMLError) as exc:
                _validate(env, keys, idp.encode(root))
        assert exc.value.code == "saml_signature_invalid"

    async def test_wrong_in_response_to_rejected(self, env, keys):
        async with env.app.app_context():
            b64 = idp.signed_response_b64(_spec(env, request_id="_theirs"), keys)
            with pytest.raises(SAMLError) as exc:
                _validate(env, keys, b64, request_id="_ours")
        assert exc.value.code == "saml_in_response_to_mismatch"

    async def test_unsolicited_response_without_in_response_to_rejected(
        self, env, keys
    ):
        async with env.app.app_context():
            spec = _spec(env, request_id=None, scd_in_response_to=None)
            with pytest.raises(SAMLError) as exc:
                _validate(env, keys, idp.signed_response_b64(spec, keys), "_ours")
        assert exc.value.code == "saml_in_response_to_mismatch"

    async def test_signature_wrapping_extra_assertion_rejected(self, env, keys):
        """Valid signed assertion hidden in Extensions + forged unsigned one."""
        async with env.app.app_context():
            root = idp.build_signed(_spec(env), keys)
            signed = idp.assertion_of(root)
            forged = etree.fromstring(etree.tostring(signed))
            forged.remove(forged.find("{http://www.w3.org/2000/09/xmldsig#}Signature"))
            forged.set("ID", "_forged")
            forged.find(f".//{{{idp.NS_SAML}}}NameID").text = "admin@example.com"
            extensions = etree.Element(f"{{{idp.NS_SAMLP}}}Extensions")
            root.remove(signed)
            extensions.append(signed)
            root.find(f"{{{idp.NS_SAMLP}}}Status").addprevious(extensions)
            root.append(forged)
            with pytest.raises(SAMLError) as exc:
                _validate(env, keys, idp.encode(root))
        assert exc.value.code in {
            "saml_assertion_structure_invalid",
            "saml_signature_invalid",
        }

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param(
                b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
                b'<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
                b'ID="_x" Version="2.0">&x;</samlp:Response>',
                id="xxe-external-entity",
            ),
            pytest.param(
                b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa">'
                b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
                b'<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]>'
                b'<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
                b'ID="_x" Version="2.0">&c;</samlp:Response>',
                id="billion-laughs",
            ),
            pytest.param(b"not xml at all", id="not-xml"),
            pytest.param(b"", id="empty-xml"),
        ],
    )
    async def test_hostile_xml_rejected_as_malformed(self, env, keys, payload):
        async with env.app.app_context():
            with pytest.raises(SAMLError) as exc:
                _validate(env, keys, base64.b64encode(payload).decode() or "AA")
        assert exc.value.code == "saml_response_malformed"
        assert exc.value.status_code == 400

    @pytest.mark.parametrize("value", ["", "!!!not-base64!!!", None, 123])
    async def test_bad_base64_input_rejected(self, env, keys, value):
        async with env.app.app_context():
            with pytest.raises(SAMLError) as exc:
                _validate(env, keys, value)
        assert exc.value.code == "saml_response_malformed"

    async def test_oversized_response_rejected(self, env, keys):
        async with env.app.app_context():
            with pytest.raises(SAMLError) as exc:
                _validate(env, keys, "A" * 1_048_577)
        assert exc.value.code == "saml_response_too_large"
        assert exc.value.status_code == 413

    async def test_garbled_signature_value_rejected(self, env, keys):
        async with env.app.app_context():
            root = idp.build_signed(_spec(env), keys)
            sig_value = root.find(
                f".//{{http://www.w3.org/2000/09/xmldsig#}}SignatureValue"
            )
            sig_value.text = base64.b64encode(b"x" * 256).decode()
            with pytest.raises(SAMLError) as exc:
                _validate(env, keys, idp.encode(root))
        assert exc.value.code == "saml_signature_invalid"

    async def test_encrypted_assertion_unsupported_is_rejected(self, env, keys):
        """No SP decryption key is configured, so EncryptedAssertion must fail closed."""
        async with env.app.app_context():
            root = idp.build_signed(_spec(env), keys)
            root.append(etree.Element(f"{{{idp.NS_SAML}}}EncryptedAssertion"))
            with pytest.raises(SAMLError) as exc:
                _validate(env, keys, idp.encode(root))
        assert exc.value.code == "saml_response_malformed"

    async def test_unexpected_library_failure_fails_closed(self, env, keys):
        """A crash inside signature verification must reject, never accept."""
        async with env.app.app_context():
            b64 = idp.signed_response_b64(_spec(env), keys)
            with patch(
                "apps.api.services.sso.saml_service.OneLogin_Saml2_Response.is_valid",
                side_effect=RuntimeError("xmlsec exploded"),
            ):
                with pytest.raises(SAMLError) as exc:
                    _validate(env, keys, b64)
        assert exc.value.code == "saml_validation_failed"
        assert exc.value.status_code == 401

    async def test_unrelated_xml_document_rejected(self, env, keys):
        async with env.app.app_context():
            b64 = base64.b64encode(b"<root><child/></root>").decode()
            with pytest.raises(SAMLError) as exc:
                _validate(env, keys, b64)
        assert exc.value.code in {"saml_response_malformed", "saml_validation_failed"}

    async def test_forged_host_header_cannot_retarget_response(self, env, keys):
        """Destination/Audience for an attacker host never validate: config wins."""
        async with env.app.app_context():
            evil_entity, evil_acs, _ = SAMLService.sp_urls(
                "https://evil.example.com", env.tenant_a
            )
            spec = idp.ResponseSpec(
                sp_entity_id=evil_entity, acs_url=evil_acs, in_response_to="_req1"
            )
            with pytest.raises(SAMLError) as exc:
                _validate(env, keys, idp.signed_response_b64(spec, keys))
        assert exc.value.code in {"saml_audience_mismatch", "saml_destination_mismatch"}


@pytest.mark.asyncio
class TestStateAndReplay:
    """Single-use AuthnRequest state and the assertion replay cache (real Redis)."""

    async def test_begin_login_builds_real_authn_request(self, env):
        async with env.app.app_context():
            started = SAMLService.begin_login(env.tenant_a, return_to="/dashboard")
        assert started["sso_url"].startswith("https://idp.example.com/sso?SAMLRequest=")
        assert idp.request_id_from_redirect(started["sso_url"]).startswith("ONELOGIN_")
        assert started["expires_in"] == 600
        keys_in_redis = list(env.client.scan_iter("elder:saml:authnreq:*"))
        assert len(keys_in_redis) == 1
        assert started["state_token"].encode() not in keys_in_redis[0]  # token hashed
        assert 0 < env.client.ttl(keys_in_redis[0]) <= 600

    async def test_login_state_is_single_use(self, env):
        async with env.app.app_context():
            token, request_id = _login(env)
            first = SAMLService._consume_login_state(env.tenant_a, token)
            assert first["request_id"] == request_id
            with pytest.raises(SAMLError) as exc:
                SAMLService._consume_login_state(env.tenant_a, token)
        assert exc.value.code == "saml_login_state_missing"

    async def test_login_state_is_tenant_scoped(self, env):
        async with env.app.app_context():
            token, _ = _login(env)
            with pytest.raises(SAMLError) as exc:
                SAMLService._consume_login_state(env.tenant_b, token)
        assert exc.value.code == "saml_login_state_missing"

    @pytest.mark.parametrize(
        "token", [None, "", "short", "a b" * 20, "x" * 200, "../" * 20]
    )
    async def test_malformed_state_tokens_rejected(self, env, token):
        async with env.app.app_context():
            with pytest.raises(SAMLError) as exc:
                SAMLService._consume_login_state(env.tenant_a, token)
        assert exc.value.status_code == 400

    async def test_corrupt_stored_state_rejected(self, env):
        async with env.app.app_context():
            token = "T" * 43
            env.client.set(
                SAMLService._state_key(env.tenant_a, token), "not-json", ex=60
            )
            with pytest.raises(SAMLError) as exc:
                SAMLService._consume_login_state(env.tenant_a, token)
            token2 = "U" * 43
            env.client.set(
                SAMLService._state_key(env.tenant_a, token2), '{"request_id": 5}', ex=60
            )
            with pytest.raises(SAMLError) as exc2:
                SAMLService._consume_login_state(env.tenant_a, token2)
        assert exc.value.code == exc2.value.code == "saml_login_state_missing"

    async def test_replayed_assertion_id_rejected_with_ttl(self, env, keys):
        async with env.app.app_context():
            b64 = idp.signed_response_b64(_spec(env), keys)
            assertion = _validate(env, keys, b64)
            SAMLService._record_assertion(env.tenant_a, env.idp_entity, assertion)
            with pytest.raises(SAMLError) as exc:
                SAMLService._record_assertion(env.tenant_a, env.idp_entity, assertion)
            # Different tenant / different IdP are different replay domains.
            SAMLService._record_assertion(env.tenant_b, env.idp_entity, assertion)
            SAMLService._record_assertion(env.tenant_a, "https://other-idp", assertion)
        assert exc.value.code == "saml_assertion_replayed"
        keys_in_redis = list(
            env.client.scan_iter(f"elder:saml:assertion:{env.tenant_a}:*")
        )
        ttl = env.client.ttl(keys_in_redis[0])
        # now+300s validity + 120s skew + 60s floor => ~480s, never below validity
        assert 300 < ttl <= 480

    async def test_full_flow_blocks_replay_even_if_state_were_reusable(self, env, keys):
        """Defence in depth: a second login-state for the same request id still hits the replay cache."""
        async with env.app.app_context():
            token, request_id = _login(env)
            b64 = idp.signed_response_b64(_spec(env, request_id), keys)
            first = await SAMLService.process_saml_response(env.tenant_a, b64, token)
            assert "error" not in first
            second_token = "R" * 43
            env.client.set(
                SAMLService._state_key(env.tenant_a, second_token),
                f'{{"request_id": "{request_id}", "return_to": null}}',
                ex=60,
            )
            second = await SAMLService.process_saml_response(
                env.tenant_a, b64, second_token
            )
        assert second["error"] == "saml_assertion_replayed"
        assert second["status_code"] == 401

    async def test_state_store_outage_fails_closed(self, env, keys):
        dead = redis_lib.Redis(port=1, socket_connect_timeout=0.2)
        async with env.app.app_context():
            env.app.extensions["module_redis"] = dead
            with pytest.raises(SAMLError) as login_exc:
                SAMLService.begin_login(env.tenant_a)
            with pytest.raises(SAMLError) as consume_exc:
                SAMLService._consume_login_state(env.tenant_a, "T" * 43)
            assertion = idp_assertion = None
            env.app.extensions["module_redis"] = env.client
            assertion = _validate(env, keys, idp.signed_response_b64(_spec(env), keys))
            env.app.extensions["module_redis"] = dead
            with pytest.raises(SAMLError) as record_exc:
                SAMLService._record_assertion(env.tenant_a, env.idp_entity, assertion)
            env.app.extensions["module_redis"] = None
            with pytest.raises(SAMLError) as none_exc:
                SAMLService.begin_login(env.tenant_a)
        for exc in (login_exc, consume_exc, record_exc, none_exc):
            assert exc.value.code == "saml_state_store_unavailable"
            assert exc.value.status_code == 503

    @pytest.mark.parametrize(
        "return_to",
        [
            "//evil.com",
            "https://evil.com",
            "/ok\r\nSet-Cookie: x=1",
            "\\evil",
            "relative",
        ],
    )
    async def test_begin_login_rejects_open_redirect_return_to(self, env, return_to):
        async with env.app.app_context():
            with pytest.raises(SAMLError) as exc:
                SAMLService.begin_login(env.tenant_a, return_to=return_to)
        assert exc.value.code == "saml_return_to_invalid"


@pytest.mark.asyncio
class TestConfiguration:
    """SP identity from config, and IdP-config problems as specific errors."""

    @pytest.mark.parametrize(
        "base",
        [
            "",
            "ftp://elder.example.com",
            "https://",
            "https://u:p@elder.example.com",
            "https://elder.example.com?x=1",
        ],
    )
    async def test_invalid_sp_base_url_rejected(self, env, base):
        env.app.config["SAML_SP_BASE_URL"] = base
        env.app.config["SITE_URL"] = ""
        async with env.app.app_context():
            with pytest.raises(SAMLError) as exc:
                SAMLService.sp_base_url()
        assert exc.value.code == "saml_sp_base_url_invalid"
        assert exc.value.status_code == 503

    async def test_http_base_url_only_allowed_in_debug_or_testing(self, env):
        env.app.config["SAML_SP_BASE_URL"] = "http://localhost:5000/"
        async with env.app.app_context():
            assert SAMLService.sp_base_url() == "http://localhost:5000"  # TESTING
            env.app.config["TESTING"] = False
            try:
                with pytest.raises(SAMLError):
                    SAMLService.sp_base_url()
                env.app.config["DEBUG"] = True
                assert SAMLService.sp_base_url() == "http://localhost:5000"
            finally:
                env.app.config["TESTING"] = True

    async def test_site_url_is_the_fallback(self, env):
        env.app.config["SAML_SP_BASE_URL"] = ""
        env.app.config["SITE_URL"] = "https://site.example.com/"
        async with env.app.app_context():
            assert SAMLService.sp_base_url() == "https://site.example.com"

    async def test_idp_problems_are_specific_errors(self, env, keys):
        db = env.db
        async with env.app.app_context():

            def load():
                return SAMLService._load_saml_idp(env.tenant_a)

            def set_idp(**fields):
                db(db.idp_configurations.id == env.idp_id).update(**fields)
                db.commit()

            with pytest.raises(SAMLError) as exc:
                SAMLService._load_saml_idp(99_999_999)
            assert (exc.value.code, exc.value.status_code) == (
                "saml_tenant_not_found",
                404,
            )

            set_idp(is_active=False)
            with pytest.raises(SAMLError) as exc:
                load()
            assert (exc.value.code, exc.value.status_code) == (
                "saml_idp_not_configured",
                404,
            )

            set_idp(is_active=True, idp_type="oidc")
            with pytest.raises(SAMLError) as exc:
                load()
            assert (exc.value.code, exc.value.status_code) == (
                "saml_idp_misconfigured",
                409,
            )

            set_idp(idp_type="saml", entity_id=None)
            with pytest.raises(SAMLError) as exc:
                load()
            assert exc.value.code == "saml_idp_misconfigured"

            set_idp(entity_id=env.idp_entity, certificate=None)
            with pytest.raises(SAMLError) as exc:
                load()
            assert "certificate not configured" in exc.value.message

            set_idp(certificate="this is not a certificate")
            with pytest.raises(SAMLError) as exc:
                load()
            assert "not a valid X.509" in exc.value.message

            set_idp(certificate=keys.cert_b64)
            assert load()["entity_id"] == env.idp_entity

            set_idp(sso_url=None)
            with pytest.raises(SAMLError) as exc:
                SAMLService.begin_login(env.tenant_a)
            assert exc.value.code == "saml_idp_misconfigured"

            set_idp(sso_url="not a url")
            with pytest.raises(SAMLError) as exc:
                SAMLService.begin_login(env.tenant_a)
            assert exc.value.status_code == 409

    async def test_sp_settings_failure_surfaces_as_503(self, env, keys):
        env.app.config["SAML_SP_BASE_URL"] = "https://elder example.com"
        async with env.app.app_context():
            b64 = idp.signed_response_b64(_spec(env), keys)
            with pytest.raises(SAMLError) as exc:
                _validate(env, keys, b64)
        assert exc.value.code == "saml_sp_misconfigured"
        assert exc.value.status_code == 503

    async def test_sp_metadata_is_escaped_and_unsigned_requests(self, env):
        async with env.app.app_context():
            xml = SAMLService.get_sp_metadata('https://elder.example.com/"><x', 5)
        assert 'AuthnRequestsSigned="false"' in xml
        assert '"><x' not in xml
        assert "&quot;&gt;&lt;x" in xml
        root = SAMLService.get_sp_metadata("https://elder.example.com/")
        assert 'entityID="https://elder.example.com/saml/metadata"' in root


@pytest.mark.asyncio
class TestProcessSamlResponse:
    """End-to-end ACS processing against the identity/tenant model (real DB)."""

    async def _run(self, env, keys, *, tenant=None, **spec_kw):
        """Start a login, answer it with a signed response, and run process_saml_response."""
        tenant = tenant or env.tenant_a
        token, request_id = _login(env)
        b64 = idp.signed_response_b64(_spec(env, request_id, **spec_kw), keys)
        return await SAMLService.process_saml_response(tenant, b64, token)

    async def test_existing_user_authenticated_with_stored_role(self, env, keys):
        async with env.app.app_context():
            uid = _user(env, "jane.doe@example.com", role="maintainer")
            out = await self._run(env, keys)
        assert out["id"] == uid
        assert out["tenant_id"] == env.tenant_a
        assert out["tenant_role"] == "maintainer"
        assert out["global_role"] is None
        assert not out.get("jit_provisioned")

    async def test_tenant_scoped_idp_strips_global_role(self, env, keys):
        async with env.app.app_context():
            _user(env, "jane.doe@example.com", global_role="admin")
            out = await self._run(env, keys)
        assert out["global_role"] is None

    async def test_global_idp_preserves_global_role(self, env, keys):
        db = env.db
        db(db.idp_configurations.id == env.idp_id).update(is_active=False)
        now = datetime.now(UTC)
        db.idp_configurations.insert(
            tenant_id=None,
            idp_type="saml",
            name="Global IdP",
            entity_id=env.idp_entity,
            sso_url="https://idp.example.com/sso",
            certificate=keys.cert_b64,
            attribute_mappings={},
            jit_provisioning_enabled=False,
            default_role="reader",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.commit()
        async with env.app.app_context():
            _user(env, "jane.doe@example.com", global_role="admin")
            out = await self._run(env, keys)
        assert out["global_role"] == "admin"

    async def test_email_matched_per_tenant_never_across_tenants(self, env, keys):
        async with env.app.app_context():
            other = _user(
                env, "jane.doe@example.com", tenant=env.tenant_b, role="admin"
            )
            out = await self._run(env, keys)  # tenant A has no such user -> JIT
        assert out["jit_provisioned"] is True
        assert out["id"] != other
        assert out["tenant_id"] == env.tenant_a
        assert out["tenant_role"] == "reader"

    async def test_email_is_case_insensitive(self, env, keys):
        async with env.app.app_context():
            uid = _user(env, "jane.doe@example.com")
            out = await self._run(
                env,
                keys,
                name_id="Jane.Doe@Example.COM",
                attributes={"email": ["Jane.Doe@Example.COM"]},
            )
        assert out["id"] == uid

    async def test_nameid_email_fallback_and_full_name_from_attribute(self, env, keys):
        async with env.app.app_context():
            out = await self._run(
                env, keys, name_id="bob@example.com", attributes={"name": ["Bob B"]}
            )
            row = env.db(env.db.portal_users.id == out["id"]).select().first()
        assert out["email"] == "bob@example.com"
        assert row.full_name == "Bob B"
        assert row.password_hash is None
        assert row.email_verified is True

    async def test_jit_disabled_refuses_unknown_user(self, env, keys):
        db = env.db
        db(db.idp_configurations.id == env.idp_id).update(
            jit_provisioning_enabled=False
        )
        db.commit()
        async with env.app.app_context():
            out = await self._run(env, keys)
        assert out["error"] == "saml_jit_disabled"
        assert out["status_code"] == 403

    async def test_deactivated_user_refused_and_not_reactivated(self, env, keys):
        async with env.app.app_context():
            uid = _user(env, "jane.doe@example.com", active=False)
            out = await self._run(env, keys)
            row = env.db(env.db.portal_users.id == uid).select().first()
        assert out["error"] == "saml_account_deactivated"
        assert out["status_code"] == 403
        assert not row.is_active

    @pytest.mark.parametrize(
        ("spec_kw", "code"),
        [
            ({"name_id": "no-at-sign", "attributes": {}}, "saml_email_missing"),
            (
                {"name_id": "x" * 300 + "@example.com", "attributes": {}},
                "saml_email_invalid",
            ),
            ({"attributes": {"email": ["not an email"]}}, "saml_email_invalid"),
        ],
    )
    async def test_missing_or_invalid_email_rejected(self, env, keys, spec_kw, code):
        async with env.app.app_context():
            out = await self._run(env, keys, **spec_kw)
        assert out["error"] == code
        assert out["status_code"] == 400

    async def test_role_mapping_only_via_explicit_role_map(self, env, keys):
        db = env.db
        db(db.idp_configurations.id == env.idp_id).update(
            attribute_mappings={
                "role": "groups",
                "role_map": {"eng": "maintainer", "ops": "admin", "bogus": "superuser"},
            }
        )
        db.commit()
        cases = [
            (["eng"], "maintainer"),
            (["eng", "ops"], "admin"),  # highest mapped wins
            (["unmapped"], "reader"),  # no passthrough of raw group names
            (["admin"], "reader"),  # ... even if it spells an Elder role
            (["bogus"], "reader"),  # role_map target outside the valid set
            ([], "reader"),
        ]
        # The admin-seat limit has its own test; keep this one independent of
        # whatever license-enforcement flag state other tests left behind.
        allow = AsyncMock(return_value=None)
        async with env.app.app_context():
            for n, (groups, expected) in enumerate(cases):
                email = f"user{n}@example.com"
                with patch("apps.api.services.sso.saml_service.check_limit", allow):
                    out = await self._run(
                        env,
                        keys,
                        name_id=email,
                        attributes={"email": [email], "groups": groups},
                    )
                assert out["tenant_role"] == expected, (groups, out)

    async def test_default_role_constrained_to_valid_roles(self, env, keys):
        db = env.db
        db(db.idp_configurations.id == env.idp_id).update(default_role="superadmin")
        db.commit()
        async with env.app.app_context():
            out = await self._run(env, keys)
        assert out["tenant_role"] == "reader"

    async def test_new_admin_subject_to_admin_seat_limit(self, env, keys):
        db = env.db
        db(db.idp_configurations.id == env.idp_id).update(default_role="admin")
        db.commit()
        blocked = AsyncMock(return_value=({"error": "limit_reached"}, 402))
        async with env.app.app_context():
            with patch("apps.api.services.sso.saml_service.check_limit", blocked):
                out = await self._run(env, keys)
        blocked.assert_awaited_once_with("tenant_admin", env.tenant_a)
        assert out["error"] == "limit_reached"
        assert out["status_code"] == 402

    async def test_rejections_use_the_stable_error_dict(self, env, keys):
        async with env.app.app_context():
            token, request_id = _login(env)
            b64 = idp.signed_response_b64(_spec(env, request_id, sign="none"), keys)
            out = await SAMLService.process_saml_response(env.tenant_a, b64, token)
            no_state = await SAMLService.process_saml_response(env.tenant_a, b64, None)
        assert set(out) == {"error", "message", "status_code"}
        assert out["error"] == "saml_unsigned"
        assert no_state["error"] == "saml_login_state_missing"

    async def test_failed_attempt_burns_login_state(self, env, keys):
        async with env.app.app_context():
            token, request_id = _login(env)
            bad = idp.signed_response_b64(_spec(env, request_id, sign="none"), keys)
            good = idp.signed_response_b64(_spec(env, request_id), keys)
            first = await SAMLService.process_saml_response(env.tenant_a, bad, token)
            second = await SAMLService.process_saml_response(env.tenant_a, good, token)
        assert first["error"] == "saml_unsigned"
        assert second["error"] == "saml_login_state_missing"

    async def test_unconfigured_tenant_and_logging_hygiene(self, env, keys, capsys):
        async with env.app.app_context():
            out = await SAMLService.process_saml_response(99_999_999, "AA", "T" * 43)
        assert out["error"] == "saml_tenant_not_found"
        captured = capsys.readouterr()
        assert "jane.doe" not in captured.out + captured.err

    async def test_unexpected_errors_propagate_not_swallowed(self, env, keys):
        async with env.app.app_context():
            token, request_id = _login(env)
            b64 = idp.signed_response_b64(_spec(env, request_id), keys)
            with patch.object(
                SAMLService, "_user_exists", side_effect=RuntimeError("boom")
            ):
                with pytest.raises(RuntimeError):
                    await SAMLService.process_saml_response(env.tenant_a, b64, token)


def _set_cookies(resp) -> dict[str, str]:
    """Map cookie name -> full Set-Cookie header for a response."""
    return {h.split("=", 1)[0]: h for h in resp.headers.get_all("Set-Cookie")}


def _cookie_value(header: str) -> str:
    """Extract the bare value from a Set-Cookie header."""
    return header.split(";", 1)[0].split("=", 1)[1]


@pytest.mark.asyncio
class TestHttpEndpoints:
    """HTTP layer: tier gate, state cookie, CSRF exemption and response shape."""

    async def _start(self, env, async_client, **query):
        """Hit the SAML login endpoint for tenant A."""
        resp = await async_client.get(
            f"/api/v1/sso/saml/login/{env.tenant_a}", query_string=query
        )
        return resp

    async def test_full_sp_initiated_login_issues_session(
        self, env, keys, async_client, enterprise_tier
    ):
        login = await self._start(env, async_client, return_to="/dashboard")
        assert login.status_code == 200
        body = await login.get_json()
        assert body["sso_url"].startswith("https://idp.example.com/sso?SAMLRequest=")
        flags = _set_cookies(login)[STATE_COOKIE]
        assert "HttpOnly" in flags and "Secure" in flags and "SameSite=None" in flags
        assert f"Path=/api/v1/sso/saml/acs/{env.tenant_a}" in flags
        token = _cookie_value(flags)
        request_id = idp.request_id_from_redirect(body["sso_url"])

        async with env.app.app_context():
            uid = _user(env, "jane.doe@example.com", role="maintainer")
        b64 = idp.signed_response_b64(_spec(env, request_id), keys)
        resp = await async_client.post(
            f"/api/v1/sso/saml/acs/{env.tenant_a}",
            form={"SAMLResponse": b64, "RelayState": "https://evil.example/ignored"},
            headers={"Cookie": f"{STATE_COOKIE}={token}"},
        )
        assert resp.status_code == 200, (await resp.get_data())[:300]
        out = await resp.get_json()

        assert set(out["user"]) == {
            "id",
            "email",
            "tenant_id",
            "tenant_role",
            "global_role",
        }
        assert out["user"] == {
            "id": uid,
            "email": "jane.doe@example.com",
            "tenant_id": env.tenant_a,
            "tenant_role": "maintainer",
            "global_role": None,
        }
        assert out["return_to"] == "/dashboard"  # server-side value, RelayState ignored
        secret = env.app.config.get("JWT_SECRET_KEY") or env.app.config["SECRET_KEY"]
        claims = jwt.decode(out["access_token"], secret, algorithms=["HS256"])
        assert claims["sub"] == str(uid)
        assert claims["tenant_id"] == env.tenant_a
        assert claims["type"] == "portal_user"
        cookies = _set_cookies(resp)
        assert ACCESS_COOKIE_NAME in cookies and REFRESH_COOKIE_NAME in cookies
        assert (
            "Max-Age=0" in cookies[STATE_COOKIE]
            or "expires=" in cookies[STATE_COOKIE].lower()
        )

        again = await async_client.post(
            f"/api/v1/sso/saml/acs/{env.tenant_a}",
            form={"SAMLResponse": b64},
            headers={"Cookie": f"{STATE_COOKIE}={token}"},
        )
        assert again.status_code == 400
        assert (await again.get_json())["error"] == "saml_login_state_missing"

    async def test_acs_without_login_state_is_rejected_as_unsolicited(
        self, env, keys, async_client, enterprise_tier
    ):
        b64 = idp.signed_response_b64(_spec(env, "_whatever"), keys)
        resp = await async_client.post(
            f"/api/v1/sso/saml/acs/{env.tenant_a}", form={"SAMLResponse": b64}
        )
        assert resp.status_code == 400
        assert (await resp.get_json())["error"] == "saml_login_state_missing"
        assert ACCESS_COOKIE_NAME not in "\n".join(resp.headers.get_all("Set-Cookie"))

    async def test_other_browsers_state_cookie_cannot_complete_a_login(
        self, env, keys, async_client, enterprise_tier
    ):
        """Login CSRF: attacker's valid response + victim's (different) cookie."""
        login = await self._start(env, async_client)
        request_id = idp.request_id_from_redirect((await login.get_json())["sso_url"])
        b64 = idp.signed_response_b64(_spec(env, request_id), keys)
        resp = await async_client.post(
            f"/api/v1/sso/saml/acs/{env.tenant_a}",
            form={"SAMLResponse": b64},
            headers={"Cookie": f"{STATE_COOKIE}={'V' * 43}"},
        )
        assert resp.status_code == 400
        assert (await resp.get_json())["error"] == "saml_login_state_missing"

    @pytest.mark.parametrize(
        ("spec_kw", "code"),
        [
            ({"sign": "none"}, "saml_unsigned"),
            ({"audience": "__sp__2"}, "saml_audience_mismatch"),
        ],
    )
    async def test_acs_rejection_is_specific_4xx_with_no_session(
        self, env, keys, async_client, enterprise_tier, spec_kw, code
    ):
        login = await self._start(env, async_client)
        token = _cookie_value(_set_cookies(login)[STATE_COOKIE])
        request_id = idp.request_id_from_redirect((await login.get_json())["sso_url"])
        b64 = idp.signed_response_b64(_spec(env, request_id, **spec_kw), keys)
        resp = await async_client.post(
            f"/api/v1/sso/saml/acs/{env.tenant_a}",
            form={"SAMLResponse": b64},
            headers={"Cookie": f"{STATE_COOKIE}={token}"},
        )
        assert resp.status_code == 401
        body = await resp.get_json()
        assert body == {"error": code, "message": body["message"]}
        joined = "\n".join(resp.headers.get_all("Set-Cookie"))
        assert ACCESS_COOKIE_NAME not in joined and REFRESH_COOKIE_NAME not in joined
        assert "jane.doe" not in body["message"]

    async def test_acs_not_blocked_by_csrf_for_users_holding_auth_cookies(
        self, env, keys, async_client, enterprise_tier
    ):
        resp = await async_client.post(
            f"/api/v1/sso/saml/acs/{env.tenant_a}",
            form={"SAMLResponse": "AA"},
            headers={
                "Cookie": f"{ACCESS_COOKIE_NAME}=stale; {STATE_COOKIE}={'S' * 43}"
            },
        )
        assert resp.status_code == 400
        assert (await resp.get_json())["error"] != "CSRF token missing or invalid"

    async def test_acs_missing_saml_response(self, env, async_client, enterprise_tier):
        resp = await async_client.post(f"/api/v1/sso/saml/acs/{env.tenant_a}", form={})
        assert resp.status_code == 400

    async def test_acs_forged_host_header_cannot_retarget(
        self, env, keys, async_client, enterprise_tier
    ):
        login = await self._start(env, async_client)
        token = _cookie_value(_set_cookies(login)[STATE_COOKIE])
        request_id = idp.request_id_from_redirect((await login.get_json())["sso_url"])
        evil_entity, evil_acs, _ = SAMLService.sp_urls(
            "https://evil.example.com", env.tenant_a
        )
        spec = idp.ResponseSpec(
            sp_entity_id=evil_entity, acs_url=evil_acs, in_response_to=request_id
        )
        resp = await async_client.post(
            f"/api/v1/sso/saml/acs/{env.tenant_a}",
            form={"SAMLResponse": idp.signed_response_b64(spec, keys)},
            headers={"Cookie": f"{STATE_COOKIE}={token}", "Host": "evil.example.com"},
        )
        assert resp.status_code == 401

    @pytest.mark.parametrize("path", ["login", "acs"])
    async def test_free_tier_is_refused_with_upgrade_prompt(
        self, env, async_client, path
    ):
        if path == "login":
            resp = await async_client.get(f"/api/v1/sso/saml/login/{env.tenant_a}")
        else:
            resp = await async_client.post(
                f"/api/v1/sso/saml/acs/{env.tenant_a}", form={"SAMLResponse": "AA"}
            )
        assert resp.status_code == 403
        body = await resp.get_json()
        assert (
            body["error"] == "tier_required" and body["required_tier"] == "enterprise"
        )

    async def test_login_errors_are_specific(self, env, async_client, enterprise_tier):
        bad = await self._start(env, async_client, return_to="//evil.com")
        assert bad.status_code == 400
        assert (await bad.get_json())["error"] == "saml_return_to_invalid"
        missing = await async_client.get("/api/v1/sso/saml/login/99999999")
        assert missing.status_code == 404
        env.app.extensions["module_redis"] = None
        down = await self._start(env, async_client)
        assert down.status_code == 503
        assert (await down.get_json())["error"] == "saml_state_store_unavailable"
        env.app.config["SAML_SP_BASE_URL"] = "ftp://nope"
        env.app.config["SITE_URL"] = ""
        no_base = await self._start(env, async_client)
        assert no_base.status_code == 503
        meta = await async_client.get("/api/v1/sso/saml/metadata/1")
        assert meta.status_code == 503
        acs = await async_client.post(
            f"/api/v1/sso/saml/acs/{env.tenant_a}",
            form={"SAMLResponse": "AA"},
            headers={"Cookie": f"{STATE_COOKIE}={'S' * 43}"},
        )
        assert acs.status_code in {400, 503}

    async def test_metadata_uses_configured_base_not_host_header(
        self, env, async_client
    ):
        resp = await async_client.get(
            f"/api/v1/sso/saml/metadata/{env.tenant_a}",
            headers={"Host": "evil.example.com"},
        )
        text = (await resp.get_data()).decode()
        assert resp.status_code == 200
        assert f'entityID="{env.entity}"' in text
        assert f'Location="{env.acs}"' in text
        assert "evil.example.com" not in text
        assert 'AuthnRequestsSigned="false"' in text


@pytest.mark.asyncio
class TestIdpConfigCrud:
    """IdP config CRUD (pre-existing behaviour) exercised against the real DB."""

    async def test_create_get_list_update_delete_roundtrip(self, env, keys):
        async with env.app.app_context():
            created = SAMLService.create_idp_config(
                name="CRUD IdP",
                tenant_id=env.tenant_b,
                entity_id="https://crud-idp",
                sso_url="https://crud-idp/sso",
                certificate=keys.cert_b64,
            )
            cfg = SAMLService.get_idp_config(env.tenant_b)
            assert cfg["id"] == created["id"] and cfg["idp_type"] == "saml"
            assert cfg["certificate"] == keys.cert_b64
            listed = SAMLService.list_idp_configs(env.tenant_b)
            assert any(c["id"] == created["id"] for c in listed)
            assert SAMLService.update_idp_config(
                created["id"], name="Renamed", bogus="x"
            )["updated"]
            assert SAMLService.update_idp_config(created["id"])["updated"]
            assert SAMLService.update_idp_config(99_999_999)["error"]
            oidc = SAMLService.create_idp_config(
                name="OIDC",
                idp_type="oidc",
                tenant_id=env.tenant_b,
                oidc_client_id="cid",
                oidc_issuer_url="https://issuer",
            )
            assert SAMLService.list_idp_configs(None)
            oidc_cfg = next(
                c
                for c in SAMLService.list_idp_configs(env.tenant_b)
                if c["id"] == oidc["id"]
            )
            assert oidc_cfg["oidc_client_id"] == "cid"
            assert SAMLService.delete_idp_config(created["id"]) == {"deleted": True}
            assert SAMLService.delete_idp_config(oidc["id"]) == {"deleted": True}
            assert SAMLService.delete_idp_config(created["id"])["error"]
            assert SAMLService.get_idp_config(env.tenant_b) is None
