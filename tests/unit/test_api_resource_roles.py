"""End-to-end create -> query tests for resource_roles (gh-284).

The ``resource_roles`` Postgres enums store UPPERCASE member names
(``resourcetype`` -> ENTITY/ORGANIZATION, ``resourceroletype`` ->
MAINTAINER/OPERATOR/VIEWER; see alembic/versions/001_add_enterprise_features.py),
but the route previously inserted/queried the lowercase API-contract value and
never set the NOT-NULL ``role_type`` column at all -- so a grant 500'd and a
lookup never matched. These tests pin the fixed casing: a POST persists
``role_type`` with the correct uppercase case, and a case-correct query
(filtered list + per-resource/per-identity lookups) matches the new row.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from quart import current_app


class _EnterpriseLicenseClient:
    """Mock license client resolving the Enterprise tier (unlocks access_reviews)."""

    def validate(self):
        class Validation:
            tier = "enterprise"

        return Validation()


def _enterprise(app) -> None:
    app.extensions["license_client"] = _EnterpriseLicenseClient()


def _org(db, tenant_id: int, name: str = "Org") -> int:
    now = datetime.now(UTC)
    org_id = db.organizations.insert(
        name=name, tenant_id=tenant_id, created_at=now, updated_at=now
    )
    db.commit()
    return org_id


def _entity(db, org_id: int, name: str = "Entity", entity_type: str = "compute") -> int:
    now = datetime.now(UTC)
    entity_id = db.entities.insert(
        name=name,
        type=entity_type,
        organization_id=org_id,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return entity_id


def _identity(db, tenant_id: int, username: str) -> int:
    identity_id = db.identities.insert(
        identity_type="human",
        username=username,
        email=f"{username}@example.com",
        tenant_id=tenant_id,
        auth_provider="local",
        is_active=True,
        is_superuser=False,
        mfa_enabled=False,
        must_change_password=False,
        portal_role="observer",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.commit()
    return identity_id


class TestCreateResourceRolePersistsCanonicalCase:
    """POST /api/v1/resource-roles -- regression: gh-284."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_persists_uppercase_enum_and_sets_role_type(
        self, mock_get_user, async_client, generate_token, app
    ):
        """A created role stores resource_type/role_type as uppercase enum names."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["access_reviews:write"])
        async with app.app_context():
            _enterprise(app)
            db = current_app.db
            org_id = _org(db, 1, f"Org Create {uuid4().hex[:8]}")
            entity_id = _entity(db, org_id, f"Entity Create {uuid4().hex[:8]}")
            grantee_id = _identity(db, 1, f"grantee_{uuid4().hex[:8]}")

        resp = await async_client.post(
            "/api/v1/resource-roles",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "identity_id": grantee_id,
                "resource_type": "entity",
                "resource_id": entity_id,
                "role": "operator",
            },
        )
        assert resp.status_code == 201, (await resp.get_data()).decode()[:300]

        body = await resp.get_json()
        # Response echoes the lowercase API contract.
        assert body["resource_type"] == "entity"
        assert body["role"] == "operator"
        role_id = body["id"]

        # The persisted row stores the canonical UPPERCASE enum member names,
        # and role_type (NOT NULL) is actually set.
        async with app.app_context():
            db = current_app.db
            row = db.resource_roles[role_id]
            assert row is not None
            assert str(row.resource_type) == "ENTITY"
            assert str(row.role_type) == "OPERATOR"
            assert str(row.role) == "operator"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_then_case_correct_query_matches(
        self, mock_get_user, async_client, generate_token, app
    ):
        """After a grant, the filtered list + per-resource/identity lookups match."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        write_token = generate_token(tenant_id=1, scopes=["access_reviews:write"])
        read_token = generate_token(tenant_id=1, scopes=["access_reviews:read"])
        async with app.app_context():
            _enterprise(app)
            db = current_app.db
            org_id = _org(db, 1, f"Org Query {uuid4().hex[:8]}")
            entity_id = _entity(db, org_id, f"Entity Query {uuid4().hex[:8]}")
            grantee_id = _identity(db, 1, f"grantee_q_{uuid4().hex[:8]}")

        create = await async_client.post(
            "/api/v1/resource-roles",
            headers={"Authorization": f"Bearer {write_token}"},
            json={
                "identity_id": grantee_id,
                "resource_type": "entity",
                "resource_id": entity_id,
                "role": "maintainer",
            },
        )
        assert create.status_code == 201, (await create.get_data()).decode()[:300]
        role_id = (await create.get_json())["id"]

        async with app.app_context():
            _enterprise(app)

        # 1) Filtered list: lowercase query params must match the uppercase row.
        listing = await async_client.get(
            f"/api/v1/resource-roles?resource_type=entity&resource_id={entity_id}"
            "&role=maintainer",
            headers={"Authorization": f"Bearer {read_token}"},
        )
        assert listing.status_code == 200, (await listing.get_data()).decode()[:300]
        data = await listing.get_json()
        ids = {item["id"] for item in data["items"]}
        assert role_id in ids, data
        match = next(item for item in data["items"] if item["id"] == role_id)
        assert match["resource_type"] == "entity"
        assert match["role"] == "maintainer"

        async with app.app_context():
            _enterprise(app)

        # 2) Per-entity lookup matches and returns the lowercase contract.
        entity_roles = await async_client.get(
            f"/api/v1/resource-roles/entities/{entity_id}/roles",
            headers={"Authorization": f"Bearer {read_token}"},
        )
        assert entity_roles.status_code == 200, (
            await entity_roles.get_data()
        ).decode()[:300]
        e_items = (await entity_roles.get_json())["items"]
        assert role_id in {item["id"] for item in e_items}
        assert all(item["resource_type"] == "entity" for item in e_items)

        async with app.app_context():
            _enterprise(app)

        # 3) Per-identity lookup matches.
        identity_roles = await async_client.get(
            f"/api/v1/resource-roles/identities/{grantee_id}/resource-roles",
            headers={"Authorization": f"Bearer {read_token}"},
        )
        assert identity_roles.status_code == 200, (
            await identity_roles.get_data()
        ).decode()[:300]
        i_items = (await identity_roles.get_json())["items"]
        assert role_id in {item["id"] for item in i_items}
