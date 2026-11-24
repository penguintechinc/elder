#!/usr/bin/env python3
"""Seed one coherent, idempotent Elder demo organisation ("Acme Corp").

Canonical entry point for demo / marketing-screenshot data (``make seed-demo``).
Builds a single tenant-scoped story: staff + teams, compute (LXD containers and
VMs), networks and IPAM, services/software, SBOM components with a severity mix
of vulnerabilities, certificates, secrets (references only), API keys, on-call,
docs/pages/diagrams, AI resources (fake endpoints, no keys), run functions and
access reviews, then layers the existing cloud-discovery, Kubernetes/geo and
support/CRM seeders on top. Each feature gets 3-4 items; the cloud and
Kubernetes topologies are inherently larger graphs (documented exceptions).

Re-running creates nothing new and prints ``unchanged``. Every created object
that has a ``village_id`` column gets a unique one. See ``docs/demo-data.md``.

Usage:
    python3 scripts/seed_demo.py
"""

import importlib.util
import os
import sys
from typing import Any

# Add repo root to path (matches the other seed scripts)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from apps.api.main import create_app  # noqa: E402
from scripts.demo_seed import core, infra, reviews, security, work  # noqa: E402
from scripts.demo_seed.common import (  # noqa: E402
    DEMO_ADMIN_EMAIL,
    SeedContext,
    backfill_village_ids,
    log,
    village_autofill,
)

_SCRIPTS = os.path.dirname(os.path.abspath(__file__))


def _load(name: str) -> Any:
    """Load a sibling seed script by file path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(_SCRIPTS, f"{name}.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row_counts(db: Any) -> dict[str, int]:
    """Return {table: row count} for every table (used for the unchanged check)."""
    from sqlalchemy import text

    counts: dict[str, int] = {}
    with db.engine.connect() as conn:
        for name in sorted(db.tables):
            counts[name] = conn.execute(
                text(f'SELECT count(*) FROM "{name}"')
            ).scalar_one()  # noqa: S608
    return counts


def seed_demo(app: Any | None = None) -> dict[str, Any]:
    """Seed the whole demo data set and return a result summary dict."""
    app = app if app is not None else create_app()
    db = app.db
    redis_client = app.redis_client

    core.ensure_login_tenant(db)
    cloud = _load("seed_cloud_discovery")
    tenant_id = cloud._resolve_or_create_demo_tenant(db)
    with village_autofill(redis_client, tenant_id):
        return _seed_all(app, cloud, tenant_id)


def _seed_all(app: Any, cloud: Any, tenant_id: int) -> dict[str, Any]:
    """Run every seeder (called with the village_id insert hook active)."""
    db = app.db
    redis_client = app.redis_client
    before = row_counts(db)

    org_id = cloud._resolve_or_create_demo_org(db, tenant_id)
    cloud._resolve_or_create_demo_admin(db, tenant_id)
    admin = db(db.identities.username == DEMO_ADMIN_EMAIL).select().first()
    ctx = SeedContext(
        db=db,
        redis_client=redis_client,
        tenant_id=tenant_id,
        org_id=org_id,
        admin_id=int(admin.id),
    )

    staff = core.seed_staff(ctx)
    core.seed_departments(ctx)
    teams = core.seed_teams(ctx, staff)
    infra.seed_compute(ctx)
    infra.seed_networks(ctx)
    infra.seed_ipam(ctx)
    infra.seed_data_stores(ctx)
    services = infra.seed_services(ctx)
    infra.seed_software(ctx)
    security.seed_sbom(ctx, services)
    security.seed_certificates(ctx)
    security.seed_license_policies(ctx)
    security.seed_secrets(ctx)
    security.seed_api_keys(ctx, staff)
    work.seed_projects(ctx)
    work.seed_oncall(ctx, staff)
    work.seed_docs_and_pages(ctx)
    work.seed_diagrams(ctx)
    work.seed_ai_resources(ctx)
    work.seed_run_functions(ctx)
    reviews.seed_access_reviews(ctx, teams)

    # Layered seeders: cloud topology, K8s/geo, support/CRM (issues, webhooks, streams)
    cloud.seed_cloud_discovery(app)
    _load("seed_k8s_geo_demo").seed_k8s_geo_demo(app)
    _load("seed_demo_unified").seed_demo_unified(app)

    backfilled = backfill_village_ids(db, redis_client, tenant_id)
    after = row_counts(db)
    created = sum(after.values()) - sum(before.values())
    return {
        "tenant_id": tenant_id,
        "org_id": org_id,
        "created_rows": created,
        "backfilled_village_ids": backfilled,
        "stats": ctx.stats,
    }


def main() -> int:
    """Run the seed and print a compact per-feature report."""
    result = seed_demo()
    log("\nDemo data (created / already-existing):")
    for feature, (made, had) in sorted(result["stats"].items()):
        log(f"  {feature:28s} {made:3d} / {had:3d}")
    log(
        f"\nlogin: {DEMO_ADMIN_EMAIL}  (password: $DEMO_ADMIN_PASSWORD, see docs/demo-data.md)"
    )
    log(f"village_id backfilled on legacy rows: {result['backfilled_village_ids']}")
    status = (
        "unchanged"
        if result["created_rows"] == 0
        else f"created {result['created_rows']} rows"
    )
    log(f"DEMO SEED RESULT: {status}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - top-level script error boundary
        import traceback

        print(f"Error seeding demo data: {exc}")
        traceback.print_exc()
        sys.exit(1)
