#!/usr/bin/env python3
"""Seed a demo AWS cloud-discovery topology using the real discovery linker.

Builds a canned ``discovery_results`` payload shaped exactly like
``AWSDiscoveryClient.discover_all()`` output (see
apps/worker/discovery/aws_discovery.py) and feeds it through
``DiscoveryService._store_discovered_resources()`` — the same production
code path a live AWS scan uses — so the Graph/Topology views render real
relationship edges (VPC/subnet/security-group/EC2/EBS/RDS/ELB/Lambda/IAM)
without ever calling AWS. Idempotent: safe to re-run.

The topology is seeded into a dedicated, clearly-named "Demo Cloud
Discovery" tenant/organization/admin identity rather than whatever tenant
happens to own the deployment's default admin user. ``/graph/map`` is
strictly tenant-scoped (gh-189), so logging in as anyone outside this
tenant will show an empty graph — the printed summary at the end of this
script is the single source of truth for which login to use.

Usage:
    python3 scripts/seed_cloud_discovery.py
"""

import os
import secrets
import sys
from datetime import datetime, timezone
from typing import Any, Dict

# Add parent directory to path (matches scripts/seed_access_reviews.py)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from werkzeug.security import generate_password_hash  # noqa: E402

from apps.api.main import create_app  # noqa: E402
from apps.worker.discovery.service import DiscoveryService  # noqa: E402

# Demo tenant/org/admin identity — overridable via env (committed defaults are
# not secrets). Lets the seed target a differently-named tenant/admin without
# code edits (e.g. from `docker run -e ...` or a k8s Job's env).
DEMO_TENANT_SLUG = os.environ.get("DEMO_TENANT_SLUG", "demo-cloud-discovery")
DEMO_TENANT_NAME = os.environ.get("DEMO_TENANT_NAME", "Demo Cloud Discovery")
DEMO_ORG_NAME = os.environ.get("DEMO_ORG_NAME", "Demo Cloud Discovery Org")
DEMO_ADMIN_USERNAME = os.environ.get("DEMO_ADMIN_USERNAME", "demo-admin@elderrms.app")
# Demo-only credential — read from the environment so no secret literal is
# committed (satisfies secret scanning + the no-hardcoded-credentials rule).
# If DEMO_ADMIN_PASSWORD is unset a random one is generated and printed at the
# end of the run; set it to pin a stable password across re-runs / redeploys.
DEMO_ADMIN_PASSWORD = os.environ.get("DEMO_ADMIN_PASSWORD") or secrets.token_urlsafe(12)

# AWS identity for the synthetic topology — overridable so the demo can mirror a
# real account/region. The ARNs below derive from these two automatically.
AWS_ACCOUNT_ID = os.environ.get("DEMO_AWS_ACCOUNT_ID", "123456789012")
AWS_REGION = os.environ.get("DEMO_AWS_REGION", "us-east-2")

VPC_ID = "vpc-demo0001"
SUBNET_WEB = "subnet-demo0a01"
SUBNET_APP = "subnet-demo0b01"
SG_WEB = "sg-demo0web01"
SG_APP = "sg-demo0app01"
EC2_WEB = "i-demo0web001"
EC2_APP = "i-demo0app001"
EBS_VOL = "vol-demo0001"
IAM_ROLE_ARN = f"arn:aws:iam::{AWS_ACCOUNT_ID}:role/demo-ec2-lambda-role"
RDS_RESOURCE_ID = "demo-prod-db"
RDS_ARN = f"arn:aws:rds:{AWS_REGION}:{AWS_ACCOUNT_ID}:db:{RDS_RESOURCE_ID}"
ELB_ARN = (
    f"arn:aws:elasticloadbalancing:{AWS_REGION}:{AWS_ACCOUNT_ID}:"
    "loadbalancer/app/demo-lb/50dc6c495c0c9188"
)
LAMBDA_ARN = f"arn:aws:lambda:{AWS_REGION}:{AWS_ACCOUNT_ID}:function:demo-processor"


def build_discovery_results() -> Dict[str, Any]:
    """Build a canned AWS ``discover_all()``-shaped discovery_results payload.

    Mirrors the real ``AWSDiscoveryClient`` category keys ("compute",
    "storage", "network", "database", "serverless", "iam") and the exact
    per-resource-type metadata/relationship shapes it emits, so this exercises
    the identical code path a live AWS scan drives in
    ``DiscoveryService._store_discovered_resources``. Every relationship's
    ``target_external_id`` resolves to another resource's own
    ``external_id``/``resource_id`` in this same payload — no unresolved
    edges.

    Topology: 1 VPC, 2 subnets, 2 security groups, 2 EC2 instances, 1 IAM
    role, 1 EBS volume, 1 RDS instance, 1 ELB (routing to the web EC2
    instance), 1 Lambda function. Covers in_network, in_subnet,
    uses_security_group, attached_to, routes_to, and assumes_role edges
    (plus the linker's automatic discovered_from edges to the provider
    root entity).
    """
    now = datetime.now(timezone.utc)

    network = [
        {
            "resource_id": VPC_ID,
            "resource_type": "vpc",
            "name": "demo-vpc",
            "provider": "aws",
            "metadata": {
                "cidr_block": "10.42.0.0/16",
                "state": "available",
                "is_default": False,
            },
            "region": AWS_REGION,
            "tags": {"Name": "demo-vpc", "Environment": "demo"},
            "external_id": VPC_ID,
            "relationships": [],
        },
        {
            "resource_id": SUBNET_WEB,
            "resource_type": "subnet",
            "name": "demo-subnet-web",
            "provider": "aws",
            "metadata": {
                "vpc_id": VPC_ID,
                "cidr_block": "10.42.1.0/24",
                "availability_zone": f"{AWS_REGION}a",
                "available_ip_addresses": 250,
            },
            "region": AWS_REGION,
            "tags": {"Name": "demo-subnet-web", "Environment": "demo"},
            "external_id": SUBNET_WEB,
            "relationships": [
                {
                    "target_external_id": VPC_ID,
                    "target_kind": "networking_resource",
                    "edge_type": "in_network",
                }
            ],
        },
        {
            "resource_id": SUBNET_APP,
            "resource_type": "subnet",
            "name": "demo-subnet-app",
            "provider": "aws",
            "metadata": {
                "vpc_id": VPC_ID,
                "cidr_block": "10.42.2.0/24",
                "availability_zone": f"{AWS_REGION}b",
                "available_ip_addresses": 250,
            },
            "region": AWS_REGION,
            "tags": {"Name": "demo-subnet-app", "Environment": "demo"},
            "external_id": SUBNET_APP,
            "relationships": [
                {
                    "target_external_id": VPC_ID,
                    "target_kind": "networking_resource",
                    "edge_type": "in_network",
                }
            ],
        },
        {
            "resource_id": SG_WEB,
            "resource_type": "security_group",
            "name": "demo-sg-web",
            "provider": "aws",
            "metadata": {
                "group_name": "demo-sg-web",
                "vpc_id": VPC_ID,
                "description": "Demo web tier ingress",
                "ingress_rules": 2,
                "egress_rules": 1,
            },
            "region": AWS_REGION,
            "tags": {"Environment": "demo"},
            "external_id": SG_WEB,
            "relationships": [
                {
                    "target_external_id": VPC_ID,
                    "target_kind": "networking_resource",
                    "edge_type": "in_network",
                }
            ],
        },
        {
            "resource_id": SG_APP,
            "resource_type": "security_group",
            "name": "demo-sg-app",
            "provider": "aws",
            "metadata": {
                "group_name": "demo-sg-app",
                "vpc_id": VPC_ID,
                "description": "Demo app/data tier ingress",
                "ingress_rules": 3,
                "egress_rules": 1,
            },
            "region": AWS_REGION,
            "tags": {"Environment": "demo"},
            "external_id": SG_APP,
            "relationships": [
                {
                    "target_external_id": VPC_ID,
                    "target_kind": "networking_resource",
                    "edge_type": "in_network",
                }
            ],
        },
        {
            "resource_id": ELB_ARN,
            "resource_type": "load_balancer",
            "name": "demo-lb",
            "provider": "aws",
            "metadata": {
                "type": "application",
                "scheme": "internet-facing",
                "vpc_id": VPC_ID,
                "state": "active",
                "dns_name": "demo-lb-123456789.us-east-2.elb.amazonaws.com",
            },
            "region": AWS_REGION,
            "tags": {"Environment": "demo"},
            "external_id": ELB_ARN,
            "relationships": [
                {
                    "target_external_id": VPC_ID,
                    "target_kind": "networking_resource",
                    "edge_type": "in_network",
                },
                {
                    "target_external_id": EC2_WEB,
                    "target_kind": "entity",
                    "edge_type": "routes_to",
                },
            ],
        },
    ]

    compute = [
        {
            "resource_id": EC2_WEB,
            "resource_type": "ec2_instance",
            "name": "demo-web-1",
            "provider": "aws",
            "metadata": {
                "instance_type": "t3.micro",
                "state": "running",
                "private_ip": "10.42.1.10",
                "public_ip": "203.0.113.10",
                "vpc_id": VPC_ID,
                "subnet_id": SUBNET_WEB,
                "launch_time": now.isoformat(),
            },
            "region": AWS_REGION,
            "tags": {"Name": "demo-web-1", "Environment": "demo"},
            "external_id": EC2_WEB,
            "relationships": [
                {
                    "target_external_id": VPC_ID,
                    "target_kind": "networking_resource",
                    "edge_type": "in_network",
                },
                {
                    "target_external_id": SUBNET_WEB,
                    "target_kind": "networking_resource",
                    "edge_type": "in_subnet",
                },
                {
                    "target_external_id": SG_WEB,
                    "target_kind": "networking_resource",
                    "edge_type": "uses_security_group",
                },
                {
                    "target_external_id": IAM_ROLE_ARN,
                    "target_kind": "identity",
                    "edge_type": "assumes_role",
                },
            ],
        },
        {
            "resource_id": EC2_APP,
            "resource_type": "ec2_instance",
            "name": "demo-app-1",
            "provider": "aws",
            "metadata": {
                "instance_type": "t3.micro",
                "state": "running",
                "private_ip": "10.42.2.10",
                "public_ip": None,
                "vpc_id": VPC_ID,
                "subnet_id": SUBNET_APP,
                "launch_time": now.isoformat(),
            },
            "region": AWS_REGION,
            "tags": {"Name": "demo-app-1", "Environment": "demo"},
            "external_id": EC2_APP,
            "relationships": [
                {
                    "target_external_id": VPC_ID,
                    "target_kind": "networking_resource",
                    "edge_type": "in_network",
                },
                {
                    "target_external_id": SUBNET_APP,
                    "target_kind": "networking_resource",
                    "edge_type": "in_subnet",
                },
                {
                    "target_external_id": SG_APP,
                    "target_kind": "networking_resource",
                    "edge_type": "uses_security_group",
                },
            ],
        },
    ]

    storage = [
        {
            "resource_id": EBS_VOL,
            "resource_type": "ebs_volume",
            "name": "demo-web-1-root",
            "provider": "aws",
            "metadata": {
                "size_gb": 20,
                "volume_type": "gp3",
                "state": "in-use",
                "iops": 3000,
                "encrypted": True,
                "availability_zone": f"{AWS_REGION}a",
            },
            "region": AWS_REGION,
            "tags": {"Environment": "demo"},
            "external_id": EBS_VOL,
            "relationships": [
                {
                    "target_external_id": EC2_WEB,
                    "target_kind": "entity",
                    "edge_type": "attached_to",
                }
            ],
        },
    ]

    database = [
        {
            "resource_id": RDS_RESOURCE_ID,
            "resource_type": "rds_instance",
            "name": RDS_RESOURCE_ID,
            "provider": "aws",
            "metadata": {
                "engine": "postgres",
                "engine_version": "16.4",
                "instance_class": "db.t3.micro",
                "storage_type": "gp3",
                "allocated_storage": 50,
                "status": "available",
                "endpoint": f"{RDS_RESOURCE_ID}.abc123.{AWS_REGION}.rds.amazonaws.com",
                "port": 5432,
                "multi_az": False,
                "availability_zone": f"{AWS_REGION}b",
                "vpc_id": VPC_ID,
            },
            "region": AWS_REGION,
            "tags": {"Environment": "demo"},
            # RDS's own external_id is its ARN (not the short DBInstanceIdentifier
            # used as resource_id) — matches AWSDiscoveryClient.discover_databases.
            "external_id": RDS_ARN,
            "relationships": [
                {
                    "target_external_id": VPC_ID,
                    "target_kind": "networking_resource",
                    "edge_type": "in_network",
                },
                {
                    "target_external_id": SG_APP,
                    "target_kind": "networking_resource",
                    "edge_type": "uses_security_group",
                },
            ],
        },
    ]

    serverless = [
        {
            "resource_id": LAMBDA_ARN,
            "resource_type": "lambda_function",
            "name": "demo-processor",
            "provider": "aws",
            "metadata": {
                "runtime": "python3.13",
                "handler": "handler.main",
                "memory_size_mb": 256,
                "timeout_seconds": 30,
                "last_modified": now.isoformat(),
                "code_size_bytes": 4096,
                "vpc_id": VPC_ID,
            },
            "region": AWS_REGION,
            "tags": {"Environment": "demo"},
            "external_id": LAMBDA_ARN,
            "relationships": [
                {
                    "target_external_id": VPC_ID,
                    "target_kind": "networking_resource",
                    "edge_type": "in_network",
                },
                {
                    "target_external_id": SG_APP,
                    "target_kind": "networking_resource",
                    "edge_type": "uses_security_group",
                },
                {
                    "target_external_id": IAM_ROLE_ARN,
                    "target_kind": "identity",
                    "edge_type": "assumes_role",
                },
            ],
        },
    ]

    # Real AWSDiscoveryClient.discover_iam() calls format_resource() for IAM
    # roles WITHOUT an external_id argument, so external_id is None and the
    # linker registers/resolves the role by its resource_id (the ARN) instead
    # — see DiscoveryService._register/_store_iam_as_identity. Mirrored here.
    iam = [
        {
            "resource_id": IAM_ROLE_ARN,
            "resource_type": "iam_role",
            "name": "demo-ec2-lambda-role",
            "provider": "aws",
            "metadata": {
                "role_id": "AROADEMOROLE00000001",
                "arn": IAM_ROLE_ARN,
                "path": "/",
                "description": "Demo EC2/Lambda execution role",
                "create_date": now.isoformat(),
                "max_session_duration": 3600,
            },
            "region": "global",
            "tags": {"Environment": "demo"},
            "external_id": None,
            "relationships": [],
        },
    ]

    resources_count = sum(
        len(bucket) for bucket in (compute, storage, network, database, serverless, iam)
    )

    return {
        "compute": compute,
        "storage": storage,
        "network": network,
        "database": database,
        "serverless": serverless,
        "iam": iam,
        "resources_count": resources_count,
        "discovery_time": now,
        "duration_seconds": 4.2,
    }


def _resolve_or_create_demo_tenant(db: Any) -> int:
    """Find or create the dedicated "Demo Cloud Discovery" tenant.

    Deliberately a brand-new, clearly-named tenant rather than whatever
    "system"/"default" tenant owns the deployment's default admin — so the
    demo topology never gets mixed into (or hidden behind) a shared
    deployment's real data, and the login story stays unambiguous.
    """
    existing = db(db.tenants.slug == DEMO_TENANT_SLUG).select().first()
    if existing:
        return int(existing.id)

    now = datetime.now(timezone.utc)
    tenant_id = db.tenants.insert(
        name=DEMO_TENANT_NAME,
        slug=DEMO_TENANT_SLUG,
        subscription_tier="enterprise",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return int(tenant_id)


def _resolve_or_create_demo_org(db: Any, tenant_id: int) -> int:
    """Find or create the demo organization under the demo tenant."""
    existing = (
        db(
            (db.organizations.tenant_id == tenant_id)
            & (db.organizations.name == DEMO_ORG_NAME)
        )
        .select()
        .first()
    )
    if existing:
        return int(existing.id)

    now = datetime.now(timezone.utc)
    org_id = db.organizations.insert(
        tenant_id=tenant_id,
        name=DEMO_ORG_NAME,
        type="organization",
        description="Seeded AWS cloud-discovery demo topology",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return int(org_id)


def _resolve_or_create_demo_admin(db: Any, tenant_id: int) -> None:
    """Find or create the demo admin identity used to log in and view the demo."""
    existing = db(db.identities.username == DEMO_ADMIN_USERNAME).select().first()
    if existing:
        return

    now = datetime.now(timezone.utc)
    db.identities.insert(
        tenant_id=tenant_id,
        username=DEMO_ADMIN_USERNAME,
        email=DEMO_ADMIN_USERNAME,
        full_name="Demo Admin",
        identity_type="human",
        auth_provider="local",
        password_hash=generate_password_hash(DEMO_ADMIN_PASSWORD),
        is_active=True,
        is_superuser=True,
        mfa_enabled=False,
        must_change_password=False,
        portal_role="admin",
        created_at=now,
        updated_at=now,
    )
    db.commit()


def _summarize_by_resource_type(discovery_results: Dict[str, Any]) -> Dict[str, int]:
    """Count seeded resources by resource_type for the summary print."""
    counts: Dict[str, int] = {}
    for category, items in discovery_results.items():
        if category in ("resources_count", "discovery_time", "duration_seconds"):
            continue
        for item in items:
            rt = item["resource_type"]
            counts[rt] = counts.get(rt, 0) + 1
    return counts


def seed_cloud_discovery() -> None:
    """Seed the demo cloud-discovery topology via the real DiscoveryService linker."""
    print("Seeding demo cloud-discovery topology (real linker, no live AWS)...")

    # NOTE: no `with app.app_context():` here — Quart's AppContext only
    # supports `async with` (no __enter__/__exit__), so a plain sync `with`
    # raises TypeError at runtime. app.db is populated at create_app() time
    # and is safe to use directly outside of any request/app context, same
    # as the pytest `app` fixture (tests/conftest.py) already does.
    app = create_app()
    db = app.db

    tenant_id = _resolve_or_create_demo_tenant(db)
    org_id = _resolve_or_create_demo_org(db, tenant_id)
    _resolve_or_create_demo_admin(db, tenant_id)

    discovery_results = build_discovery_results()
    service = DiscoveryService(db)
    counts = service._store_discovered_resources(org_id, discovery_results)
    db.commit()

    type_counts = _summarize_by_resource_type(discovery_results)

    print("\nResources seeded by type:")
    for resource_type, count in sorted(type_counts.items()):
        print(f"  {resource_type}: {count}")

    print(
        f"\nedges_created={counts['edges_created']} "
        f"unresolved_edges={counts['unresolved_edges']}"
    )

    print("\n" + "=" * 72)
    print("DEMO LOGIN — use this exact identity, /graph/map is tenant-scoped")
    print("=" * 72)
    print(f"  tenant       : {DEMO_TENANT_NAME} (slug={DEMO_TENANT_SLUG})")
    print(f"  tenant_id    : {tenant_id}")
    print(f"  org_id       : {org_id}")
    print(f"  login email  : {DEMO_ADMIN_USERNAME}")
    print(f"  password     : {DEMO_ADMIN_PASSWORD}")
    print(
        "  Any other login will see an EMPTY graph — this data lives "
        "only in this tenant."
    )
    print("=" * 72)

    if counts["unresolved_edges"]:
        print(
            f"\nWARNING: {counts['unresolved_edges']} unresolved edge(s) — "
            "the demo graph will be missing relationships. Investigate "
            "before the demo.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    try:
        seed_cloud_discovery()
    except Exception as e:  # noqa: BLE001 - top-level script error boundary
        print(f"Error seeding cloud discovery demo data: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
