"""Regression tests for SAML login & IdP configuration hardening (gh-312).

regression: gh-312 -- verifies all six hardening items:
1. Per-route rate limiting on GET /api/v1/sso/saml/login/<tenant_id>.
2. Uniform 404 error responses on saml_login (no tenant/config enumeration).
3. X.509 certificate not_valid_after_utc validation at CRUD create/update AND
   at SAML login initiation and ACS assertion verification.
4. Mandatory https:// scheme on IdP HTTP endpoint URLs (metadata_url, sso_url,
   slo_url, oidc_issuer_url) on create and update, while preserving URN entity_id.
5. Protocol-type filtering and deterministic ordering in SAMLService.get_idp_config
   so an OIDC config can never shadow a SAML config.
6. Explicit 501 Not Implemented on SAML SLO (GET and POST) with SP metadata
   omitting SingleLogoutService.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import jwt
import pytest
import pytest_asyncio
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from quart import current_app

from apps.api.api.v1.sso import _SAML_LOGIN_UNAVAILABLE_MSG
from apps.api.services.sso import OIDCService, SAMLService
from apps.api.services.sso.idp_access import IdPCaller
from apps.api.services.sso.idp_validation import (
    IdPValidationError,
    validate_https_url,
    validate_x509_certificate,
)

# RFC 5737 TEST-NET public IPs so penguin_limiter's skip_private_ips does not bypass.
# Each rate-limit test uses its own IP so shared CI Redis counters cannot collide.
PUBLIC_IP_1 = "203.0.113.10"
PUBLIC_IP_2 = "203.0.113.11"
PUBLIC_IP_3 = "198.51.100.10"


def _make_pem_cert(*, days_from_now: int = 365, cn: str = "idp.example.test") -> str:
    """Build a self-signed X.509 PEM certificate with a configurable expiry."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.now(UTC)
    if days_from_now >= 0:
        not_before = now - timedelta(days=1)
        not_after = now + timedelta(days=days_from_now)
    else:
        not_before = now + timedelta(days=days_from_now - 30)
        not_after = now + timedelta(days=days_from_now)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode("utf-8")


VALID_CERT = _make_pem_cert(days_from_now=365, cn="valid-idp.example.test")
EXPIRED_CERT = _make_pem_cert(days_from_now=-5, cn="expired-idp.example.test")


def _mint_admin_token(app, *, tenant_id: int) -> str:
    """Mint a tenant-admin portal JWT for CRUD route tests."""
    secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
    now = datetime.now(UTC)
    payload = {
        "sub": "901",
        "email": "admin@example.test",
        "tenant_id": tenant_id,
        "tenant_role": "admin",
        "global_role": None,
        "type": "portal_user",
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture(autouse=True)
def _patch_login_required(mocker):
    """POST /idp also carries @login_required (identity auth); stub it."""
    mocker.patch(
        "apps.api.auth.decorators.get_current_user",
        return_value=MagicMock(id=1, is_superuser=False),
    )


@pytest_asyncio.fixture
async def saml_world(app):
    """Create isolated tenants and clean up all rows after each test."""
    prefix = f"gh312-{uuid4().hex[:8]}"
    created_tenants: list[int] = []

    async with app.app_context():
        db = current_app.db
        # Clear any stray global IdP rows so global fallback tests stay deterministic.
        saved_global_ids = [
            row.id
            for row in db(db.idp_configurations.tenant_id == None).select()  # noqa: E711
        ]
        if saved_global_ids:
            db(db.idp_configurations.id.belongs(saved_global_ids)).update(
                is_active=False
            )
            db.commit()

        def make_tenant(label: str) -> int:
            tid = db.tenants.insert(
                name=f"{prefix}-{label}",
                slug=f"{prefix}-{label}",
                is_active=True,
            )
            db.commit()
            created_tenants.append(tid)
            return tid

        t_ok = make_tenant("ok")
        t_empty = make_tenant("empty")
        t_oidc_only = make_tenant("oidc-only")
        t_disabled = make_tenant("disabled")
        t_no_url = make_tenant("no-url")
        t_http_url = make_tenant("http-url")
        t_expired_cert = make_tenant("expired-cert")
        t_bad_cert = make_tenant("bad-cert")
        t_shadow = make_tenant("shadow")

        def insert_idp(
            tenant_id: int | None, idp_type: str, label: str, **extra
        ) -> int:
            cid = db.idp_configurations.insert(
                tenant_id=tenant_id,
                idp_type=idp_type,
                name=f"{prefix}-{label}",
                attribute_mappings={},
                jit_provisioning_enabled=True,
                default_role="reader",
                is_active=extra.pop("is_active", True),
                **extra,
            )
            db.commit()
            return cid

        ok_saml_id = insert_idp(
            t_ok,
            "saml",
            "ok-saml",
            entity_id="urn:example:ok-idp",
            sso_url="https://idp-ok.example.test/sso",
            certificate=VALID_CERT,
        )
        insert_idp(
            t_oidc_only,
            "oidc",
            "oidc-only",
            oidc_client_id="cid",
            oidc_client_secret="csec",
            oidc_issuer_url="https://oidc.example.test",
        )
        insert_idp(
            t_disabled,
            "saml",
            "disabled-saml",
            sso_url="https://disabled.example.test/sso",
            certificate=VALID_CERT,
            is_active=False,
        )
        insert_idp(
            t_no_url,
            "saml",
            "no-url-saml",
            sso_url=None,
            certificate=VALID_CERT,
        )
        insert_idp(
            t_http_url,
            "saml",
            "http-url-saml",
            sso_url="http://insecure.example.test/sso",
            certificate=VALID_CERT,
        )
        insert_idp(
            t_expired_cert,
            "saml",
            "expired-cert-saml",
            sso_url="https://expired.example.test/sso",
            certificate=EXPIRED_CERT,
        )
        insert_idp(
            t_bad_cert,
            "saml",
            "bad-cert-saml",
            sso_url="https://badcert.example.test/sso",
            certificate="NOT-A-VALID-PEM-CERT",
        )
        # Insert OIDC FIRST (lower id), then SAML #1, then SAML #2 to test shadowing + order.
        shadow_oidc_id = insert_idp(
            t_shadow,
            "oidc",
            "shadow-oidc",
            oidc_client_id="shadow-cid",
            oidc_client_secret="shadow-sec",
            oidc_issuer_url="https://shadow-oidc.example.test",
        )
        shadow_saml_first_id = insert_idp(
            t_shadow,
            "saml",
            "shadow-saml-1",
            sso_url="https://shadow-saml-1.example.test/sso",
            certificate=VALID_CERT,
        )
        shadow_saml_second_id = insert_idp(
            t_shadow,
            "saml",
            "shadow-saml-2",
            sso_url="https://shadow-saml-2.example.test/sso",
            certificate=VALID_CERT,
        )

    yield {
        "prefix": prefix,
        "t_ok": t_ok,
        "ok_saml_id": ok_saml_id,
        "t_empty": t_empty,
        "t_oidc_only": t_oidc_only,
        "t_disabled": t_disabled,
        "t_no_url": t_no_url,
        "t_http_url": t_http_url,
        "t_expired_cert": t_expired_cert,
        "t_bad_cert": t_bad_cert,
        "t_shadow": t_shadow,
        "shadow_oidc_id": shadow_oidc_id,
        "shadow_saml_first_id": shadow_saml_first_id,
        "shadow_saml_second_id": shadow_saml_second_id,
        "admin_headers": {
            "Authorization": f"Bearer {_mint_admin_token(app, tenant_id=t_ok)}"
        },
    }

    async with app.app_context():
        db = current_app.db
        db(db.idp_configurations.name.startswith(prefix)).delete()
        for tid in created_tenants:
            db(db.idp_configurations.tenant_id == tid).delete()
            db(db.tenants.id == tid).delete()
        if saved_global_ids:
            db(db.idp_configurations.id.belongs(saved_global_ids)).update(
                is_active=True
            )
        db.commit()


def _clear_saml_rate_limit_state(app) -> None:
    """Clear per-route rate-limiter cache and any Redis counters for SAML login."""
    app.extensions.pop("route_rate_limiters", None)
    redis_client = getattr(app, "redis_client", None)
    if redis_client is not None:
        keys = list(redis_client.scan_iter(match="*elder_saml_login*"))
        if keys:
            redis_client.delete(*keys)


class TestSamlLoginRateLimit:
    """regression: gh-312 -- per-route rate limit on SAML login initiation."""

    @pytest.fixture(autouse=True)
    def _isolate_rate_limit_counters(self, app):
        _clear_saml_rate_limit_state(app)
        yield
        _clear_saml_rate_limit_state(app)

    @pytest.mark.asyncio
    async def test_saml_login_enforces_rate_limit_and_returns_429(
        self, app, async_client, saml_world, monkeypatch
    ):
        monkeypatch.setitem(app.config, "RATELIMIT_ENABLED", True)
        monkeypatch.setitem(app.config, "SAML_LOGIN_RATELIMIT", "2/minute")

        headers = {"X-Forwarded-For": PUBLIC_IP_1}
        url = f"/api/v1/sso/saml/login/{saml_world['t_ok']}"

        r1 = await async_client.get(url, headers=headers)
        r2 = await async_client.get(url, headers=headers)
        r3 = await async_client.get(url, headers=headers)

        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r3.status_code == 429
        assert "Retry-After" in r3.headers
        assert (await r3.get_json())["error"] == "Rate Limit Exceeded"

        # A different client IP must still have its own independent quota.
        r_other = await async_client.get(url, headers={"X-Forwarded-For": PUBLIC_IP_2})
        assert r_other.status_code == 200

    @pytest.mark.asyncio
    async def test_saml_login_rate_limit_also_bounds_nonexistent_tenant_probes(
        self, app, async_client, saml_world, monkeypatch
    ):
        monkeypatch.setitem(app.config, "RATELIMIT_ENABLED", True)
        monkeypatch.setitem(app.config, "SAML_LOGIN_RATELIMIT", "1/minute")

        headers = {"X-Forwarded-For": PUBLIC_IP_3}
        r1 = await async_client.get("/api/v1/sso/saml/login/9999999", headers=headers)
        r2 = await async_client.get("/api/v1/sso/saml/login/9999998", headers=headers)

        assert r1.status_code == 404
        assert r2.status_code == 429


class TestSamlLoginUniformErrors:
    """regression: gh-312 -- uniform error responses prevent tenant enumeration."""

    @pytest.mark.asyncio
    async def test_all_unavailable_states_return_identical_404_body(
        self, async_client, saml_world
    ):
        unavailable_tenants = [
            999999999,  # nonexistent tenant
            saml_world["t_empty"],  # existing tenant with no IdP
            saml_world["t_oidc_only"],  # tenant with OIDC only
            saml_world["t_disabled"],  # tenant with disabled SAML IdP
            saml_world["t_no_url"],  # SAML IdP missing sso_url
            saml_world["t_http_url"],  # legacy non-HTTPS sso_url
            saml_world["t_expired_cert"],  # expired IdP certificate
            saml_world["t_bad_cert"],  # malformed IdP certificate
        ]
        bodies = []
        for tid in unavailable_tenants:
            resp = await async_client.get(f"/api/v1/sso/saml/login/{tid}")
            assert resp.status_code == 404, f"tenant={tid} got {resp.status_code}"
            body = await resp.get_json()
            assert body == {"error": _SAML_LOGIN_UNAVAILABLE_MSG}
            bodies.append(body)

        assert all(b == bodies[0] for b in bodies)

    @pytest.mark.asyncio
    async def test_valid_saml_tenant_returns_200_with_https_sso_url(
        self, async_client, saml_world
    ):
        resp = await async_client.get(f"/api/v1/sso/saml/login/{saml_world['t_ok']}")
        assert resp.status_code == 200
        body = await resp.get_json()
        assert body["sso_url"] == "https://idp-ok.example.test/sso"


class TestCertificateExpiryValidation:
    """regression: gh-312 -- X.509 cert not_valid_after_utc checked at CRUD & login."""

    def test_validate_x509_certificate_accepts_valid_and_rejects_expired(self):
        cert = validate_x509_certificate(VALID_CERT)
        assert cert.not_valid_after_utc > datetime.now(UTC)

        with pytest.raises(IdPValidationError, match="expired"):
            validate_x509_certificate(EXPIRED_CERT)

        with pytest.raises(IdPValidationError, match="Invalid X.509"):
            validate_x509_certificate("not-a-pem")

        with pytest.raises(IdPValidationError, match="Invalid X.509"):
            validate_x509_certificate("")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "cert_value",
        [EXPIRED_CERT, "NOT-A-PEM-CERT", ""],
        ids=["expired", "malformed", "empty"],
    )
    async def test_create_idp_rejects_expired_or_invalid_cert_with_400(
        self, async_client, saml_world, cert_value
    ):
        resp = await async_client.post(
            "/api/v1/sso/idp",
            json={
                "name": f"{saml_world['prefix']}-bad-cert-create",
                "idp_type": "saml",
                "sso_url": "https://idp.example.test/sso",
                "certificate": cert_value,
            },
            headers=saml_world["admin_headers"],
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_create_idp_accepts_valid_pem_cert_with_201(
        self, async_client, saml_world
    ):
        resp = await async_client.post(
            "/api/v1/sso/idp",
            json={
                "name": f"{saml_world['prefix']}-valid-cert-create",
                "idp_type": "saml",
                "entity_id": "urn:example:valid-idp",
                "sso_url": "https://idp.example.test/sso",
                "certificate": VALID_CERT,
            },
            headers=saml_world["admin_headers"],
        )
        assert resp.status_code == 201, (await resp.get_data()).decode()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "cert_value",
        [EXPIRED_CERT, "NOT-A-PEM-CERT"],
        ids=["expired", "malformed"],
    )
    async def test_update_idp_rejects_expired_or_invalid_cert_with_400(
        self, async_client, saml_world, cert_value
    ):
        resp = await async_client.put(
            f"/api/v1/sso/idp/{saml_world['ok_saml_id']}",
            json={"certificate": cert_value},
            headers=saml_world["admin_headers"],
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_saml_acs_rejects_expired_idp_cert_with_400_and_no_cookies(
        self, async_client, saml_world
    ):
        resp = await async_client.post(
            f"/api/v1/sso/saml/acs/{saml_world['t_expired_cert']}",
            form={"SAMLResponse": "fake-saml-payload"},
        )
        assert resp.status_code == 400
        body = await resp.get_json()
        assert "expired" in body["error"].lower()
        assert not resp.headers.get_all("Set-Cookie")


class TestHttpsUrlValidation:
    """regression: gh-312 -- IdP HTTP endpoint URLs must use https:// on CRUD."""

    @pytest.mark.parametrize(
        "bad_url",
        [
            "http://idp.example.test/sso",
            "ftp://idp.example.test/sso",
            "javascript:alert(1)",
            "idp.example.test/sso",
            "https://",
            "https:///path-only",
            "https://idp.example.test/sso with space",
            "",
        ],
    )
    def test_validate_https_url_rejects_non_https(self, bad_url):
        with pytest.raises(IdPValidationError):
            validate_https_url(bad_url)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "field_name",
        ["metadata_url", "sso_url", "slo_url", "oidc_issuer_url"],
    )
    async def test_create_rejects_http_urls_on_all_endpoint_fields(
        self, async_client, saml_world, field_name
    ):
        resp = await async_client.post(
            "/api/v1/sso/idp",
            json={
                "name": f"{saml_world['prefix']}-http-{field_name}",
                "idp_type": "oidc" if field_name == "oidc_issuer_url" else "saml",
                field_name: "http://insecure.example.test/endpoint",
            },
            headers=saml_world["admin_headers"],
        )
        raw = (await resp.get_data()).decode()
        assert resp.status_code == 400, raw
        assert "http://insecure.example.test/endpoint" not in raw

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "field_name",
        ["metadata_url", "sso_url", "slo_url", "oidc_issuer_url"],
    )
    async def test_update_rejects_http_urls_on_all_endpoint_fields(
        self, async_client, saml_world, field_name
    ):
        resp = await async_client.put(
            f"/api/v1/sso/idp/{saml_world['ok_saml_id']}",
            json={field_name: "http://insecure.example.test/endpoint"},
            headers=saml_world["admin_headers"],
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_create_and_update_accept_urn_entity_id_and_https_urls(
        self, async_client, saml_world
    ):
        create_resp = await async_client.post(
            "/api/v1/sso/idp",
            json={
                "name": f"{saml_world['prefix']}-urn-ok",
                "idp_type": "saml",
                "entity_id": "urn:oasis:names:tc:SAML:2.0:example-idp",
                "metadata_url": "https://idp.example.test/metadata.xml",
                "sso_url": "https://idp.example.test/sso",
                "slo_url": "https://idp.example.test/slo",
                "certificate": VALID_CERT,
            },
            headers=saml_world["admin_headers"],
        )
        assert create_resp.status_code == 201, (await create_resp.get_data()).decode()
        created_id = (await create_resp.get_json())["id"]

        update_resp = await async_client.put(
            f"/api/v1/sso/idp/{created_id}",
            json={
                "entity_id": "urn:example:updated-idp",
                "sso_url": "https://idp.example.test/sso-v2",
            },
            headers=saml_world["admin_headers"],
        )
        assert update_resp.status_code == 200

    @pytest.mark.asyncio
    async def test_service_layer_rejects_http_url_directly(self, app, saml_world):
        caller = IdPCaller(tenant_id=saml_world["t_ok"])
        async with app.app_context():
            with pytest.raises(IdPValidationError):
                SAMLService.create_idp_config(
                    caller,
                    name=f"{saml_world['prefix']}-svc-http",
                    sso_url="http://insecure.example.test/sso",
                )
            with pytest.raises(IdPValidationError):
                SAMLService.update_idp_config(
                    saml_world["ok_saml_id"],
                    caller,
                    {"sso_url": "http://insecure.example.test/sso"},
                )


class TestGetIdpConfigProtocolFilteringAndOrdering:
    """regression: gh-312 -- OIDC config cannot shadow SAML; ordering is deterministic."""

    @pytest.mark.asyncio
    async def test_saml_get_idp_config_is_not_shadowed_by_lower_id_oidc_config(
        self, app, saml_world
    ):
        async with app.app_context():
            cfg = SAMLService.get_idp_config(saml_world["t_shadow"])
        assert cfg is not None
        assert cfg["idp_type"] == "saml"
        # Must pick the lowest-id active SAML config, never the lower-id OIDC config.
        assert cfg["id"] == saml_world["shadow_saml_first_id"]
        assert cfg["id"] != saml_world["shadow_oidc_id"]
        assert cfg["id"] != saml_world["shadow_saml_second_id"]

    @pytest.mark.asyncio
    async def test_saml_get_idp_config_returns_none_for_oidc_only_tenant(
        self, app, saml_world
    ):
        async with app.app_context():
            cfg = SAMLService.get_idp_config(saml_world["t_oidc_only"])
        assert cfg is None

    @pytest.mark.asyncio
    async def test_oidc_get_idp_config_by_tenant_returns_oidc_deterministically(
        self, app, saml_world
    ):
        async with app.app_context():
            cfg = OIDCService.get_idp_config_by_tenant(saml_world["t_shadow"])
        assert cfg is not None
        assert cfg["idp_type"] == "oidc"
        assert cfg["id"] == saml_world["shadow_oidc_id"]


class TestSamlSloNotImplemented:
    """regression: gh-312 -- SAML SLO returns explicit 501 instead of fake 200."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["get", "post"])
    async def test_saml_slo_returns_501_with_logout_guidance(
        self, async_client, saml_world, method
    ):
        resp = await getattr(async_client, method)(
            f"/api/v1/sso/saml/slo/{saml_world['t_ok']}"
        )
        assert resp.status_code == 501
        body = await resp.get_json()
        assert "not supported" in body["error"].lower()
        assert "/api/v1/auth/logout" in body["error"]

    @pytest.mark.asyncio
    async def test_sp_metadata_omits_single_logout_service(
        self, async_client, saml_world
    ):
        resp = await async_client.get(f"/api/v1/sso/saml/metadata/{saml_world['t_ok']}")
        assert resp.status_code == 200
        xml_body = (await resp.get_data()).decode()
        assert "AssertionConsumerService" in xml_body
        assert "SingleLogoutService" not in xml_body
