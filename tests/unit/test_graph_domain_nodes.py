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

    @pytest.mark.asyncio
    async def test_cross_tenant_isolation_no_query_param_bypass(self, app):
        """Verify cross-tenant inventory leak is fixed: query param cannot bypass isolation.

        regression: gh-189
        """
        db = app.db

        def _seed():
            now = datetime.now(timezone.utc)

            # Tenant A
            tenant_a_id = db.tenants.insert(
                name="Tenant A",
                slug=f"tenant-a-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            email_a = f"user-a-{uuid.uuid4().hex[:8]}@test.local"
            identity_a_id = db.identities.insert(
                tenant_id=tenant_a_id,
                username=email_a,
                email=email_a,
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

            # Tenant B
            tenant_b_id = db.tenants.insert(
                name="Tenant B",
                slug=f"tenant-b-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            email_b = f"user-b-{uuid.uuid4().hex[:8]}@test.local"
            identity_b_id = db.identities.insert(
                tenant_id=tenant_b_id,
                username=email_b,
                email=email_b,
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

            # Org in Tenant A
            org_a_id = db.organizations.insert(name="Org A", tenant_id=tenant_a_id)

            # Org in Tenant B
            org_b_id = db.organizations.insert(name="Org B", tenant_id=tenant_b_id)

            # Entities have no tenant_id column — they are scoped via
            # organization_id (whose organization carries the tenant).
            entity_a_id = db.entities.insert(
                name="Entity A",
                type="compute",
                organization_id=org_a_id,
                external_id="ea-1",
            )

            entity_b_id = db.entities.insert(
                name="Entity B",
                type="compute",
                organization_id=org_b_id,
                external_id="eb-1",
            )

            # Service in Tenant A
            svc_a_id = db.services.insert(
                name="Service A",
                organization_id=org_a_id,
                tenant_id=tenant_a_id,
                is_public=False,
                external_id="sa-1",
            )

            # Service in Tenant B
            svc_b_id = db.services.insert(
                name="Service B",
                organization_id=org_b_id,
                tenant_id=tenant_b_id,
                is_public=False,
                external_id="sb-1",
            )

            # Dependency in Tenant A
            db.dependencies.insert(
                source_type="entity",
                source_id=entity_a_id,
                target_type="service",
                target_id=svc_a_id,
                tenant_id=tenant_a_id,
                dependency_type="uses",
            )

            # Dependency in Tenant B
            db.dependencies.insert(
                source_type="entity",
                source_id=entity_b_id,
                target_type="service",
                target_id=svc_b_id,
                tenant_id=tenant_b_id,
                dependency_type="uses",
            )

            db.commit()

            return {
                "tenant_a_id": tenant_a_id,
                "tenant_b_id": tenant_b_id,
                "identity_a_id": identity_a_id,
                "identity_b_id": identity_b_id,
                "org_a_id": org_a_id,
                "org_b_id": org_b_id,
                "entity_a_id": entity_a_id,
                "entity_b_id": entity_b_id,
            }

        fixtures = await run_in_threadpool(_seed)

        client = app.test_client()

        # User A token
        token_a = self._token(app, fixtures["tenant_a_id"], fixtures["identity_a_id"])

        # Test 1: User A can see their own tenant's resources
        response = await client.get(
            "/api/v1/graph/map",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert response.status_code == 200
        body = await response.get_json()
        entity_names = {n["label"] for n in body["nodes"]}
        assert "Entity A" in entity_names, "User A should see Entity A"
        assert "Entity B" not in entity_names, "User A should NOT see Entity B"

        # Test 2: User A cannot bypass isolation with ?tenant_id=<B> query param
        tenant_b_id = fixtures["tenant_b_id"]
        response = await client.get(
            f"/api/v1/graph/map?tenant_id={tenant_b_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert response.status_code == 200
        body = await response.get_json()
        entity_names = {n["label"] for n in body["nodes"]}
        # The query param MUST be ignored; User A still sees only their tenant
        assert "Entity A" in entity_names, "User A should still see Entity A"
        assert (
            "Entity B" not in entity_names
        ), "User A still cannot see Entity B (query param ignored)"

        # Test 3: no tenant-B resource of any kind leaks, and every rendered
        # edge connects only rendered (in-tenant) nodes.
        all_labels = {n["label"] for n in body["nodes"]}
        assert "Service B" not in all_labels, "Tenant B service must not render"
        node_ids = {n["id"] for n in body["nodes"]}
        for edge in body["edges"]:
            assert (
                edge["from"] in node_ids and edge["to"] in node_ids
            ), "edge must only connect rendered (in-tenant) nodes"

    @pytest.mark.asyncio
    async def test_org_id_param_cannot_cross_tenant(self, app):
        """The organization_id query param must not cross tenants either.

        entities/networking_resources/projects/milestones/issues are scoped by
        organization_id (no tenant_id column), so passing another tenant's
        org id must be re-validated against the caller's tenant, not trusted.

        regression: gh-189
        """
        db = app.db

        def _seed():
            now = datetime.now(timezone.utc)
            tenant_a = db.tenants.insert(
                name="Tenant A",
                slug=f"tenant-a-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            email_a = f"user-a-{uuid.uuid4().hex[:8]}@test.local"
            identity_a = db.identities.insert(
                tenant_id=tenant_a,
                username=email_a,
                email=email_a,
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
            tenant_b = db.tenants.insert(
                name="Tenant B",
                slug=f"tenant-b-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            org_b = db.organizations.insert(name="Org B", tenant_id=tenant_b)
            # Tenant-B resources across every org-scoped table (no tenant_id
            # column — scoped only by organization_id). None may leak.
            db.entities.insert(
                name="Secret Entity B",
                type="compute",
                organization_id=org_b,
                external_id="secret-eb",
            )
            db.projects.insert(
                name="Secret Project B",
                organization_id=org_b,
                status="active",
            )
            db.milestones.insert(
                title="Secret Milestone B",
                organization_id=org_b,
                status="open",
            )
            db.issues.insert(
                title="Secret Issue B",
                organization_id=org_b,
                issue_type="OTHER",
                priority="LOW",
                status="OPEN",
                is_incident=0,
                resource_type="entity",
                resource_id=1,
            )
            db.commit()
            return {
                "tenant_a": tenant_a,
                "identity_a": identity_a,
                "org_b": org_b,
            }

        fixtures = await run_in_threadpool(_seed)
        token_a = self._token(app, fixtures["tenant_a"], fixtures["identity_a"])
        secret_labels = {
            "Secret Entity B",
            "Secret Project B",
            "Secret Milestone B",
            "Secret Issue B",
        }
        client = app.test_client()

        # Caller's tenant owns ZERO orgs. Both the default (no-param) request and
        # an ?organization_id=<tenant B org> request must fail closed for every
        # org-scoped table, not return tenant B's rows.
        for url in (
            "/api/v1/graph/map",
            f"/api/v1/graph/map?organization_id={fixtures['org_b']}",
        ):
            response = await client.get(
                url, headers={"Authorization": f"Bearer {token_a}"}
            )
            assert response.status_code == 200, url
            body = await response.get_json()
            labels = {n["label"] for n in body["nodes"]}
            leaked = secret_labels & labels
            assert not leaked, f"tenant B rows leaked via {url}: {leaked}"
