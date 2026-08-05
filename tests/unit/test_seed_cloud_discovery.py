"""Tests for scripts/seed_cloud_discovery.py's canned discovery payload.

Loads build_discovery_results() directly from the script (scripts/ is not a
package, so it's loaded by file path rather than imported as
scripts.seed_cloud_discovery) and feeds it through the REAL production
linker (DiscoveryService._store_discovered_resources) against the test DB —
the same assertion style as tests/unit/test_discovery_linker.py — to prove
the demo topology resolves every declared edge with zero unresolved_edges.
"""

import importlib.util
import uuid
from pathlib import Path
from typing import Any

import pytest

from apps.worker.discovery.service import DiscoveryService

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "seed_cloud_discovery.py"
)


def _load_seed_script() -> Any:
    """Load scripts/seed_cloud_discovery.py by file path (not a package)."""
    spec = importlib.util.spec_from_file_location("seed_cloud_discovery", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed_cloud_discovery = _load_seed_script()


@pytest.fixture
def seeded(app):
    """A DiscoveryService on the test DAL with one tenant + organization.

    Per-invocation unique tenant slug (same pattern as
    tests/unit/test_discovery_linker.py's `seeded` fixture) since the test DB
    is session-persistent with no per-test rollback.
    """
    db = app.db
    tenant_id = db.tenants.insert(
        name="Seed Script Test Tenant",
        slug=f"seed-cloud-discovery-test-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    org_id = db.organizations.insert(name="Seed Script Test Org", tenant_id=tenant_id)
    db.commit()
    yield DiscoveryService(db), db, org_id


def test_build_discovery_results_shape():
    """Sanity-check the canned payload's category keys and resource counts."""
    results = seed_cloud_discovery.build_discovery_results()

    assert set(results.keys()) == {
        "compute",
        "storage",
        "network",
        "database",
        "serverless",
        "iam",
        "resources_count",
        "discovery_time",
        "duration_seconds",
    }
    assert len(results["network"]) == 6  # vpc, 2 subnets, 2 sgs, 1 elb
    assert len(results["compute"]) == 2  # 2 ec2 instances
    assert len(results["storage"]) == 1  # 1 ebs volume
    assert len(results["database"]) == 1  # 1 rds instance
    assert len(results["serverless"]) == 1  # 1 lambda function
    assert len(results["iam"]) == 1  # 1 iam role
    assert results["resources_count"] == 12


def test_seed_topology_creates_expected_edges_with_zero_unresolved(seeded):
    """Feed the canned payload through the real linker: full edge coverage, 0 unresolved."""
    service, db, org_id = seeded
    results = seed_cloud_discovery.build_discovery_results()

    counts = service._store_discovered_resources(org_id, results)
    db.commit()

    # 19 declared relationship edges:
    #   subnet_web->vpc, subnet_app->vpc, sg_web->vpc, sg_app->vpc (4, in_network)
    #   elb->vpc (in_network), elb->ec2_web (routes_to)                        (2)
    #   ec2_web->vpc, ec2_web->subnet_web, ec2_web->sg_web, ec2_web->role      (4)
    #   ec2_app->vpc, ec2_app->subnet_app, ec2_app->sg_app                    (3)
    #   ebs->ec2_web (attached_to)                                             (1)
    #   rds->vpc, rds->sg_app                                                  (2)
    #   lambda->vpc, lambda->sg_app, lambda->role                              (3)
    assert counts["edges_created"] == 19
    assert counts["unresolved_edges"] == 0


def _entity(db, org_id, external_id):
    return (
        db(
            (db.entities.external_id == external_id)
            & (db.entities.organization_id == org_id)
        )
        .select()
        .first()
    )


def _networking(db, org_id, external_id):
    return (
        db(
            (db.networking_resources.external_id == external_id)
            & (db.networking_resources.organization_id == org_id)
        )
        .select()
        .first()
    )


def _dependency_exists(db, source_type, source_id, target_type, target_id, dep_type):
    return (
        db(
            (db.dependencies.source_type == source_type)
            & (db.dependencies.source_id == source_id)
            & (db.dependencies.target_type == target_type)
            & (db.dependencies.target_id == target_id)
            & (db.dependencies.dependency_type == dep_type)
        )
        .select()
        .first()
        is not None
    )


def test_seed_topology_covers_every_required_edge_type(seeded):
    """Assert each required edge type (in_network, in_subnet,
    uses_security_group, attached_to, routes_to, assumes_role) resolves to a
    concrete dependencies row, not just a non-zero edges_created count."""
    service, db, org_id = seeded
    results = seed_cloud_discovery.build_discovery_results()

    counts = service._store_discovered_resources(org_id, results)
    db.commit()
    assert counts["unresolved_edges"] == 0

    vpc = _networking(db, org_id, seed_cloud_discovery.VPC_ID)
    subnet_web = _networking(db, org_id, seed_cloud_discovery.SUBNET_WEB)
    sg_web = _networking(db, org_id, seed_cloud_discovery.SG_WEB)
    sg_app = _networking(db, org_id, seed_cloud_discovery.SG_APP)
    elb = _networking(db, org_id, seed_cloud_discovery.ELB_ARN)
    ec2_web = _entity(db, org_id, seed_cloud_discovery.EC2_WEB)
    ec2_app = _entity(db, org_id, seed_cloud_discovery.EC2_APP)

    ebs = (
        db(
            (db.data_stores.external_id == seed_cloud_discovery.EBS_VOL)
            & (db.data_stores.organization_id == org_id)
        )
        .select()
        .first()
    )
    rds = (
        db(
            (db.data_stores.external_id == seed_cloud_discovery.RDS_ARN)
            & (db.data_stores.organization_id == org_id)
        )
        .select()
        .first()
    )
    lambda_svc = (
        db(
            (db.services.external_id == seed_cloud_discovery.LAMBDA_ARN)
            & (db.services.organization_id == org_id)
        )
        .select()
        .first()
    )
    role_identity = (
        db(
            (db.identities.external_id == seed_cloud_discovery.IAM_ROLE_ARN)
            & (db.identities.organization_id == org_id)
        )
        .select()
        .first()
    )

    assert all(
        row is not None
        for row in (
            vpc,
            subnet_web,
            sg_web,
            sg_app,
            elb,
            ec2_web,
            ec2_app,
            ebs,
            rds,
            lambda_svc,
            role_identity,
        )
    )

    # in_network
    assert _dependency_exists(
        db,
        "networking_resource",
        subnet_web.id,
        "networking_resource",
        vpc.id,
        "in_network",
    )
    assert _dependency_exists(
        db, "entity", ec2_web.id, "networking_resource", vpc.id, "in_network"
    )

    # in_subnet
    assert _dependency_exists(
        db, "entity", ec2_web.id, "networking_resource", subnet_web.id, "in_subnet"
    )

    # uses_security_group
    assert _dependency_exists(
        db,
        "entity",
        ec2_web.id,
        "networking_resource",
        sg_web.id,
        "uses_security_group",
    )
    assert _dependency_exists(
        db,
        "data_store",
        rds.id,
        "networking_resource",
        sg_app.id,
        "uses_security_group",
    )

    # attached_to
    assert _dependency_exists(
        db, "data_store", ebs.id, "entity", ec2_web.id, "attached_to"
    )

    # routes_to
    assert _dependency_exists(
        db, "networking_resource", elb.id, "entity", ec2_web.id, "routes_to"
    )

    # assumes_role
    assert _dependency_exists(
        db, "entity", ec2_web.id, "identity", role_identity.id, "assumes_role"
    )
    assert _dependency_exists(
        db, "service", lambda_svc.id, "identity", role_identity.id, "assumes_role"
    )


def test_seed_topology_is_idempotent(seeded):
    """Re-running the same payload against the same org must not duplicate edges."""
    service, db, org_id = seeded
    results = seed_cloud_discovery.build_discovery_results()

    first = service._store_discovered_resources(org_id, results)
    db.commit()
    second = service._store_discovered_resources(org_id, results)
    db.commit()

    assert first["edges_created"] == 19
    assert first["unresolved_edges"] == 0
    # Second pass: every edge already exists, so _create_dependency_link's
    # dedup means no *new* rows get created — edges_created still reflects
    # writes attempted this pass, but the row count must not grow.
    assert second["unresolved_edges"] == 0

    ec2_web = _entity(db, org_id, seed_cloud_discovery.EC2_WEB)
    vpc = _networking(db, org_id, seed_cloud_discovery.VPC_ID)
    rows = db(
        (db.dependencies.source_type == "entity")
        & (db.dependencies.source_id == ec2_web.id)
        & (db.dependencies.target_type == "networking_resource")
        & (db.dependencies.target_id == vpc.id)
        & (db.dependencies.dependency_type == "in_network")
    ).select()
    assert len(rows) == 1


def test_resolve_or_create_demo_tenant_is_idempotent(app):
    """Calling the tenant resolver twice must return the same tenant_id, not duplicate."""
    db = app.db
    first_id = seed_cloud_discovery._resolve_or_create_demo_tenant(db)
    second_id = seed_cloud_discovery._resolve_or_create_demo_tenant(db)
    assert first_id == second_id

    rows = db(db.tenants.slug == seed_cloud_discovery.DEMO_TENANT_SLUG).select()
    assert len(rows) == 1


def test_resolve_or_create_demo_org_is_idempotent(app):
    """Calling the org resolver twice under the same tenant must not duplicate."""
    db = app.db
    tenant_id = seed_cloud_discovery._resolve_or_create_demo_tenant(db)
    first_id = seed_cloud_discovery._resolve_or_create_demo_org(db, tenant_id)
    second_id = seed_cloud_discovery._resolve_or_create_demo_org(db, tenant_id)
    assert first_id == second_id

    rows = db(
        (db.organizations.tenant_id == tenant_id)
        & (db.organizations.name == seed_cloud_discovery.DEMO_ORG_NAME)
    ).select()
    assert len(rows) == 1


def test_resolve_or_create_demo_admin_is_idempotent_and_login_capable(app):
    """The demo admin identity is created once, is active, and its password verifies."""
    from werkzeug.security import check_password_hash

    db = app.db
    tenant_id = seed_cloud_discovery._resolve_or_create_demo_tenant(db)
    seed_cloud_discovery._resolve_or_create_demo_admin(db, tenant_id)
    seed_cloud_discovery._resolve_or_create_demo_admin(db, tenant_id)  # idempotent

    rows = db(
        db.identities.username == seed_cloud_discovery.DEMO_ADMIN_USERNAME
    ).select()
    assert len(rows) == 1

    identity = rows.first()
    assert identity.tenant_id == tenant_id
    assert identity.is_active
    assert identity.portal_role == "admin"
    assert check_password_hash(
        identity.password_hash, seed_cloud_discovery.DEMO_ADMIN_PASSWORD
    )
