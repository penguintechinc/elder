#!/usr/bin/env python3
"""Seed a demo Kubernetes cluster hierarchy + geo-located entities.

Sibling to ``scripts/seed_cloud_discovery.py`` — run alongside it (same demo
tenant/org/admin, resolved via that script's own helpers so there is exactly
one login for all demo data, not a second disconnected identity).

Two things this seeds that the AWS cloud-discovery demo doesn't cover:

1. A realistic Kubernetes cluster hierarchy (cluster -> nodes/pods/deployments)
   so cluster drill-down + a Deployments page have data. ``KubernetesDiscoveryClient``
   (apps/worker/discovery/k8s_discovery.py) only discovers nodes/pods/services/
   PV(C)s/ingress/secrets/service-accounts today — it has no ``discover_deployments()``
   and never emits a ``k8s_deployment`` resource_type. And
   ``DiscoveryService._ensure_provider_root_entity`` (apps/worker/discovery/service.py)
   names the per-scan Kubernetes root entity from a hardcoded
   ``f"{provider} discovery"`` string, not from any per-cluster identity in the
   scan config — so two separate ``_store_discovered_resources()`` calls for two
   *different* clusters would collide onto the SAME "kubernetes discovery" root
   entity instead of producing two distinct ``kubernetes_cluster`` entities. Real
   multi-cluster parenting therefore isn't expressible through the linker as it
   stands today, so cluster/node/pod/deployment entities are written directly via
   the DAL (`_upsert_entity` below) with an explicit ``parent_id`` — the exact
   fallback the seed spec calls out. Namespaces/services/edges still go through
   ``DiscoveryService``'s own helper methods (``_upsert_networking_resource``,
   ``_store_as_service``, ``_link_resources``) — same production code as the AWS
   demo, just not the single ``_store_discovered_resources`` entry point.

2. ~10 geo-located entities (real city coordinates in ``metadata.location``) for
   the upcoming Map view.

Convention (must match — other seed/agent code depends on it): ``entity.tags``
is a ``{key: value}`` dict, and re-running this script MERGES tags/metadata on
an existing entity rather than replacing them wholesale — see ``_upsert_entity``.
Note ``DiscoveryService._store_as_entity`` currently nests discovered resource
tags inside ``metadata["tags"]`` instead of writing the real ``entities.tags``
column (tracked for a fix on feat/k8s-backend); this script bypasses that
method for entities, so it already writes the real ``tags`` column today.

Usage:
    python3 scripts/seed_k8s_geo_demo.py
"""

import importlib.util
import os
import sys
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add parent directory to path (matches scripts/seed_cloud_discovery.py)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from apps.api.main import create_app  # noqa: E402
from apps.worker.discovery.service import DiscoveryService  # noqa: E402

_SEED_CLOUD_DISCOVERY_PATH = Path(__file__).resolve().parent / "seed_cloud_discovery.py"


def _load_seed_cloud_discovery() -> Any:
    """Load scripts/seed_cloud_discovery.py by file path (scripts/ is not a
    package — same loading approach as tests/unit/test_seed_cloud_discovery.py).

    Reused so this script shares that one's tenant/org/admin resolution and
    demo identity constants instead of forking a second copy that could drift.
    """
    spec = importlib.util.spec_from_file_location(
        "seed_cloud_discovery", _SEED_CLOUD_DISCOVERY_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Part A: Kubernetes cluster hierarchy
# ---------------------------------------------------------------------------

CLUSTERS: list[dict[str, Any]] = [
    {
        "name": "prod-use2-eks",
        "region": "us-east-2",
        "tags": {"env": "prod", "team": "platform", "region": "us-east-2"},
        "metadata": {
            "cloud_provider": "aws",
            "distribution": "eks",
            "k8s_version": "1.31",
        },
        "namespaces": ["default", "api", "data"],
        "nodes": [
            {
                "name": "ip-10-42-1-11.us-east-2.compute.internal",
                "labels": {
                    "kubernetes.io/os": "linux",
                    "node.kubernetes.io/instance-type": "m5.xlarge",
                    "topology.kubernetes.io/zone": "us-east-2a",
                },
                "metadata": {
                    "capacity_cpu": "4",
                    "capacity_memory": "16Gi",
                    "kubelet_version": "v1.31.0-eks",
                    "os_image": "Amazon Linux 2023",
                    "architecture": "amd64",
                    "conditions": ["Ready"],
                },
            },
            {
                "name": "ip-10-42-1-12.us-east-2.compute.internal",
                "labels": {
                    "kubernetes.io/os": "linux",
                    "node.kubernetes.io/instance-type": "m5.xlarge",
                    "topology.kubernetes.io/zone": "us-east-2b",
                },
                "metadata": {
                    "capacity_cpu": "4",
                    "capacity_memory": "16Gi",
                    "kubelet_version": "v1.31.0-eks",
                    "os_image": "Amazon Linux 2023",
                    "architecture": "amd64",
                    "conditions": ["Ready"],
                },
            },
            {
                "name": "ip-10-42-1-13.us-east-2.compute.internal",
                "labels": {
                    "kubernetes.io/os": "linux",
                    "node.kubernetes.io/instance-type": "m5.xlarge",
                    "topology.kubernetes.io/zone": "us-east-2c",
                },
                "metadata": {
                    "capacity_cpu": "4",
                    "capacity_memory": "16Gi",
                    "kubelet_version": "v1.31.0-eks",
                    "os_image": "Amazon Linux 2023",
                    "architecture": "amd64",
                    "conditions": ["Ready"],
                },
            },
        ],
        "deployments": [
            {
                "name": "api",
                "namespace": "api",
                "replicas": 3,
                "images": ["ghcr.io/penguintechinc/elder/api:v4.0.0"],
                "labels": {"app": "api", "tier": "backend"},
            },
            {
                "name": "worker",
                "namespace": "data",
                "replicas": 2,
                "images": ["ghcr.io/penguintechinc/elder/worker:v4.0.0"],
                "labels": {"app": "worker", "tier": "backend"},
            },
            {
                "name": "web",
                "namespace": "default",
                "replicas": 2,
                "images": ["ghcr.io/penguintechinc/elder/webui:v4.0.0"],
                "labels": {"app": "web", "tier": "frontend"},
            },
        ],
        "services": [
            {
                "name": "api-svc",
                "namespace": "api",
                "type": "ClusterIP",
                "cluster_ip": "10.100.1.10",
                "ports": [{"port": 8080, "protocol": "TCP"}],
                "labels": {"app": "api"},
            },
            {
                "name": "web-svc",
                "namespace": "default",
                "type": "LoadBalancer",
                "cluster_ip": "10.100.1.20",
                "ports": [{"port": 443, "protocol": "TCP"}],
                "labels": {"app": "web"},
            },
        ],
    },
    {
        "name": "staging-usw1-gke",
        "region": "us-west1",
        "tags": {"env": "staging", "team": "platform", "region": "us-west1"},
        "metadata": {
            "cloud_provider": "gcp",
            "distribution": "gke",
            "k8s_version": "1.30",
        },
        "namespaces": ["default", "api"],
        "nodes": [
            {
                "name": "gke-staging-usw1-default-pool-abc123",
                "labels": {
                    "cloud.google.com/gke-nodepool": "default-pool",
                    "topology.kubernetes.io/zone": "us-west1-a",
                },
                "metadata": {
                    "capacity_cpu": "4",
                    "capacity_memory": "16Gi",
                    "kubelet_version": "v1.30.5-gke.1443001",
                    "os_image": "Container-Optimized OS",
                    "architecture": "amd64",
                    "conditions": ["Ready"],
                },
            },
            {
                "name": "gke-staging-usw1-default-pool-def456",
                "labels": {
                    "cloud.google.com/gke-nodepool": "default-pool",
                    "topology.kubernetes.io/zone": "us-west1-b",
                },
                "metadata": {
                    "capacity_cpu": "4",
                    "capacity_memory": "16Gi",
                    "kubelet_version": "v1.30.5-gke.1443001",
                    "os_image": "Container-Optimized OS",
                    "architecture": "amd64",
                    "conditions": ["Ready"],
                },
            },
        ],
        "deployments": [
            {
                "name": "api",
                "namespace": "api",
                "replicas": 2,
                "images": ["ghcr.io/penguintechinc/elder/api:v4.0.0-rc1"],
                "labels": {"app": "api", "tier": "backend"},
            },
            {
                "name": "web",
                "namespace": "default",
                "replicas": 1,
                "images": ["ghcr.io/penguintechinc/elder/webui:v4.0.0-rc1"],
                "labels": {"app": "web", "tier": "frontend"},
            },
        ],
        "services": [
            {
                "name": "api-svc",
                "namespace": "api",
                "type": "ClusterIP",
                "cluster_ip": "10.4.2.10",
                "ports": [{"port": 8080, "protocol": "TCP"}],
                "labels": {"app": "api"},
            },
        ],
    },
]


def _upsert_entity(
    db: Any,
    organization_id: int,
    name: str,
    entity_type: str,
    sub_type: str,
    tags: dict[str, Any],
    metadata: dict[str, Any],
    parent_id: int | None = None,
    external_id: str | None = None,
    region: str | None = None,
    cloud_provider: str | None = None,
) -> int:
    """Create or update an ``entities`` row with ``tags``/``metadata`` merged
    (not replaced) on re-run, and an explicit ``parent_id``.

    Idempotency key mirrors ``DiscoveryService._store_as_entity``:
    ``(organization_id, sub_type, name)``. Unlike that method, this writes
    ``tags`` to the real column directly (see module docstring) and merges
    both dict fields on update instead of overwriting them wholesale, so a
    re-run never clobbers fields another seed/agent may have added to the
    same entity.
    """
    existing = (
        db(
            (db.entities.organization_id == organization_id)
            & (db.entities.sub_type == sub_type)
            & (db.entities.name == name)
        )
        .select()
        .first()
    )
    now = datetime.now(UTC)

    if existing:
        merged_tags = {**(existing.tags or {}), **tags}
        merged_metadata = {**(existing.metadata or {}), **metadata}
        update_data: dict[str, Any] = {
            "tags": merged_tags,
            "metadata": merged_metadata,
            "updated_at": now,
        }
        if external_id is not None:
            update_data["external_id"] = external_id
        if region is not None:
            update_data["region"] = region
        if cloud_provider is not None:
            update_data["cloud_provider"] = cloud_provider
        if parent_id is not None:
            update_data["parent_id"] = parent_id
        db(db.entities.id == existing.id).update(**update_data)
        return int(existing.id)

    insert_data: dict[str, Any] = {
        "name": name,
        "type": entity_type,
        "sub_type": sub_type,
        "organization_id": organization_id,
        "tags": tags,
        "metadata": metadata,
        "external_id": external_id,
        "region": region,
        "cloud_provider": cloud_provider,
        "created_at": now,
        "updated_at": now,
    }
    if parent_id is not None:
        insert_data["parent_id"] = parent_id
    return int(db.entities.insert(**insert_data))


def _seed_cluster(
    service: DiscoveryService,
    db: Any,
    organization_id: int,
    tenant_id: int,
    spec: dict[str, Any],
) -> dict[str, int]:
    """Seed one cluster's full hierarchy: cluster -> namespaces, nodes, pods,
    deployments, services — all parented (directly or via dependencies edges)
    to the cluster entity.
    """
    cluster_name = spec["name"]
    now_iso = datetime.now(UTC).isoformat()
    cloud_provider = spec["metadata"].get("cloud_provider")

    cluster_id = _upsert_entity(
        db,
        organization_id,
        name=cluster_name,
        entity_type="compute",
        sub_type="kubernetes_cluster",
        tags=spec["tags"],
        metadata={**spec["metadata"], "discovered_at": now_iso},
        region=spec.get("region"),
        cloud_provider=cloud_provider,
        external_id=f"cluster:{cluster_name}",
    )

    # Namespaces -> networking_resources, dual-linked to the cluster entity.
    # Same DiscoveryService helper _ensure_intermediate_networking uses for
    # AWS VPCs / K8s namespaces, so the shape matches production exactly.
    namespace_net_ids: dict[str, int | None] = {}
    for ns_name in spec["namespaces"]:
        net_id = service._upsert_networking_resource(
            organization_id=organization_id,
            name=ns_name,
            network_type="namespace",
            region=None,
            attributes={"provider": "kubernetes", "cluster": cluster_name},
            tags=["kubernetes", "namespace", "discovered"],
            external_id=f"{cluster_name}:{ns_name}",
        )
        if net_id:
            service._upsert_network_entity_mapping(net_id, cluster_id, "attached")
        namespace_net_ids[ns_name] = net_id

    # Nodes -> entities, parent_id=cluster.
    node_ids: list[int] = []
    node_names: list[str] = []
    for node_spec in spec["nodes"]:
        node_id = _upsert_entity(
            db,
            organization_id,
            name=node_spec["name"],
            entity_type="compute",
            sub_type="k8s_node",
            tags=node_spec["labels"],
            metadata=node_spec["metadata"],
            parent_id=cluster_id,
            region=spec.get("region"),
            cloud_provider=cloud_provider,
            external_id=f"{cluster_name}:node:{node_spec['name']}",
        )
        node_ids.append(node_id)
        node_names.append(node_spec["name"])

    # Deployments -> entities, parent_id=cluster. Pods -> entities,
    # parent_id=cluster (per spec: pods parent directly to the cluster, not
    # nested under the deployment entity) + semantic edges via the
    # dependencies table using the already-canonical runs_on/runs_in/manages
    # edge types (CANONICAL_EDGE_TYPES reserves them; no discovery client
    # emits them yet, so this seed is the first data to exercise them).
    deployment_count = 0
    pod_count = 0
    for dep_spec in spec["deployments"]:
        dep_id = _upsert_entity(
            db,
            organization_id,
            name=dep_spec["name"],
            entity_type="compute",
            sub_type="k8s_deployment",
            tags=dep_spec["labels"],
            metadata={
                "namespace": dep_spec["namespace"],
                "replicas": dep_spec["replicas"],
                "available_replicas": dep_spec["replicas"],
                "images": dep_spec["images"],
                "selector": dep_spec["labels"],
            },
            parent_id=cluster_id,
            region=spec.get("region"),
            cloud_provider=cloud_provider,
            external_id=(
                f"{cluster_name}:deployment:{dep_spec['namespace']}:{dep_spec['name']}"
            ),
        )
        deployment_count += 1

        for pod_index in range(dep_spec["replicas"]):
            pod_name = f"{dep_spec['name']}-{pod_index + 1}"
            node_id = node_ids[pod_count % len(node_ids)]
            node_name = node_names[pod_count % len(node_names)]
            pod_id = _upsert_entity(
                db,
                organization_id,
                name=pod_name,
                entity_type="compute",
                sub_type="k8s_pod",
                tags=dep_spec["labels"],
                metadata={
                    "namespace": dep_spec["namespace"],
                    "node_name": node_name,
                    "phase": "Running",
                    "containers": [
                        {"name": dep_spec["name"], "image": image}
                        for image in dep_spec["images"]
                    ],
                },
                parent_id=cluster_id,
                region=spec.get("region"),
                cloud_provider=cloud_provider,
                external_id=(f"{cluster_name}:pod:{dep_spec['namespace']}:{pod_name}"),
            )
            pod_count += 1

            service._link_resources(
                ("entity", pod_id), ("entity", node_id), "runs_on", tenant_id
            )
            service._link_resources(
                ("entity", dep_id), ("entity", pod_id), "manages", tenant_id
            )
            ns_net_id = namespace_net_ids.get(dep_spec["namespace"])
            if ns_net_id:
                service._link_resources(
                    ("entity", pod_id),
                    ("networking_resource", ns_net_id),
                    "runs_in",
                    tenant_id,
                )

    # Services -> services table (deployment_method="kubernetes" for
    # resource_type "k8s_service" — DiscoveryService._store_as_service),
    # linked back to the cluster entity.
    service_count = 0
    for svc_spec in spec["services"]:
        resource_id = f"{cluster_name}:svc:{svc_spec['namespace']}:{svc_spec['name']}"
        resource = {
            "resource_id": resource_id,
            "resource_type": "k8s_service",
            "name": svc_spec["name"],
            "provider": "kubernetes",
            "metadata": {
                "namespace": svc_spec["namespace"],
                "type": svc_spec["type"],
                "cluster_ip": svc_spec["cluster_ip"],
                "ports": svc_spec["ports"],
            },
            "tags": svc_spec["labels"],
            "external_id": resource_id,
        }
        svc_id = service._store_as_service(organization_id, resource, "kubernetes")
        if svc_id:
            service_count += 1
            service._create_dependency_link(
                "service",
                svc_id,
                "entity",
                cluster_id,
                tenant_id,
                "discovered_from",
                {"provider": "kubernetes", "cluster": cluster_name},
            )

    return {
        "cluster_id": cluster_id,
        "nodes": len(node_ids),
        "deployments": deployment_count,
        "pods": pod_count,
        "namespaces": len(namespace_net_ids),
        "services": service_count,
    }


# ---------------------------------------------------------------------------
# Part B: geo-located entities (for the Map view)
# ---------------------------------------------------------------------------

GEO_LOCATIONS: list[dict[str, Any]] = [
    {
        "name": "Ashburn DC1",
        "sub_type": "data_center",
        "city": "Ashburn",
        "state": "VA",
        "country": "United States",
        "latitude": 39.0438,
        "longitude": -77.4874,
        "tags": {"site_type": "data_center", "provider": "aws", "tier": "primary"},
    },
    {
        "name": "San Francisco HQ",
        "sub_type": "office",
        "city": "San Francisco",
        "state": "CA",
        "country": "United States",
        "latitude": 37.7749,
        "longitude": -122.4194,
        "tags": {"site_type": "office", "role": "headquarters"},
    },
    {
        "name": "London DC2",
        "sub_type": "data_center",
        "city": "London",
        "state": None,
        "country": "United Kingdom",
        "latitude": 51.5074,
        "longitude": -0.1278,
        "tags": {"site_type": "data_center", "provider": "azure", "tier": "secondary"},
    },
    {
        "name": "Frankfurt DC3",
        "sub_type": "data_center",
        "city": "Frankfurt",
        "state": None,
        "country": "Germany",
        "latitude": 50.1109,
        "longitude": 8.6821,
        "tags": {"site_type": "data_center", "provider": "gcp", "tier": "secondary"},
    },
    {
        "name": "Tokyo Edge",
        "sub_type": "edge_site",
        "city": "Tokyo",
        "state": None,
        "country": "Japan",
        "latitude": 35.6762,
        "longitude": 139.6503,
        "tags": {"site_type": "edge_site", "provider": "cloudflare"},
    },
    {
        "name": "Sydney Office",
        "sub_type": "office",
        "city": "Sydney",
        "state": "NSW",
        "country": "Australia",
        "latitude": -33.8688,
        "longitude": 151.2093,
        "tags": {"site_type": "office", "role": "regional"},
    },
    {
        "name": "São Paulo Edge",
        "sub_type": "edge_site",
        "city": "São Paulo",
        "state": "SP",
        "country": "Brazil",
        "latitude": -23.5505,
        "longitude": -46.6333,
        "tags": {"site_type": "edge_site", "provider": "cloudflare"},
    },
    {
        "name": "Singapore DC4",
        "sub_type": "data_center",
        "city": "Singapore",
        "state": None,
        "country": "Singapore",
        "latitude": 1.3521,
        "longitude": 103.8198,
        "tags": {"site_type": "data_center", "provider": "aws", "tier": "secondary"},
    },
    {
        "name": "Mumbai Edge",
        "sub_type": "edge_site",
        "city": "Mumbai",
        "state": "MH",
        "country": "India",
        "latitude": 19.0760,
        "longitude": 72.8777,
        "tags": {"site_type": "edge_site", "provider": "fastly"},
    },
    {
        "name": "Toronto Office",
        "sub_type": "office",
        "city": "Toronto",
        "state": "ON",
        "country": "Canada",
        "latitude": 43.6532,
        "longitude": -79.3832,
        "tags": {"site_type": "office", "role": "regional"},
    },
]


def _seed_geo_entities(db: Any, organization_id: int) -> int:
    """Seed standalone geo-located entities (no parent) for the Map view.

    ``metadata.location`` is a flat sub-object with real city coordinates —
    the shape a future Map endpoint would query.
    """
    count = 0
    for loc in GEO_LOCATIONS:
        _upsert_entity(
            db,
            organization_id,
            name=loc["name"],
            entity_type="datacenter",
            sub_type=loc["sub_type"],
            tags=loc["tags"],
            metadata={
                "location": {
                    "city": loc["city"],
                    "state": loc["state"],
                    "country": loc["country"],
                    "latitude": loc["latitude"],
                    "longitude": loc["longitude"],
                },
                "discovered_at": datetime.now(UTC).isoformat(),
            },
            external_id=f"geo:{loc['name']}",
        )
        count += 1
    return count


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def seed_k8s_geo_demo() -> None:
    """Seed the demo K8s cluster hierarchy + geo-located entities."""
    print("Seeding demo Kubernetes cluster hierarchy + geo-located entities...")

    # See seed_cloud_discovery.py for why this isn't `with app.app_context():`.
    app = create_app()
    db = app.db

    cd = _load_seed_cloud_discovery()
    tenant_id = cd._resolve_or_create_demo_tenant(db)
    organization_id = cd._resolve_or_create_demo_org(db, tenant_id)
    cd._resolve_or_create_demo_admin(db, tenant_id)

    service = DiscoveryService(db)

    totals = {
        "clusters": 0,
        "nodes": 0,
        "deployments": 0,
        "pods": 0,
        "namespaces": 0,
        "services": 0,
    }
    cluster_ids: dict[str, int] = {}
    for spec in CLUSTERS:
        counts = _seed_cluster(service, db, organization_id, tenant_id, spec)
        cluster_ids[spec["name"]] = counts["cluster_id"]
        totals["clusters"] += 1
        for key in ("nodes", "deployments", "pods", "namespaces", "services"):
            totals[key] += counts[key]

    geo_count = _seed_geo_entities(db, organization_id)
    db.commit()

    print("\nKubernetes cluster hierarchy seeded:")
    print(
        f"  clusters={totals['clusters']} nodes={totals['nodes']} "
        f"deployments={totals['deployments']} pods={totals['pods']} "
        f"namespaces={totals['namespaces']} services={totals['services']}"
    )
    print(f"\nGeo-located entities seeded: {geo_count}")

    # Verify parent_id linkage the same way a cluster drill-down UI would
    # query it: entities WHERE parent_id=<cluster_id> AND sub_type IN
    # (k8s_node, k8s_pod, k8s_deployment).
    print("\nParent_id verification (drill-down query):")
    linkage_ok = True
    for cluster_name, cluster_id in cluster_ids.items():
        children = db(
            (db.entities.parent_id == cluster_id)
            & (db.entities.sub_type.belongs(["k8s_node", "k8s_pod", "k8s_deployment"]))
        ).select()
        print(f"  cluster={cluster_name!r} id={cluster_id} children={len(children)}")
        if len(children) == 0:
            linkage_ok = False

    print("\n" + "=" * 72)
    print("Seeded into the SAME demo tenant/org as seed_cloud_discovery.py:")
    print(f"  tenant_id={tenant_id} organization_id={organization_id}")
    print(f"  login email  : {cd.DEMO_ADMIN_USERNAME}")
    print(
        "  Run scripts/seed_cloud_discovery.py first (or alongside) — it owns "
        "admin creation and prints the password on first run."
    )
    print("=" * 72)

    if not linkage_ok:
        print(
            "\nERROR: at least one cluster has zero parent_id-linked children — "
            "drill-down would show an empty cluster.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    try:
        seed_k8s_geo_demo()
    except Exception as e:  # noqa: BLE001 - top-level script error boundary
        print(f"Error seeding k8s/geo demo data: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
