"""Infrastructure demo data: compute, networks, IPAM, data stores, services."""

from __future__ import annotations

from .common import SeedContext, get_or_create, now

# LXD containers/VMs use the same sub_types the LXD connector emits
# (apps/worker/connectors/lxd_connector.py) so Compute/Map filters match.
COMPUTE = (
    ("LXD: acme-web-01", "lxd_container", "lxd", "10.20.1.11", "running"),
    ("LXD: acme-api-01", "lxd_container", "lxd", "10.20.2.21", "running"),
    ("LXD: acme-db-vm-01", "lxd_vm", "lxd", "10.20.2.31", "running"),
    ("vmware: acme-build-01", "virtual_machine", "vmware", "10.20.9.41", "stopped"),
)

NETWORKS = (
    ("Acme Prod VPC", "vpc", "10.20.0.0/16"),
    ("Web Subnet", "subnet", "10.20.1.0/24"),
    ("App Subnet", "subnet", "10.20.2.0/24"),
    ("Management VLAN", "vlan", "10.20.9.0/24"),
)

VLANS = ((110, "web"), (120, "app"), (190, "management"))
ADDRESSES = (
    ("10.20.1.11/24", "acme-web-01.acme.example.com", 1),
    ("10.20.2.21/24", "acme-api-01.acme.example.com", 2),
    ("10.20.2.31/24", "acme-db-vm-01.acme.example.com", 2),
    ("10.20.9.41/24", "acme-build-01.acme.example.com", 3),
)

DATA_STORES = (
    ("Orders Database", "postgresql", "self-hosted", "confidential", True),
    ("Build Artifacts Bucket", "object_storage", "aws_s3", "internal", False),
    ("Analytics Warehouse", "warehouse", "aws_redshift", "confidential", False),
)

SERVICES = (
    ("Acme Storefront", "TypeScript", 443, True, "storefront.acme.example.com"),
    ("Acme Orders API", "Python", 8443, False, "orders.acme.example.com"),
    ("Acme Billing Worker", "Rust", 9443, False, "billing.acme.example.com"),
    ("Acme Status Page", "Python", 443, True, "status.acme.example.com"),
)

SOFTWARE = (
    ("PostgreSQL", "open-source", "PostgreSQL Global Development Group", "17.6", 0),
    ("Grafana Enterprise", "commercial", "Grafana Labs", "11.2", 25),
    ("HashiCorp Vault", "commercial", "HashiCorp", "1.17", 10),
    ("Elder Agent", "internal", "Acme Corp", "4.0.0", 0),
)


def seed_compute(ctx: SeedContext) -> list[int]:
    """Seed 4 compute entities: 2 LXD containers, 1 LXD VM, 1 VMware VM."""
    ids = []
    for name, sub_type, runtime, ip, status in COMPUTE:
        ids.append(
            get_or_create(
                ctx,
                "entities",
                {"organization_id": ctx.org_id, "name": name, "type": "compute"},
                {
                    "sub_type": sub_type,
                    "status": status,
                    "is_managed": True,
                    "tags": {
                        "runtime": runtime,
                        "env": "production",
                        "owner": "platform",
                    },
                    "metadata": {
                        "ip_address": ip,
                        "os": "Ubuntu 24.04",
                        "arch": "x86_64",
                    },
                    "created_at": now(),
                    "updated_at": now(),
                },
                feature="compute (LXD/VM)",
            )
        )
    return ids


def seed_networks(ctx: SeedContext) -> list[int]:
    """Seed 4 networking resources (VPC, 2 subnets, VLAN)."""
    ids = []
    for name, kind, cidr in NETWORKS:
        ids.append(
            get_or_create(
                ctx,
                "networking_resources",
                {"organization_id": ctx.org_id, "name": name, "network_type": kind},
                {
                    "description": f"{name} ({cidr})",
                    "region": "us-east-2",
                    "attributes": {"cidr": cidr},
                    "tags": {"env": "production"},
                    "is_active": True,
                    "created_at": now(),
                    "updated_at": now(),
                },
                feature="networks",
            )
        )
    return ids


def seed_ipam(ctx: SeedContext) -> None:
    """Seed IPAM: 3 prefixes, 3 VLANs and 4 addresses (3-4 per feature)."""
    pool = get_or_create(
        ctx,
        "ipam_prefixes",
        {"tenant_id": ctx.tenant_id, "prefix": "10.20.0.0/16"},
        {
            "organization_id": ctx.org_id,
            "is_pool": True,
            "description": "Acme private address pool",
            "status": "active",
            "created_at": now(),
            "updated_at": now(),
        },
        feature="ipam prefixes",
    )
    prefix_ids = [pool]
    for cidr, desc in (("10.20.1.0/24", "Web tier"), ("10.20.2.0/24", "App tier")):
        prefix_ids.append(
            get_or_create(
                ctx,
                "ipam_prefixes",
                {"tenant_id": ctx.tenant_id, "prefix": cidr},
                {
                    "organization_id": ctx.org_id,
                    "parent_id": pool,
                    "is_pool": False,
                    "description": desc,
                    "status": "active",
                    "created_at": now(),
                    "updated_at": now(),
                },
                feature="ipam prefixes",
            )
        )
    for vid, name in VLANS:
        get_or_create(
            ctx,
            "ipam_vlans",
            {"tenant_id": ctx.tenant_id, "vid": vid, "name": name},
            {
                "organization_id": ctx.org_id,
                "status": "active",
                "created_at": now(),
                "updated_at": now(),
            },
            feature="ipam vlans",
        )
    for address, dns_name, which in ADDRESSES:
        get_or_create(
            ctx,
            "ipam_addresses",
            {"tenant_id": ctx.tenant_id, "address": address},
            {
                "prefix_id": prefix_ids[min(which, len(prefix_ids) - 1)],
                "dns_name": dns_name,
                "status": "active",
                "created_at": now(),
                "updated_at": now(),
            },
            feature="ipam addresses",
        )


def seed_data_stores(ctx: SeedContext) -> None:
    """Seed 3 data stores with encryption/classification metadata."""
    for name, kind, provider, classification, pii in DATA_STORES:
        get_or_create(
            ctx,
            "data_stores",
            {"tenant_id": ctx.tenant_id, "name": name},
            {
                "organization_id": ctx.org_id,
                "storage_type": kind,
                "storage_provider": provider,
                "location_region": "us-east-2",
                "data_classification": classification,
                "encryption_at_rest": True,
                "encryption_in_transit": True,
                "backup_enabled": True,
                "contains_pii": pii,
                "is_active": True,
                "created_at": now(),
                "updated_at": now(),
            },
            feature="data stores",
        )


def seed_services(ctx: SeedContext) -> dict[str, int]:
    """Seed 4 services and 4 dependency edges between them."""
    ids: dict[str, int] = {}
    for name, lang, port, public, domain in SERVICES:
        ids[name] = get_or_create(
            ctx,
            "services",
            {"organization_id": ctx.org_id, "name": name},
            {
                "is_public": public,
                "domains": [domain],
                "language": lang,
                "deployment_type": "containerized",
                "port": port,
                "health_endpoint": "/healthz",
                "status": "active",
                "tags": {"env": "production"},
                "created_at": now(),
                "updated_at": now(),
            },
            feature="services",
        )
    edges = (
        ("Acme Storefront", "Acme Orders API"),
        ("Acme Orders API", "Acme Billing Worker"),
        ("Acme Status Page", "Acme Orders API"),
        ("Acme Billing Worker", "Acme Orders API"),
    )
    for src, dst in edges:
        get_or_create(
            ctx,
            "dependencies",
            {
                "source_type": "service",
                "source_id": ids[src],
                "target_type": "service",
                "target_id": ids[dst],
            },
            {
                "tenant_id": ctx.tenant_id,
                "dependency_type": "depends_on",
                "created_at": now(),
                "updated_at": now(),
            },
            feature="service dependencies",
        )
    return ids


def seed_software(ctx: SeedContext) -> dict[str, int]:
    """Seed 4 software products with vendor/version/seat data."""
    ids: dict[str, int] = {}
    for name, kind, vendor, version, seats in SOFTWARE:
        ids[name] = get_or_create(
            ctx,
            "software",
            {"organization_id": ctx.org_id, "name": name},
            {
                "software_type": kind,
                "vendor": vendor,
                "version": version,
                "seats": seats,
                "is_active": True,
                "tags": {"env": "production"},
                "created_at": now(),
                "updated_at": now(),
            },
            feature="software",
        )
    return ids
