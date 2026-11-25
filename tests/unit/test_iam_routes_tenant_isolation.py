"""Two-tenant isolation tests for the IAM provider routes (/api/v1/iam).

The tenant comes only from the verified JWT ``tenant`` claim. A provider
belonging to another tenant's organization must be indistinguishable from a
missing one (404), and a token without a tenant claim is refused (403).
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from quart import current_app

from apps.api.services.iam.service import IAMService

SCOPES = ["discovery:read", "discovery:write", "discovery:admin"]


def _admin_user(tenant_id):
    """Build a mocked admin user for the auth decorators."""
    return MagicMock(
        id=1,
        is_superuser=True,
        global_role="admin",
        tenant_role="admin",
        tenant_id=tenant_id,
    )


def _tenant_with_provider(db, label):
    """Create a tenant, an organization and an IAM provider; return (tenant_id, provider_id)."""
    uniq = uuid4().hex[:8]
    tid = db.tenants.insert(
        name=f"{label}-{uniq}", slug=f"{label}-{uniq}", is_active=True
    )
    now = datetime.now(UTC)
    org = db.organizations.insert(
        name=f"{label}-org-{uniq}", tenant_id=tid, created_at=now, updated_at=now
    )
    db.commit()
    provider = IAMService(db).create_provider(
        tid,
        f"{label}-iam-{uniq}",
        "aws_iam",
        {"region": "us-east-1"},
        organization_id=org,
    )
    return tid, provider["id"]


@pytest_asyncio.fixture
async def two_tenants(app):
    """Create providers in two separate tenants and remove them afterwards."""
    async with app.app_context():
        db = current_app.db
        tenant_a, provider_a = _tenant_with_provider(db, "iam-a")
        tenant_b, provider_b = _tenant_with_provider(db, "iam-b")
    yield tenant_a, provider_a, tenant_b, provider_b
    async with app.app_context():
        db = current_app.db
        db(db.iam_providers.id.belongs([provider_a, provider_b])).delete()
        db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,suffix",
    [
        ("get", ""),
        ("put", ""),
        ("delete", ""),
        ("post", "/test"),
        ("post", "/sync"),
        ("get", "/users"),
        ("get", "/roles"),
        ("get", "/policies"),
        ("get", "/groups"),
        ("get", "/users/someone/access-keys"),
    ],
)
async def test_foreign_tenant_provider_is_not_found(
    method, suffix, app, async_client, generate_token, two_tenants
):
    """Tenant A gets 404 for every route addressing tenant B's provider."""
    tenant_a, _, _, provider_b = two_tenants
    token = generate_token(tenant_id=tenant_a, scopes=SCOPES)
    with patch(
        "apps.api.auth.decorators.get_current_user", return_value=_admin_user(tenant_a)
    ):
        kwargs = {"headers": {"Authorization": f"Bearer {token}"}}
        if method == "put":
            kwargs["json"] = {"name": "hijacked"}
        resp = await getattr(async_client, method)(
            f"/api/v1/iam/providers/{provider_b}{suffix}", **kwargs
        )
    assert resp.status_code == 404, (await resp.get_data()).decode()[:200]

    async with app.app_context():
        row = (
            current_app.db(current_app.db.iam_providers.id == provider_b)
            .select()
            .first()
        )
        assert row is not None and not row.name.startswith("hijacked")


@pytest.mark.asyncio
async def test_list_and_get_only_show_own_tenant(
    app, async_client, generate_token, two_tenants
):
    """Listing returns only the caller tenant's providers; own provider is readable."""
    tenant_a, provider_a, _, provider_b = two_tenants
    token = generate_token(tenant_id=tenant_a, scopes=SCOPES)
    headers = {"Authorization": f"Bearer {token}"}
    with patch(
        "apps.api.auth.decorators.get_current_user", return_value=_admin_user(tenant_a)
    ):
        listed = await async_client.get("/api/v1/iam/providers", headers=headers)
        own = await async_client.get(
            f"/api/v1/iam/providers/{provider_a}", headers=headers
        )

    assert listed.status_code == 200
    ids = {p["id"] for p in (await listed.get_json())["providers"]}
    assert ids == {provider_a}
    assert provider_b not in ids
    assert own.status_code == 200
    assert (await own.get_json())["id"] == provider_a


@pytest.mark.asyncio
async def test_create_in_foreign_organization_is_not_found(
    app, async_client, generate_token, two_tenants
):
    """Creating a provider under another tenant's organization returns 404."""
    tenant_a, _, _, provider_b = two_tenants
    async with app.app_context():
        db = current_app.db
        foreign_org = (
            db(db.iam_providers.id == provider_b).select().first().organization_id
        )
    token = generate_token(tenant_id=tenant_a, scopes=SCOPES)
    with patch(
        "apps.api.auth.decorators.get_current_user", return_value=_admin_user(tenant_a)
    ):
        resp = await async_client.post(
            "/api/v1/iam/providers",
            json={
                "name": "intruder",
                "provider_type": "aws_iam",
                "config": {},
                "organization_id": foreign_org,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404, (await resp.get_data()).decode()[:200]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["/api/v1/iam/providers", "/api/v1/iam/providers/{pid}"]
)
async def test_missing_tenant_claim_is_forbidden(
    path, app, async_client, generate_token, two_tenants
):
    """A verified token without a tenant claim is refused with 403."""
    _, provider_a, _, _ = two_tenants
    token = generate_token(tenant_id="", scopes=SCOPES)
    with patch(
        "apps.api.auth.decorators.get_current_user", return_value=_admin_user(None)
    ):
        resp = await async_client.get(
            path.format(pid=provider_a), headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 403, (await resp.get_data()).decode()[:200]
