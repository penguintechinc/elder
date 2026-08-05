"""Tests for the cloud-discovery relationship linker (PR1 core engine)."""

import logging
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from apps.api.models.security import Certificate
from apps.api.modules.infrastructure.models.infrastructure import (
    DataStore,
    NetworkingResource,
)
from apps.api.modules.sbom.models.assets import Service, Software
from apps.worker.discovery.service import DiscoveryService


def test_domain_tables_have_external_id_column():
    # regression: edge resolution keys on external_id; these tables lacked it.
    for model in (NetworkingResource, Service, Software, DataStore, Certificate):
        assert "external_id" in model.__table__.columns, model.__tablename__


@pytest.fixture
def seeded(app):
    """A DiscoveryService on the test DAL with one tenant + organization.

    The test DB is a session-persistent SQLite file with no per-test
    rollback, so a fixed tenant slug would collide with the UNIQUE
    constraint on `tenants.slug` the second time this fixture runs. Use a
    per-invocation unique slug instead (same pattern as
    tests/unit/test_api_diagram_collab.py) so repeated/parallel use of this
    fixture never collides.
    """
    db = app.db
    tenant_id = db.tenants.insert(
        name="Linker Test Tenant",
        slug=f"linker-test-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    org_id = db.organizations.insert(name="Linker Test Org", tenant_id=tenant_id)
    db.commit()
    yield DiscoveryService(db), db, org_id


def test_entity_insert_sets_external_id(seeded):
    service, db, org_id = seeded
    eid = service._store_as_entity(
        org_id,
        {"name": "web-1", "resource_type": "ec2_instance", "resource_id": "i-abc"},
        "compute",
    )
    db.commit()
    row = db(db.entities.id == eid).select().first()
    assert row.external_id == "i-abc"


def test_entity_update_path_does_not_raise_and_refreshes_metadata(seeded):
    # regression: the UPDATE branch of _store_as_entity wrote an "attributes"
    # key, but the entities table has no such column (it's "metadata"), so
    # re-discovering an existing entity raised CompileError: Unconsumed
    # column names: attributes. This is uncaught in _store_as_entity.
    service, db, org_id = seeded
    resource = {
        "name": "web-1",
        "resource_type": "ec2_instance",
        "resource_id": "i-update-path",
        "region": "us-east-1",
    }

    first_id = service._store_as_entity(org_id, resource, "compute")
    db.commit()

    # Second call on the same org/sub_type/name takes the UPDATE branch.
    second_id = service._store_as_entity(org_id, resource, "compute")
    db.commit()

    assert second_id == first_id

    row = db(db.entities.id == second_id).select().first()
    assert row.external_id == "i-update-path"
    # penguin-dal returns the metadata column as `metadata` (the actual DB
    # column name), not `entity_metadata` (the SQLAlchemy attribute alias).
    assert row.metadata is not None
    assert row.metadata.get("resource_id") == "i-update-path"
    assert row.metadata.get("region") == "us-east-1"


def test_upsert_network_entity_mapping_creates_row(seeded):
    # Characterization test: this is expected to PASS immediately.
    # The historical bug (networking_resource_id vs network_id) is already
    # fixed on this branch; this guards against future regression.
    service, db, org_id = seeded

    net_id = service._upsert_networking_resource(
        org_id, name="vpc-1", network_type="vpc"
    )
    db.commit()
    assert net_id is not None

    entity_id = service._store_as_entity(
        org_id,
        {"name": "web-2", "resource_type": "ec2_instance", "resource_id": "i-map-1"},
        "compute",
    )
    db.commit()
    assert entity_id is not None

    service._upsert_network_entity_mapping(net_id, entity_id, "in_network")
    db.commit()

    row = (
        db(
            (db.network_entity_mappings.network_id == net_id)
            & (db.network_entity_mappings.entity_id == entity_id)
        )
        .select()
        .first()
    )
    assert row is not None
    assert row.relationship_type == "in_network"


def test_resolve_target_prefers_scan_index():
    service = DiscoveryService(MagicMock())
    scan_index = {("aws", "vpc-1"): ("networking_resource", 42)}
    assert service._resolve_target(
        scan_index, "aws", "vpc-1", "networking_resource", 1
    ) == (
        "networking_resource",
        42,
    )


def test_resolve_target_unknown_returns_none_without_db_hit():
    db = MagicMock()
    db.return_value.select.return_value.first.return_value = None
    service = DiscoveryService(db)
    assert (
        service._resolve_target({}, "aws", "vpc-nope", "networking_resource", 1) is None
    )


def test_two_pass_linker_creates_edges(seeded):
    service, db, org_id = seeded
    results = {
        "network": [
            {
                "name": "vpc-1",
                "resource_type": "vpc",
                "resource_id": "vpc-1",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-1"},
            },
        ],
        "compute": [
            {
                "name": "web",
                "resource_type": "ec2_instance",
                "resource_id": "i-1",
                "provider": "aws",
                "metadata": {},
                "relationships": [
                    {
                        "target_external_id": "vpc-1",
                        "edge_type": "in_network",
                        "target_kind": "networking_resource",
                    }
                ],
            },
        ],
    }
    counts = service._store_discovered_resources(org_id, results)
    assert counts == {"edges_created": 1, "unresolved_edges": 0}

    # Scope by organization_id: external_id/resource_id values like "i-1" and
    # "vpc-1" are reused by other tests in this module against their own
    # (uniquely-slugged) organizations in the same session-persistent DB.
    inst = (
        db((db.entities.external_id == "i-1") & (db.entities.organization_id == org_id))
        .select()
        .first()
    )
    vpc = (
        db(
            (db.networking_resources.external_id == "vpc-1")
            & (db.networking_resources.organization_id == org_id)
        )
        .select()
        .first()
    )
    dep = (
        db(
            (db.dependencies.source_type == "entity")
            & (db.dependencies.source_id == inst.id)
            & (db.dependencies.target_type == "networking_resource")
            & (db.dependencies.target_id == vpc.id)
        )
        .select()
        .first()
    )
    assert dep is not None and dep.dependency_type == "in_network"
    # dual-write to the topology-tab projection
    nem = (
        db(
            (db.network_entity_mappings.network_id == vpc.id)
            & (db.network_entity_mappings.entity_id == inst.id)
        )
        .select()
        .first()
    )
    assert nem is not None


def test_linker_is_idempotent(seeded):
    service, db, org_id = seeded
    results = {
        "network": [
            {
                "name": "vpc-1",
                "resource_type": "vpc",
                "resource_id": "vpc-1",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-1"},
            }
        ],
        "compute": [
            {
                "name": "web",
                "resource_type": "ec2_instance",
                "resource_id": "i-1",
                "provider": "aws",
                "metadata": {},
                "relationships": [
                    {
                        "target_external_id": "vpc-1",
                        "edge_type": "in_network",
                        "target_kind": "networking_resource",
                    }
                ],
            }
        ],
    }
    service._store_discovered_resources(org_id, results)
    service._store_discovered_resources(org_id, results)

    # Scope to this test's own org/entity: the dependencies table is shared
    # across the whole (session-persistent) test DB, so an unscoped lookup
    # by external_id alone, or a count of dependency_type == "in_network",
    # would also pick up rows from other tests in this module that reuse
    # the same resource_id ("i-1") or edge_type.
    inst = (
        db((db.entities.external_id == "i-1") & (db.entities.organization_id == org_id))
        .select()
        .first()
    )
    rows = db(
        (db.dependencies.source_type == "entity")
        & (db.dependencies.source_id == inst.id)
        & (db.dependencies.dependency_type == "in_network")
    ).select()
    assert len(rows) == 1


def test_linker_counts_unresolved(seeded):
    service, db, org_id = seeded
    results = {
        "compute": [
            {
                "name": "web",
                "resource_type": "ec2_instance",
                "resource_id": "i-9",
                "provider": "aws",
                "metadata": {},
                "relationships": [
                    {
                        "target_external_id": "vpc-missing",
                        "edge_type": "in_network",
                        "target_kind": "networking_resource",
                    }
                ],
            }
        ],
    }
    counts = service._store_discovered_resources(org_id, results)
    assert counts["unresolved_edges"] == 1
    assert counts["edges_created"] == 0


# --- Fix #1: k8s_pod software registration must not clobber the pod's own
# scan_index entry (regression coverage for code review findings). ---------


def test_pod_relationship_source_survives_single_image_software_registration(seeded):
    # regression: _register(scan_index, provider, resource=<pod>, "software",
    # sw_id) used to key on the POD's own resource_id, overwriting the pod's
    # ("entity", entity_id) scan_index entry with ("software", sw_id). Any
    # relationship declared on the pod itself would then resolve to the
    # wrong source. Even a single image triggered the overwrite.
    service, db, org_id = seeded
    results = {
        "compute": [
            {
                "name": "pod-a",
                "resource_type": "k8s_pod",
                "resource_id": "pod-a",
                "provider": "kubernetes",
                "metadata": {
                    "namespace": "default",
                    "containers": [{"image": "nginx:1.25"}],
                },
                "relationships": [
                    {
                        "target_external_id": "default",
                        "edge_type": "in_network",
                        "target_kind": "networking_resource",
                    }
                ],
            }
        ],
    }
    counts = service._store_discovered_resources(org_id, results)
    assert counts == {"edges_created": 1, "unresolved_edges": 0}

    pod = (
        db(
            (db.entities.external_id == "pod-a")
            & (db.entities.organization_id == org_id)
        )
        .select()
        .first()
    )
    assert pod is not None

    dep = (
        db(
            (db.dependencies.source_type == "entity")
            & (db.dependencies.source_id == pod.id)
            & (db.dependencies.dependency_type == "in_network")
        )
        .select()
        .first()
    )
    assert dep is not None
    # Prove the edge did NOT come from the clobbered ("software", sw_id) entry.
    assert dep.source_type == "entity"


def test_pod_relationship_source_survives_multi_image_software_registration(seeded):
    # Same regression as above, but with 2 images: pre-fix, the LAST image
    # processed would be the one left clobbering the pod's scan_index entry.
    service, db, org_id = seeded
    results = {
        "compute": [
            {
                "name": "pod-b",
                "resource_type": "k8s_pod",
                "resource_id": "pod-b",
                "provider": "kubernetes",
                "metadata": {
                    "namespace": "default",
                    "containers": [
                        {"image": "nginx:1.25"},
                        {"image": "busybox:1.36"},
                    ],
                },
                "relationships": [
                    {
                        "target_external_id": "default",
                        "edge_type": "in_network",
                        "target_kind": "networking_resource",
                    }
                ],
            }
        ],
    }
    counts = service._store_discovered_resources(org_id, results)
    assert counts == {"edges_created": 1, "unresolved_edges": 0}

    pod = (
        db(
            (db.entities.external_id == "pod-b")
            & (db.entities.organization_id == org_id)
        )
        .select()
        .first()
    )
    assert pod is not None

    dep = (
        db(
            (db.dependencies.source_type == "entity")
            & (db.dependencies.source_id == pod.id)
            & (db.dependencies.dependency_type == "in_network")
        )
        .select()
        .first()
    )
    assert dep is not None
    assert dep.source_type == "entity"

    # Both images still get their own resolvable scan_index entry.
    nginx_sw = (
        db(
            (db.software.external_id == "nginx:1.25")
            & (db.software.organization_id == org_id)
        )
        .select()
        .first()
    )
    busybox_sw = (
        db(
            (db.software.external_id == "busybox:1.36")
            & (db.software.organization_id == org_id)
        )
        .select()
        .first()
    )
    assert nginx_sw is not None
    assert busybox_sw is not None


# --- Fix #2: an unresolved edge SOURCE (the resource's own id was never
# registered) must be counted/logged, not silently dropped. ----------------


def test_linker_counts_unresolved_when_source_not_registered(seeded, caplog):
    # k8s_secret resources are stored via the "builtin_secret" domain branch,
    # which never calls _register(...) — so a relationship declared on a
    # secret has no scan_index entry to originate from.
    service, db, org_id = seeded
    results = {
        "compute": [
            {
                "name": "my-secret",
                "resource_type": "k8s_secret",
                "resource_id": "secret-1",
                "provider": "kubernetes",
                "metadata": {
                    "namespace": "default",
                    "type": "Opaque",
                    "keys": ["password"],
                },
                "relationships": [
                    {
                        "target_external_id": "default",
                        "edge_type": "in_network",
                        "target_kind": "networking_resource",
                    }
                ],
            }
        ],
    }
    with caplog.at_level("WARNING"):
        counts = service._store_discovered_resources(org_id, results)

    assert counts["unresolved_edges"] == 1
    assert counts["edges_created"] == 0
    assert any(
        "secret-1" in record.message
        and "Unresolved discovery edge source" in record.message
        for record in caplog.records
    )


# --- Fix #3: edges_created must reflect actual writes, not attempts that
# hit the swallowed-exception path in _create_dependency_link. -------------


def test_create_dependency_link_returns_false_on_write_exception():
    db = MagicMock()
    db.return_value.select.return_value.first.return_value = None
    db.dependencies.insert.side_effect = RuntimeError("db exploded")
    service = DiscoveryService(db)
    assert (
        service._create_dependency_link(
            "entity", 1, "networking_resource", 2, "in_network"
        )
        is False
    )


def test_create_dependency_link_returns_true_on_success():
    db = MagicMock()
    db.return_value.select.return_value.first.return_value = None
    service = DiscoveryService(db)
    assert (
        service._create_dependency_link(
            "entity", 1, "networking_resource", 2, "in_network"
        )
        is True
    )


def test_edges_created_excludes_failed_dependency_writes(seeded, monkeypatch):
    # Force the dependencies write to fail even though the target resolves
    # cleanly, and confirm the pass-2 counter does not count it.
    service, db, org_id = seeded
    monkeypatch.setattr(service, "_create_dependency_link", lambda *a, **kw: False)

    results = {
        "network": [
            {
                "name": "vpc-1",
                "resource_type": "vpc",
                "resource_id": "vpc-1",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-1"},
            },
        ],
        "compute": [
            {
                "name": "web",
                "resource_type": "ec2_instance",
                "resource_id": "i-fail",
                "provider": "aws",
                "metadata": {},
                "relationships": [
                    {
                        "target_external_id": "vpc-1",
                        "edge_type": "in_network",
                        "target_kind": "networking_resource",
                    }
                ],
            },
        ],
    }
    counts = service._store_discovered_resources(org_id, results)
    assert counts == {"edges_created": 0, "unresolved_edges": 0}

    inst = (
        db(
            (db.entities.external_id == "i-fail")
            & (db.entities.organization_id == org_id)
        )
        .select()
        .first()
    )
    assert inst is not None
    rows = db(
        (db.dependencies.source_type == "entity")
        & (db.dependencies.source_id == inst.id)
    ).select()
    assert len(rows) == 0


# --- Task 5: edges_created/unresolved_edges must reach the discovery job
# result, not just _store_discovered_resources's own return value. ----------


def test_run_discovery_surfaces_edge_counts_in_job_result(seeded, monkeypatch):
    # regression: _store_discovered_resources's counts were computed but
    # never merged into run_discovery's return dict or the persisted
    # discovery_history.results_json, so operators/E2E had no visibility
    # into linkage health from the job result.
    service, db, org_id = seeded

    job_id = db.discovery_jobs.insert(
        name="Edge Count Job",
        provider="aws",
        config_json={"_organization_id": org_id},
        schedule_interval=3600,
        enabled=True,
    )
    db.commit()

    discovery_results = {
        "compute": [
            {
                "name": "web",
                "resource_type": "ec2_instance",
                "resource_id": "i-edge-counts",
                "provider": "aws",
                "metadata": {},
            }
        ],
        "resources_count": 1,
        "discovery_time": datetime.now(timezone.utc),
    }
    mock_client = MagicMock()
    mock_client.discover_all.return_value = discovery_results
    monkeypatch.setattr(service, "_get_discovery_client", lambda job_id: mock_client)

    known_counts = {"edges_created": 3, "unresolved_edges": 1}
    monkeypatch.setattr(
        service,
        "_store_discovered_resources",
        lambda organization_id, results: dict(known_counts),
    )

    result = service.run_discovery(job_id)

    assert result["success"] is True
    assert result["edges_created"] == 3
    assert result["unresolved_edges"] == 1

    history = (
        db(db.discovery_history.job_id == job_id)
        .select(orderby=~db.discovery_history.id)
        .first()
    )
    assert history is not None
    assert history.results_json["edges_created"] == 3
    assert history.results_json["unresolved_edges"] == 1


def test_run_discovery_without_organization_id_still_persists_zero_counts(
    seeded, monkeypatch
):
    # regression: results_for_storage.update(edge_counts) lived inside the
    # `if organization_id:` branch, so a job with no organization_id (a real,
    # reachable path per create_job's optional organization_id) never wrote
    # edges_created/unresolved_edges into discovery_history.results_json at
    # all, even though the return dict always defaulted them to 0/0.
    service, db, org_id = seeded

    # No "_organization_id" in config_json — this is the no-org path.
    job_id = db.discovery_jobs.insert(
        name="No Org Job",
        provider="aws",
        config_json={},
        schedule_interval=3600,
        enabled=True,
    )
    db.commit()

    discovery_results = {
        "compute": [
            {
                "name": "web",
                "resource_type": "ec2_instance",
                "resource_id": "i-no-org",
                "provider": "aws",
                "metadata": {},
            }
        ],
        "resources_count": 1,
        "discovery_time": datetime.now(timezone.utc),
    }
    mock_client = MagicMock()
    mock_client.discover_all.return_value = discovery_results
    monkeypatch.setattr(service, "_get_discovery_client", lambda job_id: mock_client)

    # _store_discovered_resources must not be called on the no-org path;
    # fail loudly if it is.
    monkeypatch.setattr(
        service,
        "_store_discovered_resources",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("_store_discovered_resources should not run without org_id")
        ),
    )

    result = service.run_discovery(job_id)

    assert result["success"] is True
    assert result["edges_created"] == 0
    assert result["unresolved_edges"] == 0

    history = (
        db(db.discovery_history.job_id == job_id)
        .select(orderby=~db.discovery_history.id)
        .first()
    )
    assert history is not None
    assert history.results_json["edges_created"] == 0
    assert history.results_json["unresolved_edges"] == 0


# --- Task 7: backfill regression coverage for previously-untested edge
# helpers — _create_dependency_link idempotency (explicit dedup assertion,
# distinct from the true/false-return tests above) and the K8s SEMANTIC edge
# helpers _store_k8s_ingress ("routes_to") and _store_k8s_pvc_as_data_store
# ("bound_to"). These are characterization tests of existing behavior. ------


def test_create_dependency_link_is_idempotent(seeded):
    # Distinct from test_create_dependency_link_returns_{true,false}_on_*
    # above (which only assert the bool return against a MagicMock db):
    # this asserts the actual dedup behavior against a real DB — two
    # identical calls must not create a second row. Sentinel ids
    # (9_000_001/9_000_002) are chosen far outside any autoincrement range
    # used elsewhere in this session-persistent-SQLite test module, so the
    # source_id/target_id filter can't accidentally match a row written by
    # another test.
    service, db, org_id = seeded

    # Get tenant_id from organization
    org = db.organizations[org_id]
    tenant_id = org.tenant_id

    service._create_dependency_link(
        "entity", 9_000_001, "entity", 9_000_002, tenant_id, "routes_to"
    )
    service._create_dependency_link(
        "entity", 9_000_001, "entity", 9_000_002, tenant_id, "routes_to"
    )
    db.commit()
    rows = db(
        (db.dependencies.source_id == 9_000_001)
        & (db.dependencies.target_id == 9_000_002)
    ).select()
    assert len(rows) == 1
    assert rows.first().dependency_type == "routes_to"


def test_create_dependency_link_allows_different_types_same_pair(seeded):
    """Different dependency_type values between same (source, target) pair create separate rows."""
    service, db, org_id = seeded

    # Get tenant_id from organization
    org = db.organizations[org_id]
    tenant_id = org.tenant_id

    # Create two edges with different dep_types between the same pair
    service._create_dependency_link(
        "entity", 9_010_001, "entity", 9_010_002, tenant_id, "routes_to"
    )
    service._create_dependency_link(
        "entity", 9_010_001, "entity", 9_010_002, tenant_id, "manages"
    )
    db.commit()

    # Both rows should exist
    rows = db(
        (db.dependencies.source_id == 9_010_001)
        & (db.dependencies.target_id == 9_010_002)
    ).select()
    assert len(rows) == 2

    types = {row.dependency_type for row in rows}
    assert types == {"routes_to", "manages"}


def test_create_dependency_link_non_canonical_type_warns_but_persists(seeded, caplog):
    """Non-canonical dependency types log a warning but edge is still created."""
    service, db, org_id = seeded

    # Get tenant_id from organization
    org = db.organizations[org_id]
    tenant_id = org.tenant_id

    # Create edge with unknown dep_type
    with caplog.at_level(logging.WARNING):
        result = service._create_dependency_link(
            "entity", 9_020_001, "entity", 9_020_002, tenant_id, "frobnicate"
        )
    db.commit()

    assert result is True

    # Verify the edge was created
    rows = db(
        (db.dependencies.source_id == 9_020_001)
        & (db.dependencies.target_id == 9_020_002)
        & (db.dependencies.dependency_type == "frobnicate")
    ).select()
    assert len(rows) == 1

    # Verify the warning was logged
    assert any(
        "Non-canonical dependency_type" in record.message
        and "frobnicate" in record.message
        for record in caplog.records
    )


def test_create_dependency_link_canonical_type_no_warning(seeded, caplog):
    """Canonical dependency types do not produce warnings."""
    service, db, org_id = seeded

    # Get tenant_id from organization
    org = db.organizations[org_id]
    tenant_id = org.tenant_id

    # Create edge with canonical dep_type
    with caplog.at_level(logging.WARNING):
        result = service._create_dependency_link(
            "entity", 9_030_001, "entity", 9_030_002, tenant_id, "bound_to"
        )
    db.commit()

    assert result is True

    # Verify no non-canonical warnings were logged
    non_canonical_warnings = [
        record
        for record in caplog.records
        if "Non-canonical dependency_type" in record.message
    ]
    assert len(non_canonical_warnings) == 0


def test_store_k8s_ingress_creates_routes_to_edge_to_service(seeded):
    service, db, org_id = seeded

    # Get tenant_id from organization
    org = db.organizations[org_id]
    tenant_id = org.tenant_id

    svc_id = service._store_as_service(
        org_id,
        {
            "name": "web-svc",
            "resource_type": "k8s_service",
            "resource_id": "svc-1",
            "metadata": {"ports": [{"port": 80}]},
        },
        "kubernetes",
    )
    db.commit()
    assert svc_id is not None

    ingress_resource = {
        "name": "web-ingress",
        "resource_type": "k8s_ingress",
        "resource_id": "ingress-1",
        "metadata": {
            "namespace": "default",
            "ingress_class": "nginx",
            "paths": [{"service": "web-svc", "host": "example.com", "path": "/"}],
            "backend_services": ["web-svc"],
        },
    }
    ingress_id = service._store_k8s_ingress(org_id, ingress_resource, {}, tenant_id)
    db.commit()
    assert ingress_id is not None

    dep = (
        db(
            (db.dependencies.source_type == "networking_resource")
            & (db.dependencies.source_id == ingress_id)
            & (db.dependencies.target_type == "service")
            & (db.dependencies.target_id == svc_id)
        )
        .select()
        .first()
    )
    assert dep is not None
    assert dep.dependency_type == "routes_to"


def test_store_k8s_ingress_skips_edge_when_service_absent(seeded):
    # No "backend-svc-missing" row exists in `services` for this org — the
    # `if svc:` guard in _store_k8s_ingress must skip edge creation without
    # raising, while the ingress itself is still persisted.
    service, db, org_id = seeded

    # Get tenant_id from organization
    org = db.organizations[org_id]
    tenant_id = org.tenant_id

    ingress_resource = {
        "name": "orphan-ingress",
        "resource_type": "k8s_ingress",
        "resource_id": "ingress-orphan",
        "metadata": {
            "namespace": "default",
            "backend_services": ["backend-svc-missing"],
        },
    }
    ingress_id = service._store_k8s_ingress(org_id, ingress_resource, {}, tenant_id)
    db.commit()
    assert ingress_id is not None

    rows = db(
        (db.dependencies.source_type == "networking_resource")
        & (db.dependencies.source_id == ingress_id)
    ).select()
    assert len(rows) == 0


def test_store_k8s_pvc_creates_bound_to_edge_to_pv(seeded):
    service, db, org_id = seeded

    # Get tenant_id from organization
    org = db.organizations[org_id]
    tenant_id = org.tenant_id

    pv_id = service._store_as_data_store(
        org_id,
        {
            "name": "pv-vol-1",
            "resource_type": "k8s_persistent_volume",
            "resource_id": "pv-vol-1",
            "metadata": {},
        },
        "kubernetes",
    )
    db.commit()
    assert pv_id is not None

    pvc_resource = {
        "name": "pvc-1",
        "resource_type": "k8s_pvc",
        "resource_id": "pvc-1",
        "metadata": {"volume_name": "pv-vol-1"},
    }
    pvc_id = service._store_k8s_pvc_as_data_store(
        org_id, pvc_resource, "kubernetes", tenant_id
    )
    db.commit()
    assert pvc_id is not None

    dep = (
        db(
            (db.dependencies.source_type == "data_store")
            & (db.dependencies.source_id == pvc_id)
            & (db.dependencies.target_type == "data_store")
            & (db.dependencies.target_id == pv_id)
        )
        .select()
        .first()
    )
    assert dep is not None
    assert dep.dependency_type == "bound_to"


def test_store_k8s_pvc_skips_edge_when_pv_absent(seeded):
    # No data_store row named "pv-does-not-exist" exists for this org — the
    # `if pv:` guard in _store_k8s_pvc_as_data_store must skip edge creation
    # without raising, while the PVC itself is still persisted.
    service, db, org_id = seeded

    # Get tenant_id from organization
    org = db.organizations[org_id]
    tenant_id = org.tenant_id

    pvc_resource = {
        "name": "pvc-orphan",
        "resource_type": "k8s_pvc",
        "resource_id": "pvc-orphan",
        "metadata": {"volume_name": "pv-does-not-exist"},
    }
    pvc_id = service._store_k8s_pvc_as_data_store(
        org_id, pvc_resource, "kubernetes", tenant_id
    )
    db.commit()
    assert pvc_id is not None

    rows = db(
        (db.dependencies.source_type == "data_store")
        & (db.dependencies.source_id == pvc_id)
    ).select()
    assert len(rows) == 0


def test_dependencies_have_tenant_id_set(seeded):
    """Verify that discovery-created dependencies have tenant_id populated.

    regression: gh-189
    """
    service, db, org_id = seeded

    # Get the tenant_id from the organization
    org = db.organizations[org_id]
    tenant_id = org.tenant_id

    # Create a simple dependency link
    src_id = service._store_as_entity(
        org_id,
        {"name": "source", "resource_type": "pod", "resource_id": "pod-1"},
        "kubernetes",
    )
    tgt_id = service._store_as_entity(
        org_id,
        {"name": "target", "resource_type": "pod", "resource_id": "pod-2"},
        "kubernetes",
    )

    # Use _create_dependency_link to create a dependency (as discovery does)
    result = service._create_dependency_link(
        "entity",
        src_id,
        "entity",
        tgt_id,
        tenant_id,
        "discovered_from",
        {"test": "data"},
    )
    assert result is True

    db.commit()

    # Verify the dependency row has tenant_id set
    dep_rows = db(
        (db.dependencies.source_type == "entity")
        & (db.dependencies.source_id == src_id)
        & (db.dependencies.target_type == "entity")
        & (db.dependencies.target_id == tgt_id)
    ).select()

    assert len(dep_rows) == 1, "Dependency should exist"
    dep = dep_rows[0]
    assert (
        dep.tenant_id == tenant_id
    ), f"Dependency must have tenant_id={tenant_id}, got {dep.tenant_id}"


# PR2a tests: provider-agnostic discovery-engine prerequisites for cloud edge capture


def test_format_resource_includes_external_id_and_relationships(app):
    """Test that format_resource returns external_id and relationships keys."""
    from apps.worker.discovery.aws_discovery import AWSDiscoveryClient

    # Create a minimal config to instantiate the discovery client
    config = {
        "provider_type": "aws",
        "aws_access_key_id": "test",
        "aws_secret_access_key": "test",
    }
    client = AWSDiscoveryClient(config)

    # Test with both external_id and relationships provided
    result = client.format_resource(
        resource_id="i-123",
        resource_type="ec2_instance",
        name="test-instance",
        metadata={"key": "value"},
        region="us-east-1",
        tags={"env": "test"},
        external_id="arn:aws:ec2:us-east-1:123456789012:instance/i-123",
        relationships=[{"type": "assumes_role", "target_id": "role-1"}],
    )

    assert result["external_id"] == "arn:aws:ec2:us-east-1:123456789012:instance/i-123"
    assert result["relationships"] == [{"type": "assumes_role", "target_id": "role-1"}]
    assert result["resource_id"] == "i-123"
    assert result["name"] == "test-instance"


def test_format_resource_defaults_external_id_and_relationships(app):
    """Test that format_resource uses sensible defaults when external_id/relationships omitted."""
    from apps.worker.discovery.aws_discovery import AWSDiscoveryClient

    config = {
        "provider_type": "aws",
        "aws_access_key_id": "test",
        "aws_secret_access_key": "test",
    }
    client = AWSDiscoveryClient(config)

    result = client.format_resource(
        resource_id="i-456",
        resource_type="ec2_instance",
        name="test-instance-2",
        metadata={},
    )

    # external_id should be None when not provided
    assert result["external_id"] is None
    # relationships should default to empty list
    assert result["relationships"] == []


def test_upsert_networking_resource_preserves_external_id_on_update(seeded):
    """Test that _upsert_networking_resource preserves external_id on update when omitted."""
    service, db, org_id = seeded

    # Insert with external_id
    net_id = service._upsert_networking_resource(
        organization_id=org_id,
        name="vpc-preserve-test",
        network_type="vpc",
        external_id="arn:aws:ec2:us-east-1:123456789012:vpc/vpc-abc",
    )
    db.commit()

    row = db(db.networking_resources.id == net_id).select().first()
    assert row.external_id == "arn:aws:ec2:us-east-1:123456789012:vpc/vpc-abc"

    # Update without providing external_id — should preserve the existing value
    service._upsert_networking_resource(
        organization_id=org_id,
        name="vpc-preserve-test",
        network_type="vpc",
        attributes={"new_attr": "value"},
        # external_id deliberately omitted
    )
    db.commit()

    row = db(db.networking_resources.id == net_id).select().first()
    # The external_id should still be the original value, not nulled
    assert row.external_id == "arn:aws:ec2:us-east-1:123456789012:vpc/vpc-abc"
    assert row.attributes.get("new_attr") == "value"


def test_store_iam_as_identity_returns_id_on_insert(seeded):
    """Test that _store_iam_as_identity returns identity_id and sets external_id on insert."""
    service, db, org_id = seeded

    resource = {
        "name": "test-user",
        "resource_type": "iam_user",
        "resource_id": "arn:aws:iam::123456789012:user/test-user",
        "metadata": {
            "arn": "arn:aws:iam::123456789012:user/test-user",
        },
    }

    identity_id = service._store_iam_as_identity(org_id, resource)
    db.commit()

    assert identity_id is not None
    row = db(db.identities.id == identity_id).select().first()
    assert row is not None
    assert row.external_id == "arn:aws:iam::123456789012:user/test-user"
    assert row.auth_provider == "aws"
    assert row.identity_type == "integration"


def test_store_iam_as_identity_returns_id_on_update(seeded):
    """Test that _store_iam_as_identity returns identity_id and updates external_id on update."""
    service, db, org_id = seeded

    resource = {
        "name": "test-role",
        "resource_type": "iam_role",
        "resource_id": "arn:aws:iam::123456789012:role/test-role",
        "metadata": {
            "arn": "arn:aws:iam::123456789012:role/test-role",
        },
    }

    # First insert
    identity_id_1 = service._store_iam_as_identity(org_id, resource)
    db.commit()

    # Second call should hit update path
    identity_id_2 = service._store_iam_as_identity(org_id, resource)
    db.commit()

    assert identity_id_1 == identity_id_2
    row = db(db.identities.id == identity_id_2).select().first()
    assert row.external_id == "arn:aws:iam::123456789012:role/test-role"
    assert row.identity_type == "serviceAccount"  # IAM roles are service accounts


def test_identity_registration_in_scan_index(seeded):
    """Test that registered IAM identities resolve via scan_index in same-scan."""
    service, db, org_id = seeded

    iam_resource = {
        "name": "lambda-exec-role",
        "resource_type": "iam_role",
        "resource_id": "arn:aws:iam::123456789012:role/lambda-exec",
        "metadata": {
            "arn": "arn:aws:iam::123456789012:role/lambda-exec",
        },
    }

    # Store and register
    identity_id = service._store_iam_as_identity(org_id, iam_resource)
    scan_index = {}
    service._register(scan_index, "aws", iam_resource, "identity", identity_id)
    db.commit()

    # Verify scan_index entry
    ext_id = "arn:aws:iam::123456789012:role/lambda-exec"
    assert ("aws", ext_id) in scan_index
    assert scan_index[("aws", ext_id)] == ("identity", identity_id)

    # Verify _resolve_target finds it via scan_index
    result = service._resolve_target(scan_index, "aws", ext_id, "identity", org_id)
    assert result == ("identity", identity_id)


def test_resolve_target_in_db_finds_identity_by_external_id(seeded):
    """Test that _resolve_target_in_db resolves identity by external_id (DB fallback)."""
    service, db, org_id = seeded

    # Insert an identity with external_id
    identity_resource = {
        "name": "gcp-service-account",
        "resource_type": "service_account",
        "resource_id": "sa-123@project.iam.gserviceaccount.com",
        "metadata": {
            "arn": "sa-123@project.iam.gserviceaccount.com",
        },
    }

    identity_id = service._store_iam_as_identity(org_id, identity_resource)
    db.commit()

    # Now resolve it via DB lookup (simulating a different scan)
    external_id = "sa-123@project.iam.gserviceaccount.com"
    result = service._resolve_target_in_db(external_id, "identity", org_id)

    assert result is not None
    assert result == ("identity", identity_id)


def test_iam_identity_creates_discovered_from_edge(seeded):
    """Test that storing iam_user/iam_role and registering it creates discovered_from edge."""
    service, db, org_id = seeded

    # Create root entity (e.g., AWS account)
    root_entity_id = service._store_as_entity(
        org_id,
        {
            "name": "aws-account",
            "resource_type": "aws_account",
            "resource_id": "123456789012",
        },
        "provider",
    )
    db.commit()

    # Get tenant for dependency creation
    org = db(db.organizations.id == org_id).select().first()
    tenant_id = org.tenant_id

    # Create IAM role
    iam_resource = {
        "name": "cross-account-role",
        "resource_type": "iam_role",
        "resource_id": "arn:aws:iam::123456789012:role/cross-account",
        "metadata": {
            "arn": "arn:aws:iam::123456789012:role/cross-account",
        },
    }

    identity_id = service._store_iam_as_identity(org_id, iam_resource)
    db.commit()

    # Create the dependency edge (as the code path does)
    result = service._create_dependency_link(
        "identity",
        identity_id,
        "entity",
        root_entity_id,
        tenant_id,
        "discovered_from",
        {"provider": "aws"},
    )

    assert result is True
    db.commit()

    # Verify the dependency exists
    dep_rows = db(
        (db.dependencies.source_type == "identity")
        & (db.dependencies.source_id == identity_id)
        & (db.dependencies.target_type == "entity")
        & (db.dependencies.target_id == root_entity_id)
    ).select()

    assert len(dep_rows) == 1
    dep = dep_rows[0]
    assert dep.dependency_type == "discovered_from"
    assert dep.tenant_id == tenant_id


# --- AWS Network Topology Relationship Tests (Phase B1) -----------------------


def test_aws_ec2_vpc_relationship_created(seeded):
    """EC2 instance -> VPC (in_network) edge is created correctly."""
    service, db, org_id = seeded
    results = {
        "network": [
            {
                "name": "vpc-prod",
                "resource_type": "vpc",
                "resource_id": "vpc-prod",
                "external_id": "vpc-prod",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-prod"},
            }
        ],
        "compute": [
            {
                "name": "web-1",
                "resource_type": "ec2_instance",
                "resource_id": "i-web-1",
                "external_id": "i-web-1",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-prod"},
                "relationships": [
                    {
                        "target_external_id": "vpc-prod",
                        "edge_type": "in_network",
                        "target_kind": "networking_resource",
                    }
                ],
            }
        ],
    }
    counts = service._store_discovered_resources(org_id, results)
    assert counts["edges_created"] == 1
    assert counts["unresolved_edges"] == 0

    inst = (
        db(
            (db.entities.external_id == "i-web-1")
            & (db.entities.organization_id == org_id)
        )
        .select()
        .first()
    )
    vpc = (
        db(
            (db.networking_resources.external_id == "vpc-prod")
            & (db.networking_resources.organization_id == org_id)
        )
        .select()
        .first()
    )
    dep = (
        db(
            (db.dependencies.source_id == inst.id)
            & (db.dependencies.target_id == vpc.id)
            & (db.dependencies.dependency_type == "in_network")
        )
        .select()
        .first()
    )
    assert dep is not None


def test_aws_ec2_subnet_relationship_created(seeded):
    """EC2 instance -> subnet (in_subnet) edge is created correctly."""
    service, db, org_id = seeded
    results = {
        "network": [
            {
                "name": "subnet-az1",
                "resource_type": "subnet",
                "resource_id": "subnet-az1",
                "external_id": "subnet-az1",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-prod", "cidr_block": "10.0.1.0/24"},
            }
        ],
        "compute": [
            {
                "name": "web-1",
                "resource_type": "ec2_instance",
                "resource_id": "i-subnet-test",
                "external_id": "i-subnet-test",
                "provider": "aws",
                "metadata": {"subnet_id": "subnet-az1"},
                "relationships": [
                    {
                        "target_external_id": "subnet-az1",
                        "edge_type": "in_subnet",
                        "target_kind": "networking_resource",
                    }
                ],
            }
        ],
    }
    counts = service._store_discovered_resources(org_id, results)
    assert counts["edges_created"] == 1
    assert counts["unresolved_edges"] == 0

    inst = (
        db(
            (db.entities.external_id == "i-subnet-test")
            & (db.entities.organization_id == org_id)
        )
        .select()
        .first()
    )
    subnet = (
        db(
            (db.networking_resources.external_id == "subnet-az1")
            & (db.networking_resources.organization_id == org_id)
        )
        .select()
        .first()
    )
    dep = (
        db(
            (db.dependencies.source_id == inst.id)
            & (db.dependencies.target_id == subnet.id)
            & (db.dependencies.dependency_type == "in_subnet")
        )
        .select()
        .first()
    )
    assert dep is not None


def test_aws_ec2_security_group_relationships(seeded):
    """EC2 instance -> multiple security groups (uses_security_group) edges."""
    service, db, org_id = seeded
    results = {
        "network": [
            {
                "name": "sg-web",
                "resource_type": "security_group",
                "resource_id": "sg-web",
                "external_id": "sg-web",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-prod"},
            },
            {
                "name": "sg-app",
                "resource_type": "security_group",
                "resource_id": "sg-app",
                "external_id": "sg-app",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-prod"},
            },
        ],
        "compute": [
            {
                "name": "web-1",
                "resource_type": "ec2_instance",
                "resource_id": "i-sg-test",
                "external_id": "i-sg-test",
                "provider": "aws",
                "metadata": {},
                "relationships": [
                    {
                        "target_external_id": "sg-web",
                        "edge_type": "uses_security_group",
                        "target_kind": "networking_resource",
                    },
                    {
                        "target_external_id": "sg-app",
                        "edge_type": "uses_security_group",
                        "target_kind": "networking_resource",
                    },
                ],
            }
        ],
    }
    counts = service._store_discovered_resources(org_id, results)
    assert counts["edges_created"] == 2
    assert counts["unresolved_edges"] == 0

    inst = (
        db(
            (db.entities.external_id == "i-sg-test")
            & (db.entities.organization_id == org_id)
        )
        .select()
        .first()
    )
    sgs = db(
        (db.networking_resources.network_type == "security_group")
        & (db.networking_resources.organization_id == org_id)
    ).select()
    deps = db(
        (db.dependencies.source_id == inst.id)
        & (db.dependencies.dependency_type == "uses_security_group")
    ).select()
    assert len(deps) == 2
    assert set(d.target_id for d in deps) == {sg.id for sg in sgs}


def test_aws_ebs_attached_to_ec2(seeded):
    """EBS volume -> EC2 instance (attached_to) edge is created correctly."""
    service, db, org_id = seeded
    results = {
        "compute": [
            {
                "name": "web-1",
                "resource_type": "ec2_instance",
                "resource_id": "i-ebs-test",
                "external_id": "i-ebs-test",
                "provider": "aws",
                "metadata": {},
            }
        ],
        "storage": [
            {
                "name": "data-vol",
                "resource_type": "ebs_volume",
                "resource_id": "vol-123",
                "external_id": "vol-123",
                "provider": "aws",
                "metadata": {"size_gb": 100},
                "relationships": [
                    {
                        "target_external_id": "i-ebs-test",
                        "edge_type": "attached_to",
                        "target_kind": "entity",
                    }
                ],
            }
        ],
    }
    counts = service._store_discovered_resources(org_id, results)
    assert counts["edges_created"] == 1
    assert counts["unresolved_edges"] == 0

    inst = (
        db(
            (db.entities.external_id == "i-ebs-test")
            & (db.entities.organization_id == org_id)
        )
        .select()
        .first()
    )
    vol = (
        db(
            (db.data_stores.external_id == "vol-123")
            & (db.data_stores.organization_id == org_id)
        )
        .select()
        .first()
    )
    dep = (
        db(
            (db.dependencies.source_type == "data_store")
            & (db.dependencies.source_id == vol.id)
            & (db.dependencies.target_type == "entity")
            & (db.dependencies.target_id == inst.id)
            & (db.dependencies.dependency_type == "attached_to")
        )
        .select()
        .first()
    )
    assert dep is not None


def test_aws_subnet_in_vpc_relationship(seeded):
    """Subnet -> VPC (in_network) edge is created correctly."""
    service, db, org_id = seeded
    results = {
        "network": [
            {
                "name": "vpc-prod",
                "resource_type": "vpc",
                "resource_id": "vpc-prod",
                "external_id": "vpc-prod",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-prod"},
            },
            {
                "name": "subnet-az1",
                "resource_type": "subnet",
                "resource_id": "subnet-az1",
                "external_id": "subnet-az1",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-prod"},
                "relationships": [
                    {
                        "target_external_id": "vpc-prod",
                        "edge_type": "in_network",
                        "target_kind": "networking_resource",
                    }
                ],
            },
        ],
    }
    counts = service._store_discovered_resources(org_id, results)
    assert counts["edges_created"] == 1
    assert counts["unresolved_edges"] == 0

    subnet = (
        db(
            (db.networking_resources.external_id == "subnet-az1")
            & (db.networking_resources.organization_id == org_id)
        )
        .select()
        .first()
    )
    vpc = (
        db(
            (db.networking_resources.external_id == "vpc-prod")
            & (db.networking_resources.organization_id == org_id)
        )
        .select()
        .first()
    )
    dep = (
        db(
            (db.dependencies.source_type == "networking_resource")
            & (db.dependencies.source_id == subnet.id)
            & (db.dependencies.target_type == "networking_resource")
            & (db.dependencies.target_id == vpc.id)
            & (db.dependencies.dependency_type == "in_network")
        )
        .select()
        .first()
    )
    assert dep is not None


def test_aws_security_group_in_vpc(seeded):
    """Security group -> VPC (in_network) edge is created correctly."""
    service, db, org_id = seeded
    results = {
        "network": [
            {
                "name": "vpc-prod",
                "resource_type": "vpc",
                "resource_id": "vpc-prod",
                "external_id": "vpc-prod",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-prod"},
            },
            {
                "name": "sg-web",
                "resource_type": "security_group",
                "resource_id": "sg-web",
                "external_id": "sg-web",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-prod"},
                "relationships": [
                    {
                        "target_external_id": "vpc-prod",
                        "edge_type": "in_network",
                        "target_kind": "networking_resource",
                    }
                ],
            },
        ],
    }
    counts = service._store_discovered_resources(org_id, results)
    assert counts["edges_created"] == 1
    assert counts["unresolved_edges"] == 0

    sg = (
        db(
            (db.networking_resources.external_id == "sg-web")
            & (db.networking_resources.organization_id == org_id)
        )
        .select()
        .first()
    )
    vpc = (
        db(
            (db.networking_resources.external_id == "vpc-prod")
            & (db.networking_resources.organization_id == org_id)
        )
        .select()
        .first()
    )
    dep = (
        db(
            (db.dependencies.source_type == "networking_resource")
            & (db.dependencies.source_id == sg.id)
            & (db.dependencies.target_type == "networking_resource")
            & (db.dependencies.target_id == vpc.id)
            & (db.dependencies.dependency_type == "in_network")
        )
        .select()
        .first()
    )
    assert dep is not None


def test_aws_complex_topology_all_edges_resolve(seeded):
    """Full AWS topology: VPC -> subnets -> EC2 -> SGs + EBS."""
    service, db, org_id = seeded
    results = {
        "network": [
            {
                "name": "vpc-prod",
                "resource_type": "vpc",
                "resource_id": "vpc-prod",
                "external_id": "vpc-prod",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-prod"},
            },
            {
                "name": "subnet-1a",
                "resource_type": "subnet",
                "resource_id": "subnet-1a",
                "external_id": "subnet-1a",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-prod"},
                "relationships": [
                    {
                        "target_external_id": "vpc-prod",
                        "edge_type": "in_network",
                        "target_kind": "networking_resource",
                    }
                ],
            },
            {
                "name": "sg-web",
                "resource_type": "security_group",
                "resource_id": "sg-web",
                "external_id": "sg-web",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-prod"},
                "relationships": [
                    {
                        "target_external_id": "vpc-prod",
                        "edge_type": "in_network",
                        "target_kind": "networking_resource",
                    }
                ],
            },
        ],
        "compute": [
            {
                "name": "web-1",
                "resource_type": "ec2_instance",
                "resource_id": "i-web-1",
                "external_id": "i-web-1",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-prod"},
                "relationships": [
                    {
                        "target_external_id": "vpc-prod",
                        "edge_type": "in_network",
                        "target_kind": "networking_resource",
                    },
                    {
                        "target_external_id": "subnet-1a",
                        "edge_type": "in_subnet",
                        "target_kind": "networking_resource",
                    },
                    {
                        "target_external_id": "sg-web",
                        "edge_type": "uses_security_group",
                        "target_kind": "networking_resource",
                    },
                ],
            }
        ],
        "storage": [
            {
                "name": "data-vol",
                "resource_type": "ebs_volume",
                "resource_id": "vol-data",
                "external_id": "vol-data",
                "provider": "aws",
                "metadata": {"size_gb": 100},
                "relationships": [
                    {
                        "target_external_id": "i-web-1",
                        "edge_type": "attached_to",
                        "target_kind": "entity",
                    }
                ],
            }
        ],
    }
    counts = service._store_discovered_resources(org_id, results)
    # 4 edges: vpc->vpc (subnet->vpc), vpc->vpc (sg->vpc), ec2->subnet, ec2->sg, ec2->vpc, vol->ec2
    assert counts["edges_created"] == 6
    assert counts["unresolved_edges"] == 0


def test_phase_b2_rds_relationships(seeded):
    """Phase B2: RDS emits in_network and uses_security_group relationships."""
    service, db, org_id = seeded
    results = {
        "network": [
            {
                "name": "vpc-prod",
                "resource_type": "vpc",
                "resource_id": "vpc-prod",
                "external_id": "vpc-prod",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-prod"},
            },
            {
                "name": "sg-rds",
                "resource_type": "security_group",
                "resource_id": "sg-rds",
                "external_id": "sg-rds",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-prod"},
                "relationships": [
                    {
                        "target_external_id": "vpc-prod",
                        "edge_type": "in_network",
                        "target_kind": "networking_resource",
                    }
                ],
            },
        ],
        "data_store": [
            {
                "name": "prod-db",
                "resource_type": "rds_instance",
                "resource_id": "prod-db",
                "external_id": "arn:aws:rds:us-east-2:123456789012:db:prod-db",
                "provider": "aws",
                "metadata": {"engine": "postgres", "vpc_id": "vpc-prod"},
                "relationships": [
                    {
                        "target_external_id": "vpc-prod",
                        "edge_type": "in_network",
                        "target_kind": "networking_resource",
                    },
                    {
                        "target_external_id": "sg-rds",
                        "edge_type": "uses_security_group",
                        "target_kind": "networking_resource",
                    },
                ],
            }
        ],
    }
    counts = service._store_discovered_resources(org_id, results)
    assert counts["edges_created"] == 3  # sg->vpc, rds->vpc, rds->sg
    assert counts["unresolved_edges"] == 0

    # Verify RDS data_store was created
    rds = (
        db(
            (
                db.data_stores.external_id
                == "arn:aws:rds:us-east-2:123456789012:db:prod-db"
            )
            & (db.data_stores.organization_id == org_id)
        )
        .select()
        .first()
    )
    assert rds is not None

    # Verify edges were created
    vpc = (
        db(
            (db.networking_resources.external_id == "vpc-prod")
            & (db.networking_resources.organization_id == org_id)
        )
        .select()
        .first()
    )
    sg = (
        db(
            (db.networking_resources.external_id == "sg-rds")
            & (db.networking_resources.organization_id == org_id)
        )
        .select()
        .first()
    )

    rds_to_vpc_dep = (
        db(
            (db.dependencies.source_type == "data_store")
            & (db.dependencies.source_id == rds.id)
            & (db.dependencies.target_type == "networking_resource")
            & (db.dependencies.target_id == vpc.id)
            & (db.dependencies.dependency_type == "in_network")
        )
        .select()
        .first()
    )
    assert rds_to_vpc_dep is not None

    rds_to_sg_dep = (
        db(
            (db.dependencies.source_type == "data_store")
            & (db.dependencies.source_id == rds.id)
            & (db.dependencies.target_type == "networking_resource")
            & (db.dependencies.target_id == sg.id)
            & (db.dependencies.dependency_type == "uses_security_group")
        )
        .select()
        .first()
    )
    assert rds_to_sg_dep is not None


def test_phase_b2_lambda_relationships(seeded):
    """Phase B2: Lambda emits in_network, uses_security_group, and assumes_role relationships."""
    service, db, org_id = seeded

    # Pre-create the IAM role as an identity with a unique username per org
    tenant_id = 1
    now = datetime.now(timezone.utc)
    role_arn = "arn:aws:iam::123456789012:role/lambda-role"
    username = (
        f"aws:123456789012:lambda-role:org{org_id}"  # Make username unique per org
    )

    # Check if identity already exists for this org
    existing = (
        db(
            (db.identities.username == username)
            & (db.identities.organization_id == org_id)
        )
        .select()
        .first()
    )
    if existing:
        identity_id = existing.id
    else:
        identity_id = db.identities.insert(
            tenant_id=tenant_id,
            identity_type="serviceAccount",
            username=username,
            full_name="lambda-role",
            organization_id=org_id,
            auth_provider="aws",
            auth_provider_id=role_arn,
            external_id=role_arn,
            portal_role="observer",
            is_active=True,
            is_superuser=False,
            mfa_enabled=False,
            must_change_password=False,
            created_at=now,
            updated_at=now,
        )

    results = {
        "network": [
            {
                "name": "vpc-prod",
                "resource_type": "vpc",
                "resource_id": "vpc-prod",
                "external_id": "vpc-prod",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-prod"},
            },
            {
                "name": "sg-lambda",
                "resource_type": "security_group",
                "resource_id": "sg-lambda",
                "external_id": "sg-lambda",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-prod"},
                "relationships": [
                    {
                        "target_external_id": "vpc-prod",
                        "edge_type": "in_network",
                        "target_kind": "networking_resource",
                    }
                ],
            },
        ],
        "service": [
            {
                "name": "my-func",
                "resource_type": "lambda_function",
                "resource_id": "arn:aws:lambda:us-east-2:123456789012:function:my-func",
                "external_id": "arn:aws:lambda:us-east-2:123456789012:function:my-func",
                "provider": "aws",
                "metadata": {"runtime": "python3.11"},
                "relationships": [
                    {
                        "target_external_id": "vpc-prod",
                        "edge_type": "in_network",
                        "target_kind": "networking_resource",
                    },
                    {
                        "target_external_id": "sg-lambda",
                        "edge_type": "uses_security_group",
                        "target_kind": "networking_resource",
                    },
                    {
                        "target_external_id": role_arn,
                        "edge_type": "assumes_role",
                        "target_kind": "identity",
                    },
                ],
            }
        ],
    }
    counts = service._store_discovered_resources(org_id, results)
    assert (
        counts["edges_created"] == 4
    )  # sg->vpc, lambda->vpc, lambda->sg, lambda->role
    assert counts["unresolved_edges"] == 0

    # Verify service was created
    func = (
        db(
            (
                db.services.external_id
                == "arn:aws:lambda:us-east-2:123456789012:function:my-func"
            )
            & (db.services.organization_id == org_id)
        )
        .select()
        .first()
    )
    assert func is not None

    # Verify assumes_role edge was created
    assumes_role_dep = (
        db(
            (db.dependencies.source_type == "service")
            & (db.dependencies.source_id == func.id)
            & (db.dependencies.target_type == "identity")
            & (db.dependencies.target_id == identity_id)
            & (db.dependencies.dependency_type == "assumes_role")
        )
        .select()
        .first()
    )
    assert assumes_role_dep is not None


def test_phase_b2_elb_routes_to_targets(seeded):
    """Phase B2: ELB emits routes_to relationships to target instances."""
    service, db, org_id = seeded
    results = {
        "network": [
            {
                "name": "vpc-prod",
                "resource_type": "vpc",
                "resource_id": "vpc-prod",
                "external_id": "vpc-prod",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-prod"},
            },
            {
                "name": "my-lb",
                "resource_type": "load_balancer",
                "resource_id": "arn:aws:elasticloadbalancing:us-east-2:123456789012:loadbalancer/app/my-lb/50dc6c495c0c9188",
                "external_id": "arn:aws:elasticloadbalancing:us-east-2:123456789012:loadbalancer/app/my-lb/50dc6c495c0c9188",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-prod"},
                "relationships": [
                    {
                        "target_external_id": "vpc-prod",
                        "edge_type": "in_network",
                        "target_kind": "networking_resource",
                    },
                    {
                        "target_external_id": "i-web-1",
                        "edge_type": "routes_to",
                        "target_kind": "entity",
                    },
                    {
                        "target_external_id": "i-web-2",
                        "edge_type": "routes_to",
                        "target_kind": "entity",
                    },
                ],
            },
        ],
        "compute": [
            {
                "name": "web-1",
                "resource_type": "ec2_instance",
                "resource_id": "i-web-1",
                "external_id": "i-web-1",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-prod"},
                "relationships": [
                    {
                        "target_external_id": "vpc-prod",
                        "edge_type": "in_network",
                        "target_kind": "networking_resource",
                    }
                ],
            },
            {
                "name": "web-2",
                "resource_type": "ec2_instance",
                "resource_id": "i-web-2",
                "external_id": "i-web-2",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-prod"},
                "relationships": [
                    {
                        "target_external_id": "vpc-prod",
                        "edge_type": "in_network",
                        "target_kind": "networking_resource",
                    }
                ],
            },
        ],
    }
    counts = service._store_discovered_resources(org_id, results)
    assert (
        counts["edges_created"] == 5
    )  # vpc->vpc (dummy), lb->vpc, lb->i1, lb->i2, i1->vpc, i2->vpc
    assert counts["unresolved_edges"] == 0

    # Verify LB was created and registered
    lb = (
        db(
            (
                db.networking_resources.external_id
                == "arn:aws:elasticloadbalancing:us-east-2:123456789012:loadbalancer/app/my-lb/50dc6c495c0c9188"
            )
            & (db.networking_resources.organization_id == org_id)
        )
        .select()
        .first()
    )
    assert lb is not None

    # Verify routes_to edges were created
    web1 = (
        db(
            (db.entities.external_id == "i-web-1")
            & (db.entities.organization_id == org_id)
        )
        .select()
        .first()
    )
    web2 = (
        db(
            (db.entities.external_id == "i-web-2")
            & (db.entities.organization_id == org_id)
        )
        .select()
        .first()
    )

    for web in [web1, web2]:
        routes_to_dep = (
            db(
                (db.dependencies.source_type == "networking_resource")
                & (db.dependencies.source_id == lb.id)
                & (db.dependencies.target_type == "entity")
                & (db.dependencies.target_id == web.id)
                & (db.dependencies.dependency_type == "routes_to")
            )
            .select()
            .first()
        )
        assert routes_to_dep is not None


def test_phase_b2_ec2_assumes_role(seeded):
    """Phase B2: EC2 emits assumes_role relationship to its IAM role."""
    service, db, org_id = seeded

    # Pre-create the IAM role as an identity with a unique username per org
    tenant_id = 1
    now = datetime.now(timezone.utc)
    role_arn = "arn:aws:iam::123456789012:role/ec2-role"
    username = f"aws:123456789012:ec2-role:org{org_id}"  # Make username unique per org

    # Check if identity already exists for this org
    existing = (
        db(
            (db.identities.username == username)
            & (db.identities.organization_id == org_id)
        )
        .select()
        .first()
    )
    if existing:
        identity_id = existing.id
    else:
        identity_id = db.identities.insert(
            tenant_id=tenant_id,
            identity_type="serviceAccount",
            username=username,
            full_name="ec2-role",
            organization_id=org_id,
            auth_provider="aws",
            auth_provider_id=role_arn,
            external_id=role_arn,
            portal_role="observer",
            is_active=True,
            is_superuser=False,
            mfa_enabled=False,
            must_change_password=False,
            created_at=now,
            updated_at=now,
        )

    results = {
        "network": [
            {
                "name": "vpc-prod",
                "resource_type": "vpc",
                "resource_id": "vpc-prod",
                "external_id": "vpc-prod",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-prod"},
            },
        ],
        "compute": [
            {
                "name": "web-server",
                "resource_type": "ec2_instance",
                "resource_id": "i-web",
                "external_id": "i-web",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-prod"},
                "relationships": [
                    {
                        "target_external_id": "vpc-prod",
                        "edge_type": "in_network",
                        "target_kind": "networking_resource",
                    },
                    {
                        "target_external_id": role_arn,
                        "edge_type": "assumes_role",
                        "target_kind": "identity",
                    },
                ],
            }
        ],
    }
    counts = service._store_discovered_resources(org_id, results)
    assert counts["edges_created"] == 2  # ec2->vpc, ec2->role
    assert counts["unresolved_edges"] == 0

    # Verify EC2 assumes_role edge was created
    ec2 = (
        db(
            (db.entities.external_id == "i-web")
            & (db.entities.organization_id == org_id)
        )
        .select()
        .first()
    )
    assert ec2 is not None

    assumes_role_dep = (
        db(
            (db.dependencies.source_type == "entity")
            & (db.dependencies.source_id == ec2.id)
            & (db.dependencies.target_type == "identity")
            & (db.dependencies.target_id == identity_id)
            & (db.dependencies.dependency_type == "assumes_role")
        )
        .select()
        .first()
    )
    assert assumes_role_dep is not None


def test_aws_no_connected_to_for_ec2_in_network(seeded):
    """Regression: AWS EC2 with in_network now, legacy connected_to never created for AWS."""
    service, db, org_id = seeded
    results = {
        "network": [
            {
                "name": "vpc-prod",
                "resource_type": "vpc",
                "resource_id": "vpc-prod",
                "external_id": "vpc-prod",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-prod"},
            },
        ],
        "compute": [
            {
                "name": "web-1",
                "resource_type": "ec2_instance",
                "resource_id": "i-web-1",
                "external_id": "i-web-1",
                "provider": "aws",
                "metadata": {"vpc_id": "vpc-prod"},
                "relationships": [
                    {
                        "target_external_id": "vpc-prod",
                        "edge_type": "in_network",
                        "target_kind": "networking_resource",
                    }
                ],
            }
        ],
    }
    counts = service._store_discovered_resources(org_id, results)
    assert counts["edges_created"] == 1  # ec2->vpc
    assert counts["unresolved_edges"] == 0

    # Verify NO network_entity_mappings row exists for EC2->VPC
    ec2 = (
        db(
            (db.entities.external_id == "i-web-1")
            & (db.entities.organization_id == org_id)
        )
        .select()
        .first()
    )
    vpc = (
        db(
            (db.networking_resources.external_id == "vpc-prod")
            & (db.networking_resources.organization_id == org_id)
        )
        .select()
        .first()
    )

    legacy_mapping = (
        db(
            (db.network_entity_mappings.network_id == vpc.id)
            & (db.network_entity_mappings.entity_id == ec2.id)
            & (db.network_entity_mappings.relationship_type == "connected_to")
        )
        .select()
        .first()
    )
    assert legacy_mapping is None, "AWS should emit in_network, not legacy connected_to"
