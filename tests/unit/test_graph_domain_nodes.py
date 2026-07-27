"""Graph domain-node rendering tests.

The global graph/map allowlist previously excluded cloud domain tables
(networking_resource, data_store, service, software), so `dependencies`
rows pointing at those types were silently dropped by the `add_edge`
guard in `get_map_data()` (neither endpoint node existed, so the edge
never rendered). These tests prove the map materializes nodes for the
domain tables so cloud-discovered edges actually show up in the UI.

regression: cloud-scan-linkage-pr1-core task 6
"""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
import pytest_asyncio

from apps.api.utils.async_utils import run_in_threadpool


@pytest.mark.integration
class TestGraphDomainNodes:
    """The global map must render domain-table nodes so cloud edges show."""

    @pytest_asyncio.fixture(autouse=True)
    async def setup_db(self, app, test_database_url):
        """Seed a tenant + identity for authenticating against the map endpoint."""
        if not test_database_url:
            pytest.skip("DATABASE_URL not set")

        db = app.db

        def _setup():
            now = datetime.now(timezone.utc)
            tenant_id = db.tenants.insert(
                name="Graph Tenant",
                slug=f"graph-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            email = f"graph-user-{uuid.uuid4().hex[:8]}@test.local"
            identity_id = db.identities.insert(
                tenant_id=tenant_id,
                username=email,
                email=email,
                identity_type="human",
                auth_provider="local",
                is_active=True,
                is_superuser=False,
                mfa_enabled=False,
                must_change_password=False,
                portal_role="viewer",
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return {"tenant_id": tenant_id, "identity_id": identity_id}

        self.fixtures = await run_in_threadpool(_setup)

    def _token(self, app, tenant_id, identity_id, scopes=None):
        """Create a test JWT with the given tenant claim and scopes."""
        scopes = scopes or ["infrastructure:read"]
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(identity_id),
            "iat": now,
            "exp": now + timedelta(hours=1),
            "scope": scopes,
            "tenant": str(tenant_id),
            "identity_id": identity_id,
            "roles": ["admin"],
        }
        secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
        return jwt.encode(payload, secret, algorithm="HS256")

    @pytest.mark.asyncio
    async def test_networking_resource_node_and_edge_render(self, app):
        """A dependency edge from an entity to a networking_resource must
        render both endpoint nodes and the edge itself."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, tenant_id, identity_id)
        db = app.db

        def _seed():
            org_id = db.organizations.insert(name="Org", tenant_id=tenant_id)
            entity_id = db.entities.insert(
                name="web",
                type="compute",
                organization_id=org_id,
                external_id="i-1",
            )
            nr_id = db.networking_resources.insert(
                name="vpc-1",
                network_type="other",
                organization_id=org_id,
                external_id="vpc-1",
            )
            db.dependencies.insert(
                source_type="entity",
                source_id=entity_id,
                target_type="networking_resource",
                target_id=nr_id,
                dependency_type="in_network",
            )
            db.commit()

        await run_in_threadpool(_seed)

        client = app.test_client()
        response = await client.get(
            "/api/v1/graph/map",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        body = await response.get_json()

        node_types = {n["resource_type"] for n in body["nodes"]}
        assert "networking_resource" in node_types

        edge_types = {e["type"] for e in body["edges"]}
        assert "in_network" in edge_types
