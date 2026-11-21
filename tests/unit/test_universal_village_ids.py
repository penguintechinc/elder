"""Comprehensive tests for Universal Village IDs backend implementation.

Covers:
1. Reliable minting helper (`apps/api/utils/village_id.py`) with Redis + CSPRNG fallback
2. Complete cross-reference registry (`apps/api/common/refs/registry.py`) and exact SPA routes
3. Org-scoped (`entities`, `networking_resources`), `discovery_jobs`, string-tenant (`geo_*`),
   and tenantless-table resolution + cross-tenant 404 / fail-closed (`gh-237`) isolation
4. Dual-registered lookup endpoints (`GET /api/v1/id/<vid>` and `GET /id/<vid>`)
   including module-scoped read authorization (401/403/404/200)
5. Creation-path minting and GET serialization across resource types
6. Alembic migration `045_universal_village_ids.py` revision chain, uniqueness,
   idempotency, and NULL backfill behavior
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

import jwt
import pytest
import sqlalchemy as sa

from apps.api.common.refs.registry import get_registry, get_type, resolve_by_village_id
from apps.api.utils.village_id import mint_village_id, resolve_tenant_id_for_org
from shared.utils.village_id import VILLAGE_ID_RE, is_valid_village_id, parse_village_id


def _load_migration_module(filename: str):
    """Load an Alembic migration file by filename for inspection/execution."""
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ensure_tenant_and_org(db, tenant_id: int = 1) -> tuple[int, int]:
    """Ensure a tenant and an organization exist and return (tenant_id, org_id)."""
    tenant = db(db.tenants.id == tenant_id).select().first()
    if not tenant:
        tenant_id = db.tenants.insert(
            name=f"Tenant {tenant_id}",
            slug=f"vid-tenant-{uuid4().hex[:8]}",
            is_active=True,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.commit()
    now = datetime.now(UTC)
    org_id = db.organizations.insert(
        name=f"VID Org {uuid4().hex[:8]}",
        tenant_id=tenant_id,
        village_id=mint_village_id(tenant_id, None),
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return tenant_id, org_id


def _create_foreign_tenant_and_org(db) -> tuple[int, int]:
    """Create a distinct foreign tenant + organization for cross-tenant tests."""
    now = datetime.now(UTC)
    other_tid = db.tenants.insert(
        name=f"Other Tenant {uuid4().hex[:6]}",
        slug=f"vid-other-{uuid4().hex[:8]}",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    other_org = db.organizations.insert(
        name=f"Foreign Org {uuid4().hex[:6]}",
        tenant_id=other_tid,
        village_id=mint_village_id(other_tid, None),
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return other_tid, other_org


def _ensure_identity(db, tenant_id: int = 1) -> int:
    """Create a fresh identity in ``tenant_id`` and return its ID."""
    now = datetime.now(UTC)
    ident_id = db.identities.insert(
        username=f"vid-user-{uuid4().hex[:8]}",
        email=f"vid-{uuid4().hex[:8]}@example.test",
        identity_type="human",
        auth_provider="local",
        is_active=True,
        is_superuser=False,
        mfa_enabled=False,
        must_change_password=False,
        portal_role="admin",
        tenant_id=tenant_id,
        village_id=mint_village_id(tenant_id, None),
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return ident_id


# =============================================================================
# 1. Reliable minting helper tests
# =============================================================================


class TestMintVillageIdHelper:
    """Tests for apps.api.utils.village_id.mint_village_id."""

    def test_uses_redis_when_available(self):
        """mint_village_id delegates to Redis INCR when client is connected."""
        redis_mock = Mock()
        redis_mock.incr.return_value = 0x42

        vid = mint_village_id(7, redis_mock)

        assert vid == "00000007-0000000000000042"
        assert is_valid_village_id(vid)
        redis_mock.incr.assert_called_once_with("elder:vid:00000007")

    def test_falls_back_when_redis_is_none(self, caplog):
        """mint_village_id falls back cleanly to CSPRNG hex when Redis is None."""
        with caplog.at_level("WARNING"):
            vid = mint_village_id(42, None)

        assert vid is not None
        assert VILLAGE_ID_RE.match(vid)
        assert vid.startswith("0000002a-")
        assert parse_village_id(vid).tenant_id == 42
        assert any("falling back" in rec.message for rec in caplog.records)

    def test_falls_back_when_redis_raises(self, caplog):
        """mint_village_id never raises or returns None if Redis errors out."""
        broken_redis = Mock()
        broken_redis.incr.side_effect = ConnectionError("Redis connection refused")

        with caplog.at_level("WARNING"):
            vid = mint_village_id(15, broken_redis)

        assert vid is not None
        assert VILLAGE_ID_RE.match(vid)
        assert vid.startswith("0000000f-")
        assert any("falling back" in rec.message for rec in caplog.records)

    def test_handles_string_and_none_tenant_ids(self):
        """mint_village_id normalizes numeric strings and None safely."""
        vid_str = mint_village_id("255", None)
        assert VILLAGE_ID_RE.match(vid_str)
        assert vid_str.startswith("000000ff-")

        vid_none = mint_village_id(None, None)
        assert VILLAGE_ID_RE.match(vid_none)
        assert vid_none.startswith("00000001-")

        vid_default = mint_village_id("default", None)
        assert VILLAGE_ID_RE.match(vid_default)
        assert vid_default.startswith("00000001-")

    def test_fallback_produces_unique_ids(self):
        """Consecutive CSPRNG fallback calls produce distinct valid IDs."""
        ids = {mint_village_id(1, None) for _ in range(50)}
        assert len(ids) == 50
        assert all(VILLAGE_ID_RE.match(v) for v in ids)


# =============================================================================
# 2. Registry completeness & exact SPA URL patterns
# =============================================================================


class TestRegistryCompleteness:
    """Verify every required resource type is registered with exact SPA routes."""

    EXPECTED_URL_PATTERNS: dict[str, tuple[str, str]] = {
        "tenant": ("tenants", "/admin/tenants/{id}"),
        "organization": ("organizations", "/organizations/{id}"),
        "entity": ("entities", "/entities/{id}"),
        "data_store": ("data_stores", "/data-inventory?highlight={id}"),
        "document": ("doc_documents", "/documents/{id}"),
        "doc_collection": ("doc_collections", "/documents?collection={id}"),
        "page": ("pg_pages", "/pages/{id}"),
        "playbook": ("stream_playbooks", "/streams/{id}"),
        "stream_template": ("stream_templates", "/streams?highlight={id}"),
        "diagram": ("dg_diagrams", "/diagrams/{id}"),
        "dg_collection": ("dg_collections", "/diagrams?collection={id}"),
        "dg_template": ("dg_templates", "/diagrams?highlight={id}"),
        "flow": ("iceflows", "/flows/{id}"),
        "issue": ("issues", "/issues/{id}"),
        "project": ("projects", "/projects/{id}"),
        "milestone": ("milestones", "/milestones?highlight={id}"),
        "label": ("labels", "/labels?highlight={id}"),
        "issue_label": ("issue_labels", "/labels?highlight={id}"),
        "ticket": ("hd_tickets", "/helpdesk/tickets/{id}"),
        "helpdesk_team": ("hd_teams", "/helpdesk/teams?highlight={id}"),
        "ai_resource": ("ai_resources", "/ai?highlight={id}"),
        "software": ("software", "/software?highlight={id}"),
        "service": ("services", "/services?highlight={id}"),
        "vulnerability": ("vulnerabilities", "/vulnerabilities?highlight={id}"),
        "sbom_scan": ("sbom_scans", "/sbom?highlight={id}"),
        "sbom_component": ("sbom_components", "/sbom?highlight={id}"),
        "license_policy": (
            "license_policies",
            "/admin/license-policies?highlight={id}",
        ),
        "on_call_rotation": ("on_call_rotations", "/on-call-rotations?highlight={id}"),
        "ipam_prefix": ("ipam_prefixes", "/ipam?tab=prefixes&highlight={id}"),
        "ipam_address": ("ipam_addresses", "/ipam?tab=addresses&highlight={id}"),
        "ipam_vlan": ("ipam_vlans", "/ipam?tab=vlans&highlight={id}"),
        "networking_resource": ("networking_resources", "/networking?highlight={id}"),
        "dependency": ("dependencies", "/dependencies?highlight={id}"),
        "geo_region": ("geo_regions", "/map?region={id}"),
        "geo_location": ("geo_locations", "/map?location={id}"),
        "identity": ("identities", "/iam?highlight={id}"),
        "identity_group": ("identity_groups", "/iam?tab=groups&highlight={id}"),
        "secret": ("secrets", "/secrets?highlight={id}"),
        "builtin_secret": ("builtin_secrets", "/secrets?highlight={id}"),
        "crypto_key": ("crypto_keys", "/keys?highlight={id}"),
        "certificate": ("certificates", "/certificates?highlight={id}"),
        "webhook": ("webhooks", "/webhooks?highlight={id}"),
        "inbound_webhook": ("inbound_webhooks", "/webhooks?highlight={id}"),
        "backup": ("backups", "/backups?highlight={id}"),
        "access_review": ("access_reviews", "/access-reviews?highlight={id}"),
        "group_access_request": (
            "group_access_requests",
            "/access-reviews?highlight={id}",
        ),
        "discovery_job": ("discovery_jobs", "/admin/integrations?highlight={id}"),
        "sync_config": ("sync_configs", "/admin/integrations?highlight={id}"),
        "iam_provider": ("iam_providers", "/admin/integrations?highlight={id}"),
        "google_workspace_provider": (
            "google_workspace_providers",
            "/admin/integrations?highlight={id}",
        ),
        "cloud_account": ("cloud_accounts", "/admin/integrations?highlight={id}"),
    }

    def test_all_expected_types_registered_with_exact_routes(self):
        """Every top-level resource type is registered with its exact React Router URL."""
        registry = get_registry()
        for type_name, (
            expected_table,
            expected_url,
        ) in self.EXPECTED_URL_PATTERNS.items():
            rt = registry.get(type_name)
            assert rt is not None, f"Missing type in registry: {type_name}"
            table_msg = f"{type_name}: expected {expected_table}, got {rt.table}"
            assert rt.table == expected_table, table_msg
            url_msg = f"{type_name}: expected {expected_url}, got {rt.url_pattern}"
            assert rt.url_pattern == expected_url, url_msg

    def test_slug_based_types_use_slug_id_column(self):
        """Documents and pages resolve by slug for SPA routing."""
        assert get_type("document").id_column == "slug"
        assert get_type("page").id_column == "slug"


# =============================================================================
# 3. Org-scoped, discovery_jobs, geo, and cross-tenant resolution tests
# =============================================================================


class TestResolveByVillageIdScoping:
    """Verify resolve_by_village_id handles org-scoped tables and fails closed."""

    def test_org_scoped_entity_resolves_and_isolates_tenants(self, app):
        """Entities (organization_id, no tenant_id) resolve for owner, 404/None cross-tenant & None tenant."""
        db = app.db
        tid1, org1 = _ensure_tenant_and_org(db, tenant_id=1)
        tid2, org2 = _create_foreign_tenant_and_org(db)

        vid1 = mint_village_id(tid1, None)
        vid2 = mint_village_id(tid2, None)
        now = datetime.now(UTC)

        eid1 = db.entities.insert(
            name="prod-web-01",
            type="compute",
            organization_id=org1,
            village_id=vid1,
            created_at=now,
            updated_at=now,
        )
        db.entities.insert(
            name="foreign-db-01",
            type="compute",
            organization_id=org2,
            village_id=vid2,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Owner tenant resolves with title + module
        resolved = resolve_by_village_id(db, vid1, tenant_id=tid1)
        assert resolved == {
            "type": "entity",
            "id": eid1,
            "table_name": "entities",
            "title": "prod-web-01",
            "module": "infrastructure",
        }

        # Cross-tenant MUST return None
        assert resolve_by_village_id(db, vid2, tenant_id=tid1) is None
        assert resolve_by_village_id(db, vid1, tenant_id=tid2) is None

        # Fail-closed (regression: gh-237): tenant_id=None MUST return None
        assert resolve_by_village_id(db, vid1, tenant_id=None) is None

        # Helper sanity check
        assert resolve_tenant_id_for_org(db, org1) == tid1
        assert resolve_tenant_id_for_org(db, org2) == tid2

    def test_networking_resource_org_scoped_resolution(self, app):
        """networking_resources (org-scoped) resolves cleanly for owning tenant only."""
        db = app.db
        tid1, org1 = _ensure_tenant_and_org(db, tenant_id=1)
        tid2, _ = _create_foreign_tenant_and_org(db)

        vid = mint_village_id(tid1, None)
        net_id = db.networking_resources.insert(
            name="vpc-prod-us-east",
            network_type="vpc",
            organization_id=org1,
            village_id=vid,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.commit()

        resolved = resolve_by_village_id(db, vid, tenant_id=tid1)
        assert resolved is not None
        assert resolved["type"] == "networking_resource"
        assert resolved["id"] == net_id
        assert resolved["title"] == "vpc-prod-us-east"
        assert resolved["module"] == "infrastructure"

        assert resolve_by_village_id(db, vid, tenant_id=tid2) is None
        assert resolve_by_village_id(db, vid, tenant_id=None) is None

    def test_discovery_job_config_json_org_scoping(self, app):
        """discovery_jobs checks config_json['_organization_id'] against tenant orgs."""
        db = app.db
        tid1, org1 = _ensure_tenant_and_org(db, tenant_id=1)
        tid2, _ = _create_foreign_tenant_and_org(db)

        vid_owned = mint_village_id(tid1, None)
        vid_unscoped = mint_village_id(tid1, None)
        now = datetime.now(UTC)

        job_id = db.discovery_jobs.insert(
            name="aws-prod-scan",
            provider="aws",
            config_json={"_organization_id": org1, "region": "us-east-1"},
            schedule_interval=3600,
            enabled=True,
            village_id=vid_owned,
            created_at=now,
            updated_at=now,
        )
        db.discovery_jobs.insert(
            name="orphan-scan",
            provider="aws",
            config_json={"region": "us-west-2"},
            schedule_interval=3600,
            enabled=True,
            village_id=vid_unscoped,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        resolved = resolve_by_village_id(db, vid_owned, tenant_id=tid1)
        assert resolved is not None
        assert resolved["type"] == "discovery_job"
        assert resolved["id"] == job_id
        assert resolved["title"] == "aws-prod-scan"
        assert resolved["module"] == "discovery"

        # Foreign tenant cannot resolve
        assert resolve_by_village_id(db, vid_owned, tenant_id=tid2) is None
        # Missing _organization_id fails closed
        assert resolve_by_village_id(db, vid_unscoped, tenant_id=tid1) is None

    def test_geo_region_string_tenant_id_resolution(self, app):
        """geo_regions stores tenant_id as VARCHAR and resolves cleanly without SQL type errors."""
        db = app.db
        tid1, _ = _ensure_tenant_and_org(db, tenant_id=1)
        tid2, _ = _create_foreign_tenant_and_org(db)

        vid = mint_village_id(tid1, None)
        now = datetime.now(UTC)
        region_id = db.geo_regions.insert(
            tenant_id=str(tid1),
            code=f"us-dal-{uuid4().hex[:6]}",
            name="Dallas Region",
            is_active=True,
            village_id=vid,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        resolved = resolve_by_village_id(db, vid, tenant_id=tid1)
        assert resolved is not None
        assert resolved["type"] == "geo_region"
        assert resolved["id"] == region_id
        assert resolved["title"] == "Dallas Region"
        assert resolve_by_village_id(db, vid, tenant_id=tid2) is None


# =============================================================================
# 4. HTTP lookup endpoints (/api/v1/id/<vid> AND /id/<vid>)
# =============================================================================


class TestLookupVillageIdEndpoints:
    """Verify both /api/v1/id/<village_id> and legacy /id/<village_id> endpoints."""

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401_on_both_routes(self, async_client):
        """Both /api/v1/id/<vid> and /id/<vid> require authentication."""
        vid = "00000001-0000000000000001"
        r_v1 = await async_client.get(f"/api/v1/id/{vid}")
        r_legacy = await async_client.get(f"/id/{vid}")
        assert r_v1.status_code == 401
        assert r_legacy.status_code == 401

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_entity_resolves_on_v1_and_legacy_routes_with_full_payload(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Org-scoped entity resolves via /api/v1/id/<vid> and /id/<vid>."""
        db = app.db
        tid1, org1 = _ensure_tenant_and_org(db, tenant_id=1)
        vid = mint_village_id(tid1, None)
        now = datetime.now(UTC)
        entity_id = db.entities.insert(
            name="k8s-cluster-alpha",
            type="kubernetes",
            organization_id=org1,
            village_id=vid,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        mock_get_user.return_value = MagicMock(id=1, tenant_id=tid1, is_superuser=False)
        token = generate_token(tenant_id=tid1, scopes=["infrastructure:read"])

        for path in (f"/api/v1/id/{vid}", f"/id/{vid}"):
            resp = await async_client.get(
                path, headers={"Authorization": f"Bearer {token}"}
            )
            assert resp.status_code == 200, (await resp.get_data()).decode()
            body = await resp.get_json()
            assert body == {
                "village_id": vid,
                "resource_type": "entity",
                "resource_id": entity_id,
                "title": "k8s-cluster-alpha",
                "module": "infrastructure",
                "redirect_url": f"/entities/{entity_id}",
                "short_link": f"/id/{vid}",
            }

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_cross_tenant_returns_404_and_missing_scope_returns_403(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Cross-tenant IDs 404; own-tenant IDs without module read scope 403; module scope 200."""
        db = app.db
        tid1, _ = _ensure_tenant_and_org(db, tenant_id=1)
        tid2, org2 = _create_foreign_tenant_and_org(db)
        ident_id = _ensure_identity(db, tenant_id=tid1)

        # Own-tenant document
        doc_vid = mint_village_id(tid1, None)
        slug = f"runbook-{uuid4().hex[:8]}"
        now = datetime.now(UTC)
        db.doc_documents.insert(
            tenant_id=tid1,
            village_id=doc_vid,
            title="Incident Runbook",
            slug=slug,
            body_html="<p>Steps</p>",
            author_identity_id=ident_id,
            status="published",
            created_at=now,
            updated_at=now,
        )
        # Foreign-tenant entity
        foreign_vid = mint_village_id(tid2, None)
        db.entities.insert(
            name="secret-foreign-host",
            type="compute",
            organization_id=org2,
            village_id=foreign_vid,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        mock_get_user.return_value = MagicMock(
            id=ident_id, tenant_id=tid1, is_superuser=False
        )

        # Cross-tenant entity -> 404
        tok_infra = generate_token(tenant_id=tid1, scopes=["infrastructure:read"])
        resp_foreign = await async_client.get(
            f"/api/v1/id/{foreign_vid}",
            headers={"Authorization": f"Bearer {tok_infra}"},
        )
        assert resp_foreign.status_code == 404

        # Own-tenant doc without read scope -> 403
        tok_no_scope = generate_token(tenant_id=tid1, scopes=["unrelated:read"])
        resp_denied = await async_client.get(
            f"/api/v1/id/{doc_vid}",
            headers={"Authorization": f"Bearer {tok_no_scope}"},
        )
        assert resp_denied.status_code == 403

        # Own-tenant doc with documents:read -> 200
        tok_docs = generate_token(tenant_id=tid1, scopes=["documents:read"])
        resp_ok = await async_client.get(
            f"/api/v1/id/{doc_vid}",
            headers={"Authorization": f"Bearer {tok_docs}"},
        )
        assert resp_ok.status_code == 200, (await resp_ok.get_data()).decode()
        body = await resp_ok.get_json()
        assert body["resource_type"] == "document"
        assert body["resource_id"] == slug
        assert body["redirect_url"] == f"/documents/{slug}"
        assert body["short_link"] == f"/id/{doc_vid}"


# =============================================================================
# 5. Creation-path minting & serialization across resource types
# =============================================================================


class TestCreationMintingAndSerialization:
    """Verify creation endpoints mint valid village_ids and GET responses expose them."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_creation_endpoints_mint_and_return_village_id(
        self, mock_get_user, async_client, app
    ):
        """Entities, data stores, playbooks, documents, pages, milestones, issues, diagrams,
        geo regions, labels, and builtin secrets all mint and return valid village_ids."""
        db = app.db
        tid1, org1 = _ensure_tenant_and_org(db, tenant_id=1)
        ident_id = _ensure_identity(db, tenant_id=tid1)

        # Force fallback path too (redis_client = None) to verify resilience!
        orig_redis = getattr(app, "redis_client", None)
        app.redis_client = None
        try:
            mock_user = MagicMock(
                id=ident_id,
                tenant_id=tid1,
                is_superuser=True,
                portal_role="admin",
                username="vid-admin",
            )
            mock_get_user.return_value = mock_user
            secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
            algorithm = app.config.get("JWT_ALGORITHM", "HS256")
            now_jwt = datetime.now(UTC)
            token = jwt.encode(
                {
                    "sub": str(ident_id),
                    "tenant": str(tid1),
                    "iat": now_jwt,
                    "exp": now_jwt + timedelta(hours=1),
                    "scope": [
                        "*:read",
                        "*:write",
                        "infrastructure:read",
                        "infrastructure:write",
                        "issues:read",
                        "issues:write",
                        "documents:read",
                        "documents:write",
                        "pages:read",
                        "pages:write",
                        "diagrams:read",
                        "diagrams:write",
                        "streams:read",
                        "streams:write",
                        "secrets:read",
                        "secrets:write",
                        "sbom:read",
                        "sbom:write",
                        "ipam:read",
                        "ipam:write",
                    ],
                    "roles": ["admin"],
                },
                secret,
                algorithm=algorithm,
            )
            headers = {"Authorization": f"Bearer {token}"}

            # 1. Entity
            r = await async_client.post(
                "/api/v1/entities",
                json={
                    "name": f"srv-{uuid4().hex[:6]}",
                    "entity_type": "compute",
                    "organization_id": org1,
                },
                headers=headers,
            )
            assert r.status_code == 201, (await r.get_data()).decode()
            ent = await r.get_json()
            assert is_valid_village_id(ent.get("village_id"))

            # Verify entity GET detail includes village_id and /api/v1/id/<vid> resolves it
            r_get = await async_client.get(
                f"/api/v1/entities/{ent['id']}", headers=headers
            )
            assert r_get.status_code == 200
            assert (await r_get.get_json()).get("village_id") == ent["village_id"]

            r_lookup = await async_client.get(
                f"/api/v1/id/{ent['village_id']}", headers=headers
            )
            assert r_lookup.status_code == 200
            assert (await r_lookup.get_json())[
                "redirect_url"
            ] == f"/entities/{ent['id']}"

            # 2. Data store (uses @validate_response(DataStoreResponse))
            r = await async_client.post(
                "/api/v1/data-stores",
                json={
                    "name": f"pg-main-{uuid4().hex[:6]}",
                    "organization_id": org1,
                    "storage_type": "database",
                },
                headers=headers,
            )
            assert r.status_code == 201, (await r.get_data()).decode()
            ds = await r.get_json()
            assert is_valid_village_id(ds.get("village_id"))

            r_lookup_ds = await async_client.get(
                f"/api/v1/id/{ds['village_id']}", headers=headers
            )
            assert r_lookup_ds.status_code == 200
            assert (await r_lookup_ds.get_json())["redirect_url"] == (
                f"/data-inventory?highlight={ds['id']}"
            )

            # 3. Project & Milestone & Label & Issue
            r_proj = await async_client.post(
                "/api/v1/projects",
                json={"name": f"Proj-{uuid4().hex[:6]}", "organization_id": org1},
                headers=headers,
            )
            assert r_proj.status_code == 201, (await r_proj.get_data()).decode()
            proj = await r_proj.get_json()
            assert is_valid_village_id(proj.get("village_id"))

            r_ms = await async_client.post(
                "/api/v1/milestones",
                json={
                    "title": f"v4.1-{uuid4().hex[:6]}",
                    "organization_id": org1,
                    "project_id": proj["id"],
                },
                headers=headers,
            )
            assert r_ms.status_code == 201, (await r_ms.get_data()).decode()
            ms = await r_ms.get_json()
            assert is_valid_village_id(ms.get("village_id"))

            r_lbl = await async_client.post(
                "/api/v1/labels",
                json={"name": f"lbl-{uuid4().hex[:6]}", "color": "#22aa88"},
                headers=headers,
            )
            assert r_lbl.status_code == 201, (await r_lbl.get_data()).decode()
            lbl = await r_lbl.get_json()
            assert is_valid_village_id(lbl.get("village_id"))

            r_iss = await async_client.post(
                "/api/v1/issues",
                json={
                    "title": f"Fix vid {uuid4().hex[:6]}",
                    "organization_id": org1,
                },
                headers=headers,
            )
            assert r_iss.status_code == 201, (await r_iss.get_data()).decode()
            iss = await r_iss.get_json()
            assert is_valid_village_id(iss.get("village_id"))

            # 4. Document, Page, Diagram, Playbook (all work even with redis_client=None!)
            r_doc = await async_client.post(
                "/api/v1/documents",
                json={"title": f"Doc {uuid4().hex[:6]}", "body": "# Hello"},
                headers=headers,
            )
            assert r_doc.status_code == 201, (await r_doc.get_data()).decode()
            doc = await r_doc.get_json()
            assert is_valid_village_id(doc.get("village_id"))

            r_page = await async_client.post(
                "/api/v1/pages",
                json={"title": f"Page {uuid4().hex[:6]}", "body_html": "<p>Body</p>"},
                headers=headers,
            )
            assert r_page.status_code == 201, (await r_page.get_data()).decode()
            page = await r_page.get_json()
            assert is_valid_village_id(page.get("village_id"))

            r_diag = await async_client.post(
                "/api/v1/diagrams",
                json={"title": f"Arch {uuid4().hex[:6]}"},
                headers=headers,
            )
            assert r_diag.status_code == 201, (await r_diag.get_data()).decode()
            diag = await r_diag.get_json()
            assert is_valid_village_id(diag.get("village_id"))

            r_stream = await async_client.post(
                "/api/v1/streams",
                json={"name": f"Playbook {uuid4().hex[:6]}"},
                headers=headers,
            )
            assert r_stream.status_code == 201, (await r_stream.get_data()).decode()
            stream = await r_stream.get_json()
            assert is_valid_village_id(stream.get("village_id"))

            # 5. Geo region
            r_geo = await async_client.post(
                "/api/v1/geo/regions",
                json={
                    "code": f"us-tx-{uuid4().hex[:6]}",
                    "name": "Texas Core",
                    "latitude": 32.78,
                    "longitude": -96.80,
                },
                headers=headers,
            )
            assert r_geo.status_code == 201, (await r_geo.get_data()).decode()
            geo = await r_geo.get_json()
            assert is_valid_village_id(geo.get("village_id"))

            # 6. Builtin secret (@validate_response(SecretMetadataResponse))
            r_sec = await async_client.post(
                "/api/v1/builtin-secrets",
                json={
                    "name": f"db-pass-{uuid4().hex[:6]}",
                    "value": "s3cr3t-test-val",
                    "organization_id": org1,
                },
                headers=headers,
            )
            assert r_sec.status_code == 201, (await r_sec.get_data()).decode()
            sec = await r_sec.get_json()
            assert is_valid_village_id(sec.get("village_id"))

            # 7. IPAM Prefix
            octet = (uuid4().int % 200) + 10
            r_ip = await async_client.post(
                "/api/v1/ipam/prefixes",
                json={
                    "prefix": f"10.{octet}.0.0/24",
                    "organization_id": org1,
                },
                headers=headers,
            )
            assert r_ip.status_code == 201, (await r_ip.get_data()).decode()
            ipam = await r_ip.get_json()
            assert is_valid_village_id(ipam.get("village_id"))

            # 8. Software (@validate_response(SoftwareDTO))
            r_sw = await async_client.post(
                "/api/v1/software",
                json={
                    "name": f"nginx-{uuid4().hex[:6]}",
                    "organization_id": org1,
                    "software_type": "open_source",
                },
                headers=headers,
            )
            assert r_sw.status_code == 201, (await r_sw.get_data()).decode()
            sw = await r_sw.get_json()
            assert is_valid_village_id(sw.get("village_id"))
        finally:
            app.redis_client = orig_redis


# =============================================================================
# 6. Migration 045 validity, revision chain, idempotency, and backfill tests
# =============================================================================


class TestMigration045UniversalVillageIds:
    """Validate alembic/versions/045_universal_village_ids.py."""

    def test_revision_chain_matches_head_044(self):
        """Migration 045 must revise 044 (matching 044_inbound_webhook_scopes.py)."""
        m044 = _load_migration_module("044_inbound_webhook_scopes.py")
        m045 = _load_migration_module("045_universal_village_ids.py")

        assert m045.revision == "045"
        assert m045.down_revision == m044.revision

    def test_trackable_tables_have_unique_table_codes_and_cover_all_new_tables(self):
        """Every table has a distinct table_code and all 22+ newly-added tables are included."""
        m045 = _load_migration_module("045_universal_village_ids.py")

        names = [name for name, _ in m045.TRACKABLE_TABLES]
        codes = [code for _, code in m045.TRACKABLE_TABLES]

        assert len(names) == len(set(names)), "Duplicate table names"
        assert len(codes) == len(set(codes)), "Duplicate table_codes"
        assert all(code >= 1 for code in codes)
        assert set(m045.NEW_VILLAGE_ID_TABLES).issubset(set(names))

        required_new = {
            "networking_resources",
            "geo_regions",
            "geo_locations",
            "secrets",
            "builtin_secrets",
            "secret_providers",
            "api_keys",
            "crypto_keys",
            "key_providers",
            "backups",
            "backup_jobs",
            "identity_groups",
            "portal_users",
            "labels",
            "cloud_accounts",
            "discovery_jobs",
            "sync_configs",
            "iam_providers",
            "google_workspace_providers",
            "alert_configurations",
            "notification_rules",
            "hd_sla_policies",
        }
        assert required_new.issubset(set(m045.NEW_VILLAGE_ID_TABLES))

    def test_upgrade_is_idempotent_and_backfills_null_rows(
        self, app, test_database_url
    ):
        """Running 045.upgrade() twice succeeds, backfills NULLs with valid 25-char hex,
        and preserves existing non-null village_ids."""
        if not test_database_url:
            pytest.skip("Requires PostgreSQL test database")

        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        m045 = _load_migration_module("045_universal_village_ids.py")
        db = app.db
        tid1, org1 = _ensure_tenant_and_org(db, tenant_id=1)

        existing_vid = mint_village_id(tid1, None)
        now = datetime.now(UTC)

        # Insert an org-scoped entity WITH village_id=None and one with an existing village_id
        null_entity_id = db.entities.insert(
            name=f"legacy-null-{uuid4().hex[:6]}",
            type="compute",
            organization_id=org1,
            village_id=None,
            created_at=now,
            updated_at=now,
        )
        preserved_entity_id = db.entities.insert(
            name=f"legacy-existing-{uuid4().hex[:6]}",
            type="compute",
            organization_id=org1,
            village_id=existing_vid,
            created_at=now,
            updated_at=now,
        )
        # Also insert a networking_resource with village_id=None
        null_net_id = db.networking_resources.insert(
            name=f"legacy-net-{uuid4().hex[:6]}",
            network_type="subnet",
            organization_id=org1,
            village_id=None,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        engine = sa.create_engine(test_database_url)
        try:
            with engine.begin() as conn:
                ctx = MigrationContext.configure(conn)
                with Operations.context(ctx):
                    m045.upgrade()
                    # Second invocation MUST be completely idempotent
                    m045.upgrade()
        finally:
            engine.dispose()

        # Verify NULL rows were backfilled with valid collision-free 25-char lowercase hex
        backfilled_ent = db(db.entities.id == null_entity_id).select().first()
        preserved_ent = db(db.entities.id == preserved_entity_id).select().first()
        backfilled_net = db(db.networking_resources.id == null_net_id).select().first()

        assert backfilled_ent is not None
        assert is_valid_village_id(backfilled_ent.village_id)
        assert parse_village_id(backfilled_ent.village_id).tenant_id == tid1

        assert preserved_ent is not None
        assert preserved_ent.village_id == existing_vid

        assert backfilled_net is not None
        assert is_valid_village_id(backfilled_net.village_id)
        assert parse_village_id(backfilled_net.village_id).tenant_id == tid1
        assert backfilled_net.village_id != backfilled_ent.village_id
