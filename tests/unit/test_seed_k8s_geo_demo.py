"""Tests for scripts/seed_k8s_geo_demo.py's cluster hierarchy + geo entities.

Loads the script by file path (scripts/ is not a package) — same approach as
tests/unit/test_seed_cloud_discovery.py — and runs it against the real test
DB to prove the parent_id linkage the cluster drill-down UI depends on, and
that geo entities carry the metadata.location shape the Map view needs.
"""

import importlib.util
import uuid
from pathlib import Path
from typing import Any

import pytest

from apps.worker.discovery.service import DiscoveryService

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "seed_k8s_geo_demo.py"


def _load_seed_script() -> Any:
    """Load scripts/seed_k8s_geo_demo.py by file path (not a package)."""
    spec = importlib.util.spec_from_file_location("seed_k8s_geo_demo", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed_k8s_geo_demo = _load_seed_script()


@pytest.fixture
def seeded(app):
    """A DiscoveryService on the test DAL with one tenant + organization.

    Per-invocation unique tenant slug (same pattern as
    tests/unit/test_discovery_linker.py's `seeded` fixture) since the test DB
    is session-persistent with no per-test rollback.
    """
    db = app.db
    tenant_id = db.tenants.insert(
        name="Seed K8s/Geo Test Tenant",
        slug=f"seed-k8s-geo-test-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    org_id = db.organizations.insert(name="Seed K8s/Geo Test Org", tenant_id=tenant_id)
    db.commit()
    yield DiscoveryService(db), db, org_id, tenant_id


def test_clusters_and_geo_locations_shape():
    """Sanity-check the canned cluster/geo specs before they hit the DB."""
    assert len(seed_k8s_geo_demo.CLUSTERS) in (1, 2)
    for cluster in seed_k8s_geo_demo.CLUSTERS:
        assert cluster["nodes"], f"{cluster['name']} has no nodes"
        assert cluster["deployments"], f"{cluster['name']} has no deployments"
        assert cluster["namespaces"], f"{cluster['name']} has no namespaces"

    assert len(seed_k8s_geo_demo.GEO_LOCATIONS) == 10
    for loc in seed_k8s_geo_demo.GEO_LOCATIONS:
        assert -90 <= loc["latitude"] <= 90
        assert -180 <= loc["longitude"] <= 180
        assert loc["sub_type"] in ("data_center", "office", "edge_site")


def _entity(db, org_id, external_id):
    return (
        db(
            (db.entities.external_id == external_id)
            & (db.entities.organization_id == org_id)
        )
        .select()
        .first()
    )


def test_cluster_children_are_parented_to_the_cluster_entity(seeded):
    """The exact query a cluster drill-down UI would run: entities WHERE
    parent_id=<cluster_id> AND sub_type IN (k8s_node, k8s_pod, k8s_deployment)
    must return every node/pod/deployment seeded for that cluster."""
    service, db, org_id, tenant_id = seeded

    for spec in seed_k8s_geo_demo.CLUSTERS:
        counts = seed_k8s_geo_demo._seed_cluster(service, db, org_id, tenant_id, spec)
        db.commit()

        cluster = _entity(db, org_id, f"cluster:{spec['name']}")
        assert cluster is not None
        assert cluster.sub_type == "kubernetes_cluster"
        assert cluster.tags == spec["tags"]

        children = db(
            (db.entities.parent_id == cluster.id)
            & (db.entities.sub_type.belongs(["k8s_node", "k8s_pod", "k8s_deployment"]))
        ).select()
        assert len(children) == counts["nodes"] + counts["pods"] + counts["deployments"]
        assert counts["nodes"] == len(spec["nodes"])
        assert counts["deployments"] == len(spec["deployments"])
        assert counts["pods"] == sum(d["replicas"] for d in spec["deployments"])

        # Every child's tags landed in the real `tags` column (dict), not
        # nested inside metadata.
        for child in children:
            assert isinstance(child.tags, dict) and child.tags


def test_seed_cluster_is_idempotent(seeded):
    """Re-running the same cluster spec must not duplicate entities."""
    service, db, org_id, tenant_id = seeded
    spec = seed_k8s_geo_demo.CLUSTERS[0]

    first = seed_k8s_geo_demo._seed_cluster(service, db, org_id, tenant_id, spec)
    db.commit()
    second = seed_k8s_geo_demo._seed_cluster(service, db, org_id, tenant_id, spec)
    db.commit()

    assert first["cluster_id"] == second["cluster_id"]

    cluster_rows = db(
        (db.entities.organization_id == org_id)
        & (db.entities.sub_type == "kubernetes_cluster")
        & (db.entities.name == spec["name"])
    ).select()
    assert len(cluster_rows) == 1

    node_rows = db(
        (db.entities.organization_id == org_id)
        & (db.entities.sub_type == "k8s_node")
        & (db.entities.parent_id == first["cluster_id"])
    ).select()
    assert len(node_rows) == len(spec["nodes"])


def test_seed_geo_entities_creates_expected_location_metadata(seeded):
    """Geo entities carry metadata.location with real lat/long + tags dict."""
    _, db, org_id, _ = seeded

    count = seed_k8s_geo_demo._seed_geo_entities(db, org_id)
    db.commit()
    assert count == len(seed_k8s_geo_demo.GEO_LOCATIONS)

    for loc in seed_k8s_geo_demo.GEO_LOCATIONS:
        entity = _entity(db, org_id, f"geo:{loc['name']}")
        assert entity is not None
        assert entity.type == "datacenter"
        assert entity.sub_type == loc["sub_type"]
        assert entity.tags == loc["tags"]
        location = entity.metadata["location"]
        assert location["city"] == loc["city"]
        assert location["latitude"] == loc["latitude"]
        assert location["longitude"] == loc["longitude"]
        assert entity.parent_id is None


def test_upsert_entity_merges_tags_and_metadata_on_rerun(seeded):
    """Re-running with an overlapping-but-different tags/metadata dict must
    merge, not clobber, existing fields — the stated convention other seed
    scripts depend on."""
    _, db, org_id, _ = seeded

    first_id = seed_k8s_geo_demo._upsert_entity(
        db,
        org_id,
        name="merge-test",
        entity_type="compute",
        sub_type="k8s_node",
        tags={"env": "prod"},
        metadata={"a": 1},
    )
    db.commit()

    second_id = seed_k8s_geo_demo._upsert_entity(
        db,
        org_id,
        name="merge-test",
        entity_type="compute",
        sub_type="k8s_node",
        tags={"team": "platform"},
        metadata={"b": 2},
    )
    db.commit()

    assert first_id == second_id
    row = db.entities[first_id]
    assert row.tags == {"env": "prod", "team": "platform"}
    assert row.metadata == {"a": 1, "b": 2}
