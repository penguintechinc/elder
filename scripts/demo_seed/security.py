"""Security demo data: SBOM scans/components, vulnerabilities, certs, secrets."""

from __future__ import annotations

import hashlib
from datetime import timedelta

from .common import SeedContext, get_or_create, now

# (service, scan_type, repo, components found)
SCANS = (
    ("Acme Storefront", "repository", "https://git.example.com/acme/storefront", 2),
    ("Acme Orders API", "container", None, 1),
    ("Acme Billing Worker", "lockfile", "https://git.example.com/acme/billing", 1),
)

# (service, name, version, package_type, purl)
COMPONENTS = (
    (
        "Acme Orders API",
        "log4j-core",
        "2.14.1",
        "maven",
        "pkg:maven/org.apache.logging.log4j/log4j-core@2.14.1",
    ),
    ("Acme Storefront", "qs", "6.5.2", "npm", "pkg:npm/qs@6.5.2"),
    ("Acme Storefront", "lodash", "4.17.15", "npm", "pkg:npm/lodash@4.17.15"),
    ("Acme Billing Worker", "tokio", "1.38.0", "cargo", "pkg:cargo/tokio@1.38.0"),
)

# (cve, severity, cvss, title, component name, status, fixed version)
VULNS = (
    (
        "CVE-2021-44228",
        "critical",
        10.0,
        "Log4Shell remote code execution in JNDI lookups",
        "log4j-core",
        "open",
        "2.17.1",
    ),
    (
        "CVE-2022-24999",
        "high",
        7.5,
        "qs prototype pollution via crafted query string",
        "qs",
        "investigating",
        "6.5.3",
    ),
    (
        "CVE-2020-28500",
        "medium",
        5.3,
        "lodash ReDoS in toNumber/trim/trimEnd",
        "lodash",
        "remediated",
        "4.17.21",
    ),
    (
        "CVE-2099-0001",
        "low",
        2.1,
        "Demo advisory: verbose error text in a helper crate",
        "tokio",
        "accepted",
        "1.38.1",
    ),
)

CERTS = (
    ("storefront.acme.example.com", "Let's Encrypt", "valid", 70),
    ("orders.acme.example.com", "Acme Internal CA", "valid", 200),
    ("billing.acme.example.com", "Acme Internal CA", "expiring_soon", 12),
)

LICENSE_POLICIES = (
    ("Permissive only", ["MIT", "Apache-2.0", "BSD-3-Clause"], [], "warn"),
    ("Block strong copyleft", [], ["AGPL-3.0", "GPL-3.0"], "block"),
    ("Review weak copyleft", [], ["LGPL-3.0", "MPL-2.0"], "warn"),
)

SECRETS = (
    ("orders-db-password", "secret/data/acme/orders-db", "password"),
    ("payments-api-token", "secret/data/acme/payments", "api_key"),
    ("storefront-tls-key", "secret/data/acme/storefront-tls", "certificate"),
)

API_KEYS = (
    ("alex.rivera", "CI deploy key"),
    ("sam.okafor", "Grafana read-only"),
    ("priya.nair", "Compliance exporter"),
)


def seed_sbom(ctx: SeedContext, services: dict[str, int]) -> None:
    """Seed 3 scans, 4 components, 4 vulnerabilities and their linkage rows."""
    for svc, scan_type, repo, found in SCANS:
        get_or_create(
            ctx,
            "sbom_scans",
            {
                "tenant_id": ctx.tenant_id,
                "parent_type": "service",
                "parent_id": services[svc],
                "scan_type": scan_type,
            },
            {
                "status": "completed",
                "repository_url": repo,
                "components_found": found,
                "started_at": now() - timedelta(hours=2),
                "completed_at": now() - timedelta(hours=2, minutes=-3),
                "created_at": now() - timedelta(hours=2),
            },
            feature="sbom scans",
        )
    comp_ids: dict[str, int] = {}
    for svc, name, version, ptype, purl in COMPONENTS:
        comp_ids[name] = get_or_create(
            ctx,
            "sbom_components",
            {
                "tenant_id": ctx.tenant_id,
                "parent_type": "service",
                "parent_id": services[svc],
                "name": name,
                "version": version,
            },
            {
                "package_type": ptype,
                "purl": purl,
                "direct": True,
                "is_active": True,
                "created_at": now(),
                "updated_at": now(),
            },
            feature="sbom components",
        )
    for cve, severity, cvss, title, comp, status, fixed in VULNS:
        vid = get_or_create(
            ctx,
            "vulnerabilities",
            {"tenant_id": ctx.tenant_id, "cve_id": cve},
            {
                "severity": severity,
                "cvss_score": cvss,
                "title": title,
                "description": title,
                "fixed_versions": [fixed],
                "source": "demo-seed",
                "is_active": True,
                "published_at": now() - timedelta(days=400),
                "created_at": now(),
                "updated_at": now(),
            },
            feature="vulnerabilities",
        )
        get_or_create(
            ctx,
            "component_vulnerabilities",
            {
                "tenant_id": ctx.tenant_id,
                "component_id": comp_ids[comp],
                "vulnerability_id": vid,
            },
            {"status": status, "created_at": now(), "updated_at": now()},
            feature="component vulnerabilities",
        )


def seed_certificates(ctx: SeedContext) -> None:
    """Seed 3 certificates (two healthy, one expiring soon)."""
    today = now().date()
    for cn, creator, status, days_left in CERTS:
        get_or_create(
            ctx,
            "certificates",
            {"tenant_id": ctx.tenant_id, "name": cn},
            {
                "organization_id": ctx.org_id,
                "creator": creator,
                "cert_type": "server",
                "common_name": cn,
                "issue_date": today - timedelta(days=90 - days_left),
                "expiration_date": today + timedelta(days=days_left),
                "auto_renew": True,
                "is_revoked": False,
                "status": status,
                "is_active": True,
                "created_at": now(),
                "updated_at": now(),
            },
            feature="certificates",
        )


def seed_license_policies(ctx: SeedContext) -> None:
    """Seed 3 SBOM license policies (allow/deny lists)."""
    for name, allowed, denied, action in LICENSE_POLICIES:
        get_or_create(
            ctx,
            "license_policies",
            {"tenant_id": ctx.tenant_id, "organization_id": ctx.org_id, "name": name},
            {
                "allowed_licenses": allowed,
                "denied_licenses": denied,
                "action": action,
                "is_active": True,
                "created_at": now(),
                "updated_at": now(),
            },
            feature="license policies",
        )


def seed_secrets(ctx: SeedContext) -> None:
    """Seed one secret provider and 3 secret *references* (no secret values)."""
    provider_id = get_or_create(
        ctx,
        "secret_providers",
        {"organization_id": ctx.org_id, "name": "Acme Vault (demo)"},
        {
            "provider": "vault",
            "config_json": {
                "address": "https://vault.acme.example.com",
                "auth": "approle",
            },
            "enabled": True,
            "created_at": now(),
            "updated_at": now(),
        },
        feature="secret providers",
    )
    for name, path, kind in SECRETS:
        get_or_create(
            ctx,
            "secrets",
            {"organization_id": ctx.org_id, "name": name},
            {
                "provider_id": provider_id,
                "provider_path": path,
                "secret_type": kind,
                "is_kv": False,
                "created_at": now(),
                "updated_at": now(),
            },
            feature="secrets",
        )


def seed_api_keys(ctx: SeedContext, staff: dict[str, int]) -> None:
    """Seed 3 API key records; hashes derive from the label, never a real key."""
    for handle, label in API_KEYS:
        digest = hashlib.sha256(f"demo-seed:{label}".encode()).hexdigest()
        get_or_create(
            ctx,
            "api_keys",
            {"identity_id": staff[handle], "name": label},
            {
                "key_hash": digest,
                "prefix": "eld_demo",
                "is_active": True,
                "expires_at": now() + timedelta(days=180),
                "created_at": now(),
                "updated_at": now(),
            },
            feature="api keys",
        )
