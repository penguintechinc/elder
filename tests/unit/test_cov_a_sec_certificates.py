"""Coverage tests for the certificates HTTP routes (real test client + real DB)."""

# flake8: noqa: E501

import json
import uuid
from datetime import UTC, date, datetime, timedelta

import jwt
import pytest
import pytest_asyncio
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from apps.api.utils.async_utils import run_in_threadpool

BASE = "/api/v1/certificates"
ALL_SCOPES = ["secrets:read", "secrets:write", "secrets:admin"]


def _tok(app, tenant_id, identity_id, scopes):
    """Mint a signed JWT for an existing identity with the given scopes."""
    payload = {
        "sub": str(identity_id),
        "type": "access",
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(hours=1),
        "scope": scopes,
        "tenant": str(tenant_id),
        "roles": ["admin"],
        "teams": [],
    }
    secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
    return jwt.encode(payload, secret, algorithm="HS256")


def _make_pem(common_name):
    """Generate a throwaway self-signed ECDSA certificate PEM at runtime."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=90))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


async def _call(app, method, url, token=None, **kw):
    """Issue a test-client request and return (status, parsed JSON or None)."""
    client = app.test_client()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    resp = await getattr(client, method)(url, headers=headers, **kw)
    body = await resp.get_data()
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    return resp.status_code, data


@pytest_asyncio.fixture
async def world(app):
    """Two tenants with an org each, a normal identity and a superuser identity."""
    db = app.db
    sfx = uuid.uuid4().hex[:8]

    def _setup():
        now = datetime.now(UTC)
        out = {}
        for key in ("a", "b"):
            tid = db.tenants.insert(
                name=f"cert-{key}-{sfx}", slug=f"cert-{key}-{sfx}", is_active=True
            )
            out[f"tenant_{key}"] = tid
            out[f"org_{key}"] = db.organizations.insert(
                name=f"corg-{key}-{sfx}", tenant_id=tid, created_at=now, updated_at=now
            )
        for label, superuser, tkey in (
            ("user", False, "tenant_a"),
            ("super", True, "tenant_a"),
            ("super_b", True, "tenant_b"),
        ):
            out[label] = db.identities.insert(
                identity_type="human",
                username=f"cert-{label}-{sfx}",
                email=f"cert-{label}-{sfx}@example.com",
                tenant_id=out[tkey],
                auth_provider="local",
                is_active=True,
                is_superuser=superuser,
                mfa_enabled=False,
                must_change_password=False,
                portal_role="observer",
                created_at=now,
                updated_at=now,
            )
        out["secret_a"] = db.builtin_secrets.insert(
            name=f"pk-a-{sfx}",
            organization_id=out["org_a"],
            secret_value="x",
            secret_type="password",
            is_active=True,
            village_id=f"c{sfx}a",
        )
        out["secret_b"] = db.builtin_secrets.insert(
            name=f"pk-b-{sfx}",
            organization_id=out["org_b"],
            secret_value="x",
            secret_type="password",
            is_active=True,
            village_id=f"c{sfx}b",
        )
        db.commit()
        return out

    w = await run_in_threadpool(_setup)
    w["sfx"] = sfx
    w["token"] = _tok(app, w["tenant_a"], w["user"], ALL_SCOPES)
    w["su_token"] = _tok(app, w["tenant_a"], w["super"], ALL_SCOPES)
    w["tenant_b_token"] = _tok(app, w["tenant_b"], w["super_b"], ALL_SCOPES)
    w["ro_token"] = _tok(app, w["tenant_a"], w["user"], ["secrets:read"])
    w["noscope_token"] = _tok(app, w["tenant_a"], w["user"], [])
    return w


def _body(world, **over):
    """Build a minimal valid certificate payload for tenant A."""
    today = date.today()
    body = {
        "name": f"cert-{uuid.uuid4().hex[:8]}",
        "organization_id": world["org_a"],
        "cert_type": "server_cert",
        "creator": "letsencrypt",
        "issue_date": today.isoformat(),
        "expiration_date": (today + timedelta(days=365)).isoformat(),
    }
    body.update(over)
    return body


async def _create(app, world, **over):
    """Create a certificate through the API and return its JSON."""
    status, data = await _call(
        app, "post", BASE, world["token"], json=_body(world, **over)
    )
    assert status == 201, data
    return data


class TestCertificateAuth:
    """Authentication and scope enforcement on the certificate routes."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method,url",
        [
            ("get", BASE),
            ("post", BASE),
            ("get", BASE + "/1"),
            ("put", BASE + "/1"),
            ("delete", BASE + "/1"),
        ],
    )
    async def test_no_token_is_401(self, app, method, url):
        """Unauthenticated calls are rejected with 401."""
        kw = {} if method in ("get", "delete") else {"json": {}}
        status, _ = await _call(app, method, url, None, **kw)
        assert status == 401

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method,url",
        [
            ("get", BASE),
            ("post", BASE),
            ("get", BASE + "/1"),
            ("put", BASE + "/1"),
            ("delete", BASE + "/1"),
        ],
    )
    async def test_missing_scope_is_403(self, app, world, method, url):
        """A token with no secrets scope gets 403 on every route."""
        kw = {} if method in ("get", "delete") else {"json": {}}
        status, _ = await _call(app, method, url, world["noscope_token"], **kw)
        assert status == 403

    @pytest.mark.asyncio
    async def test_read_scope_cannot_write(self, app, world):
        """A read-only token cannot create certificates."""
        status, _ = await _call(app, "post", BASE, world["ro_token"], json=_body(world))
        assert status == 403


class TestCertificateCreate:
    """POST /certificates validation and persistence."""

    @pytest.mark.asyncio
    async def test_create_persists_and_computes_status(self, app, world):
        """A valid create persists tenant-scoped row with computed status."""
        pem = _make_pem(f"c-{world['sfx']}")
        data = await _create(
            app,
            world,
            certificate_pem=pem,
            private_key_secret_id=world["secret_a"],
            validation_type="DV",
            renewal_method="manual",
            acme_challenge_type="http-01",
            ct_log_status="logged",
        )
        assert data["tenant_id"] == world["tenant_a"]
        assert data["status"] == "active"
        assert data["certificate_pem"] == pem
        assert data["private_key_secret_id"] == world["secret_a"]
        assert data["ct_log_status"] == "logged"

        def _row():
            return app.db.certificates[data["id"]]

        row = await run_in_threadpool(_row)
        assert row.tenant_id == world["tenant_a"]

    @pytest.mark.asyncio
    async def test_create_expiring_and_revoked_status(self, app, world):
        """Near-expiry certs are expiring_soon; revoked flag wins."""
        soon = (date.today() + timedelta(days=5)).isoformat()
        data = await _create(app, world, expiration_date=soon)
        assert data["status"] == "expiring_soon"
        data = await _create(app, world, is_revoked=True)
        assert data["status"] == "revoked"

    @pytest.mark.asyncio
    async def test_empty_body_is_400(self, app, world):
        """An empty JSON body is rejected with 400."""
        status, _ = await _call(app, "post", BASE, world["token"], json={})
        assert status == 400

    @pytest.mark.asyncio
    async def test_list_body_is_400(self, app, world):
        """A JSON array body should be a 400, not an unhandled 500."""
        status, _ = await _call(app, "post", BASE, world["token"], json=[1, 2])
        assert status == 400

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "missing",
        ["name", "organization_id", "cert_type", "creator", "expiration_date"],
    )
    async def test_missing_required_field_is_400(self, app, world, missing):
        """Each required field is enforced with a 400."""
        body = _body(world)
        body.pop(missing)
        status, data = await _call(app, "post", BASE, world["token"], json=body)
        assert status == 400
        assert missing in json.dumps(data)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "field",
        [
            "creator",
            "cert_type",
            "validation_type",
            "renewal_method",
            "acme_challenge_type",
            "ct_log_status",
        ],
    )
    async def test_invalid_enum_is_400(self, app, world, field):
        """An out-of-set enum value is rejected with 400 for every enum field."""
        status, _ = await _call(
            app, "post", BASE, world["token"], json=_body(world, **{field: "bogus"})
        )
        assert status == 400

    @pytest.mark.asyncio
    async def test_unknown_organization_is_404(self, app, world):
        """Creating under a non-existent organization returns 404."""
        status, _ = await _call(
            app,
            "post",
            BASE,
            world["token"],
            json=_body(world, organization_id=2_000_000_000),
        )
        assert status == 404

    @pytest.mark.asyncio
    async def test_org_without_tenant_is_400(self, app, world):
        """An organization lacking a tenant is rejected with 400."""

        def _mk():
            now = datetime.now(UTC)
            oid = app.db.organizations.insert(
                name=f"notenant-{world['sfx']}", created_at=now, updated_at=now
            )
            app.db.commit()
            return oid

        try:
            oid = await run_in_threadpool(_mk)
        except Exception:
            pytest.skip("organizations.tenant_id is NOT NULL in this schema")
        status, _ = await _call(
            app, "post", BASE, world["token"], json=_body(world, organization_id=oid)
        )
        assert status == 400

    @pytest.mark.asyncio
    async def test_private_key_secret_unknown_is_404(self, app, world):
        """A private_key_secret_id that does not exist returns 404."""
        status, _ = await _call(
            app,
            "post",
            BASE,
            world["token"],
            json=_body(world, private_key_secret_id=2_000_000_000),
        )
        assert status == 404

    @pytest.mark.asyncio
    async def test_private_key_secret_other_tenant_is_404(self, app, world):
        """A secret owned by another tenant cannot be linked (404, no leak)."""
        status, _ = await _call(
            app,
            "post",
            BASE,
            world["token"],
            json=_body(world, private_key_secret_id=world["secret_b"]),
        )
        assert status == 404


class TestCertificateList:
    """GET /certificates filtering, pagination and tenant isolation."""

    @pytest.mark.asyncio
    async def test_filters(self, app, world):
        """Each query filter narrows results to matching certificates."""
        sfx = world["sfx"]
        c1 = await _create(
            app,
            world,
            name=f"alpha-{sfx}",
            creator="digicert",
            cert_type="wildcard",
            common_name=f"cn-{sfx}.example.com",
            description=f"desc-{sfx}",
        )
        c2 = await _create(
            app,
            world,
            name=f"beta-{sfx}",
            expiration_date=(date.today() + timedelta(days=3)).isoformat(),
            is_active=False,
        )

        async def ids(qs):
            status, data = await _call(
                app, "get", f"{BASE}?{qs}&per_page=100", world["token"]
            )
            assert status == 200
            return {i["id"] for i in data["items"]}, data

        got, data = await ids(f"organization_id={world['org_a']}&search={sfx}")
        assert got == {c1["id"], c2["id"]}
        assert data["total"] == 2 and data["page"] == 1
        assert (await ids(f"search=cn-{sfx}"))[0] == {c1["id"]}
        assert (await ids(f"search=desc-{sfx}"))[0] == {c1["id"]}
        assert (await ids(f"creator=digicert&search={sfx}"))[0] == {c1["id"]}
        assert (await ids(f"cert_type=wildcard&search={sfx}"))[0] == {c1["id"]}
        assert (await ids(f"status=active&search={sfx}"))[0] == {c1["id"]}
        assert (await ids(f"is_active=false&search={sfx}"))[0] == {c2["id"]}
        assert (await ids(f"is_active=true&search={sfx}"))[0] == {c1["id"]}
        assert (await ids(f"expiring_soon=true&search={sfx}"))[0] == {c2["id"]}
        assert (await ids(f"search=nomatch-{sfx}"))[0] == set()

    @pytest.mark.asyncio
    async def test_unfiltered_list_returns_envelope(self, app, world):
        """A list without filters returns the pagination envelope with items."""
        c = await _create(app, world)
        status, data = await _call(app, "get", BASE + "?per_page=100", world["token"])
        assert status == 200
        assert {"items", "total", "page", "per_page", "pages"} <= set(data)
        assert c["id"] in [i["id"] for i in data["items"]]

    @pytest.mark.asyncio
    async def test_pagination(self, app, world):
        """per_page limits the page size while total reflects all matches."""
        for _ in range(3):
            await _create(app, world, name=f"pg-{world['sfx']}-{uuid.uuid4().hex[:4]}")
        status, data = await _call(
            app,
            "get",
            f"{BASE}?search=pg-{world['sfx']}&per_page=2&page=2",
            world["token"],
        )
        assert status == 200
        assert data["total"] == 3 and len(data["items"]) == 1 and data["page"] == 2

    @pytest.mark.asyncio
    async def test_status_recomputed_on_read(self, app, world):
        """A stale stored status is recomputed from the expiry date when listed."""
        c = await _create(app, world, name=f"stale-{world['sfx']}")

        def _age():
            app.db(app.db.certificates.id == c["id"]).update(
                expiration_date=date.today() - timedelta(days=2), status="active"
            )
            app.db.commit()

        await run_in_threadpool(_age)
        status, data = await _call(
            app, "get", f"{BASE}?search=stale-{world['sfx']}", world["token"]
        )
        assert status == 200
        assert data["items"][0]["status"] == "expired"

    @pytest.mark.asyncio
    async def test_tenant_isolation(self, app, world):
        """Tenant B never sees tenant A's certificates in a listing."""
        c = await _create(app, world, name=f"iso-{world['sfx']}")
        status, data = await _call(
            app, "get", f"{BASE}?search=iso-{world['sfx']}", world["tenant_b_token"]
        )
        assert status == 200
        assert c["id"] not in [i["id"] for i in data["items"]]


class TestCertificateGet:
    """GET /certificates/<id>."""

    @pytest.mark.asyncio
    async def test_get_returns_recomputed_status(self, app, world):
        """Fetching a certificate returns it with a status derived from its dates."""
        c = await _create(app, world)
        status, data = await _call(app, "get", f"{BASE}/{c['id']}", world["token"])
        assert status == 200
        assert data["id"] == c["id"] and data["status"] == "active"

    @pytest.mark.asyncio
    async def test_get_missing_is_404(self, app, world):
        """An unknown certificate id returns 404."""
        status, _ = await _call(app, "get", f"{BASE}/2000000000", world["token"])
        assert status == 404

    @pytest.mark.asyncio
    async def test_get_other_tenant_is_404(self, app, world):
        """Another tenant's certificate id yields 404, never its content."""
        c = await _create(app, world)
        status, data = await _call(
            app, "get", f"{BASE}/{c['id']}", world["tenant_b_token"]
        )
        assert status == 404
        assert c["name"] not in json.dumps(data)


class TestCertificateUpdate:
    """PUT /certificates/<id> (superuser bypasses the resource-role check)."""

    @pytest.mark.asyncio
    async def test_update_fields_dates_and_status(self, app, world):
        """Updating fields and dates persists them and recomputes status."""
        c = await _create(app, world)
        now = datetime.now(UTC)
        body = {
            "name": f"renamed-{world['sfx']}",
            "creator": "acme",
            "cert_type": "ecc",
            "validation_type": "OV",
            "renewal_method": "api",
            "acme_challenge_type": "dns-01",
            "ct_log_status": "pending",
            "private_key_secret_id": world["secret_a"],
            "organization_id": world["org_a"],
            "issue_date": date.today().isoformat(),
            "expiration_date": (date.today() + timedelta(days=4)).isoformat(),
            "not_before": now.isoformat(),
            "not_after": (now + timedelta(days=4)).isoformat(),
            "renewal_days_before": 10,
            "notes": "n",
        }
        status, data = await _call(
            app, "put", f"{BASE}/{c['id']}", world["su_token"], json=body
        )
        assert status == 200, data
        assert data["name"] == body["name"]
        assert data["status"] == "expiring_soon"
        assert data["private_key_secret_id"] == world["secret_a"]
        assert data["notes"] == "n"

    @pytest.mark.asyncio
    async def test_update_revoke_recomputes_status(self, app, world):
        """Setting is_revoked flips the stored status to revoked."""
        c = await _create(app, world)
        status, data = await _call(
            app,
            "put",
            f"{BASE}/{c['id']}",
            world["su_token"],
            json={"is_revoked": True},
        )
        assert status == 200 and data["status"] == "revoked"

    @pytest.mark.asyncio
    async def test_update_empty_body_noop(self, app, world):
        """An update with no recognised fields leaves the row unchanged."""
        c = await _create(app, world)
        status, data = await _call(
            app, "put", f"{BASE}/{c['id']}", world["su_token"], json={"unknown": 1}
        )
        assert status == 200 and data["name"] == c["name"]

    @pytest.mark.asyncio
    async def test_update_empty_body_is_400(self, app, world):
        """An empty update body returns 400."""
        c = await _create(app, world)
        status, _ = await _call(
            app, "put", f"{BASE}/{c['id']}", world["su_token"], json={}
        )
        assert status == 400

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "field",
        [
            "creator",
            "cert_type",
            "validation_type",
            "renewal_method",
            "acme_challenge_type",
            "ct_log_status",
        ],
    )
    async def test_update_invalid_enum_is_400(self, app, world, field):
        """Invalid enum values are rejected on update as on create."""
        c = await _create(app, world)
        status, _ = await _call(
            app, "put", f"{BASE}/{c['id']}", world["su_token"], json={field: "bogus"}
        )
        assert status == 400

    @pytest.mark.asyncio
    async def test_update_unknown_org_is_404(self, app, world):
        """Moving a certificate to a non-existent organization returns 404."""
        c = await _create(app, world)
        status, _ = await _call(
            app,
            "put",
            f"{BASE}/{c['id']}",
            world["su_token"],
            json={"organization_id": 2_000_000_000},
        )
        assert status == 404

    @pytest.mark.asyncio
    async def test_update_foreign_secret_is_404(self, app, world):
        """Linking another tenant's secret on update returns 404."""
        c = await _create(app, world)
        status, _ = await _call(
            app,
            "put",
            f"{BASE}/{c['id']}",
            world["su_token"],
            json={"private_key_secret_id": world["secret_b"]},
        )
        assert status == 404

    @pytest.mark.asyncio
    async def test_update_missing_is_404(self, app, world):
        """Updating an unknown id returns 404."""
        status, _ = await _call(
            app, "put", f"{BASE}/2000000000", world["su_token"], json={"notes": "x"}
        )
        assert status == 404

    @pytest.mark.asyncio
    async def test_update_other_tenant_is_404_and_unchanged(self, app, world):
        """Tenant B cannot update tenant A's certificate; the row is untouched."""
        c = await _create(app, world)
        status, _ = await _call(
            app,
            "put",
            f"{BASE}/{c['id']}",
            world["tenant_b_token"],
            json={"name": "hijacked"},
        )
        assert status == 404
        _, data = await _call(app, "get", f"{BASE}/{c['id']}", world["token"])
        assert data["name"] == c["name"]


class TestCertificateUpdateEdges:
    """Update-route branches: pre-parsed dates and partial status recompute."""

    @pytest.mark.asyncio
    async def test_update_only_issue_date_keeps_status(self, app, world):
        """Changing only issue_date does not touch the computed status."""
        c = await _create(app, world)
        status, data = await _call(
            app,
            "put",
            f"{BASE}/{c['id']}",
            world["su_token"],
            json={"issue_date": (date.today() - timedelta(days=1)).isoformat()},
        )
        assert status == 200 and data["status"] == "active"

    @pytest.mark.asyncio
    async def test_update_renewal_window_only(self, app, world):
        """Widening renewal_days_before moves a ~60d cert into expiring_soon."""
        c = await _create(
            app,
            world,
            expiration_date=(date.today() + timedelta(days=60)).isoformat(),
        )
        assert c["status"] == "active"
        status, data = await _call(
            app,
            "put",
            f"{BASE}/{c['id']}",
            world["su_token"],
            json={"renewal_days_before": 90},
        )
        assert status == 200 and data["status"] == "expiring_soon"


class TestCertificateDelete:
    """DELETE /certificates/<id>."""

    @pytest.mark.asyncio
    async def test_delete_removes_row(self, app, world):
        """Deleting removes the certificate; a later GET is 404."""
        c = await _create(app, world)
        status, _ = await _call(app, "delete", f"{BASE}/{c['id']}", world["su_token"])
        assert status == 204
        status, _ = await _call(app, "get", f"{BASE}/{c['id']}", world["token"])
        assert status == 404

    @pytest.mark.asyncio
    async def test_delete_missing_is_404(self, app, world):
        """Deleting an unknown id returns 404."""
        status, _ = await _call(app, "delete", f"{BASE}/2000000000", world["su_token"])
        assert status == 404

    @pytest.mark.asyncio
    async def test_delete_other_tenant_is_404_and_kept(self, app, world):
        """Tenant B cannot delete tenant A's certificate."""
        c = await _create(app, world)
        status, _ = await _call(
            app, "delete", f"{BASE}/{c['id']}", world["tenant_b_token"]
        )
        assert status == 404
        status, _ = await _call(app, "get", f"{BASE}/{c['id']}", world["token"])
        assert status == 200

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["put", "delete"])
    async def test_non_superuser_without_role_is_denied(self, app, world, method):
        """A normal user without a resource role cannot modify or delete."""
        c = await _create(app, world)
        kw = {"json": {"notes": "x"}} if method == "put" else {}
        status, _ = await _call(app, method, f"{BASE}/{c['id']}", world["token"], **kw)
        assert status in (400, 403)
