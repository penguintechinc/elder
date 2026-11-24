"""Work-management demo data: projects, on-call, docs, diagrams, AI, runs, webhooks, workers."""

from __future__ import annotations

import hashlib
from datetime import timedelta

from .common import SeedContext, get_or_create, now

PROJECTS = (
    ("Platform Hardening", "active"),
    ("Storefront Relaunch", "active"),
    ("Billing Migration", "planning"),
)
MILESTONES = (
    ("Hardening: TLS everywhere", "open", 30),
    ("Storefront: beta launch", "open", 60),
    ("Billing: cutover rehearsal", "closed", -10),
)
LABELS = (
    ("security", "#d73a4a"),
    ("infrastructure", "#0e8a16"),
    ("customer-impact", "#fbca04"),
    ("tech-debt", "#6f42c1"),
)

ROTATIONS = (
    ("Platform primary", "weekly", ("alex.rivera", "sam.okafor")),
    ("Security escalation", "weekly", ("priya.nair",)),
    ("Billing worker", "manual", ("sam.okafor",)),
)

DOCS = (
    (
        "Production Runbook",
        "production-runbook",
        "Steps to deploy, roll back and verify the Acme production stack.",
    ),
    (
        "Incident Response Policy",
        "incident-response-policy",
        "Severity levels, paging rules and post-incident review expectations.",
    ),
    (
        "Onboarding Guide",
        "onboarding-guide",
        "Accounts, access requests and first-week checklist for new engineers.",
    ),
)
PAGES = (
    ("Platform Overview", "platform-overview", "How the Acme platform is laid out."),
    ("Service Catalog", "service-catalog", "Owners, SLAs and contacts per service."),
    ("Maintenance Calendar", "maintenance-calendar", "Planned windows for Q1."),
)

DIAGRAMS = (
    "Acme Production Architecture",
    "Network Topology",
    "Incident Escalation Flow",
)

# Fake endpoints on the reserved example.com domain; there are NO credentials.
AI_RESOURCES = (
    (
        "Claude (Acme enterprise)",
        "llm",
        "anthropic",
        "claude-sonnet",
        "https://llm.acme.example.com/v1",
        "allowed",
        "enterprise",
    ),
    (
        "Local Ollama gemma",
        "llm",
        "ollama",
        "gemma",
        "http://ollama.acme.example.com:11434",
        "restricted",
        "team",
    ),
    (
        "Acme AI Gateway",
        "ai_gateway",
        "waddleai",
        None,
        "https://ai-gateway.acme.example.com",
        "allowed",
        "enterprise",
    ),
    (
        "Elder MCP Server",
        "mcp_server",
        "elder",
        None,
        "https://mcp.acme.example.com/mcp",
        "pending_review",
        "team",
    ),
)

RUN_FUNCTIONS = (
    (
        "rotate-certs-check",
        "python",
        "print('checking certificate expiry for acme.example.com')",
    ),
    ("nightly-report", "python", "print('generating nightly capacity report')"),
    ("webhook-relay", "nodejs", "console.log('relaying webhook to example.com')"),
)

# (name, slug, description, auth_type, token_preview, basic_user, scopes, is_active, receive_count, minutes_ago, metadata)
INBOUND_WEBHOOKS = (
    (
        "Alertmanager Production Alerts",
        "acme-alertmanager-prod",
        "Receives Prometheus Alertmanager firing and resolved notifications from dal2 production.",
        "bearer",
        "wh_demo...0001",
        None,
        ["events:log", "issues:create", "playbooks:trigger"],
        True,
        2,
        18,
        {"source": "alertmanager.acme.example.com", "env": "production"},
    ),
    (
        "CI/CD Vulnerability Scanner",
        "acme-ci-vuln-ingest",
        "Ingests SBOM and CVE findings from CI builds on git.example.com.",
        "bearer",
        "wh_demo...0002",
        None,
        ["events:log", "vulnerabilities:create", "vulnerabilities:update"],
        True,
        1,
        120,
        {"source": "ci.acme.example.com", "env": "ci"},
    ),
    (
        "Cloud Inventory Sync",
        "acme-inventory-sync",
        "Accepts infrastructure and service catalog updates from Terraform state hooks.",
        "basic",
        None,
        "terraform-sync",
        ["events:log", "entities:create", "entities:update", "services:update"],
        True,
        1,
        300,
        {"source": "infra.acme.example.com", "env": "production"},
    ),
    (
        "Statuspage Incident Mirror",
        "acme-statuspage-mirror",
        "Logs external dependency status webhooks for post-incident correlation.",
        "bearer",
        "wh_demo...0004",
        None,
        ["events:log"],
        False,
        0,
        None,
        {"source": "status.example.org", "env": "staging"},
    ),
)

# (webhook_slug, source_ip, action, status, minutes_ago, headers, payload, action_result)
INBOUND_WEBHOOK_EVENTS = (
    (
        "acme-alertmanager-prod",
        "198.51.100.10",
        "issues:create",
        "created",
        18,
        {"content-type": "application/json", "user-agent": "Alertmanager/0.27.0"},
        {
            "alertname": "HighErrorRate",
            "service": "orders-api.acme.example.com",
            "severity": "critical",
        },
        {"resource": "issues", "title": "HighErrorRate on orders-api.acme.example.com"},
    ),
    (
        "acme-alertmanager-prod",
        "198.51.100.11",
        "playbooks:trigger",
        "received",
        65,
        {"content-type": "application/json", "user-agent": "Alertmanager/0.27.0"},
        {
            "alertname": "CertExpiringSoon",
            "host": "billing.acme.example.com",
            "days_remaining": 12,
        },
        {"logged": True, "playbook": "cert-renewal-triage"},
    ),
    (
        "acme-ci-vuln-ingest",
        "203.0.113.42",
        "vulnerabilities:create",
        "created",
        120,
        {"content-type": "application/json", "user-agent": "Acme-CI-Scanner/2.4"},
        {
            "cve_id": "CVE-2022-24999",
            "repository": "https://git.example.com/acme/storefront",
            "severity": "high",
        },
        {"resource": "vulnerabilities", "cve_id": "CVE-2022-24999"},
    ),
    (
        "acme-inventory-sync",
        "192.0.2.15",
        "entities:update",
        "updated",
        300,
        {"content-type": "application/json", "user-agent": "Terraform-Hook/1.9"},
        {
            "entity": "acme-app-01.example.com",
            "region": "dal2",
            "state": "running",
        },
        {"resource": "entities", "updated": True},
    ),
)

# (worker_id, worker_type, name, cluster, hostname, version, config_json, capabilities_json)
REMOTE_WORKERS = (
    (
        "runworker-dal2-01",
        "runworker",
        "DAL2 Run Worker 01",
        "dal2-prod",
        "runworker-dal2-01.acme.example.com",
        "4.0.0",
        {"poll_interval_seconds": 30, "max_concurrency": 4},
        {"runtimes": ["python", "nodejs", "bash"], "region": "us-central"},
    ),
    (
        "scanner-dal2-01",
        "scanner",
        "DAL2 Security Scanner 01",
        "dal2-prod",
        "scanner-dal2-01.acme.example.com",
        "4.0.0",
        {"poll_interval_seconds": 60, "max_concurrency": 2},
        {"scanners": ["sbom", "cve", "tls", "container"], "region": "us-central"},
    ),
    (
        "runworker-eu-west-01",
        "runworker",
        "EU-West Run Worker 01",
        "eu-west-prod",
        "runworker-eu-west-01.acme.example.com",
        "4.0.0",
        {"poll_interval_seconds": 30, "max_concurrency": 4},
        {"runtimes": ["python", "nodejs"], "region": "eu-west"},
    ),
)


def seed_projects(ctx: SeedContext) -> None:
    """Seed 3 projects, 3 milestones and 4 labels."""
    project_ids = []
    for name, status in PROJECTS:
        project_ids.append(
            get_or_create(
                ctx,
                "projects",
                {"organization_id": ctx.org_id, "name": name},
                {
                    "status": status,
                    "tenant_id": ctx.tenant_id,
                    "description": f"{name} workstream",
                    "created_at": now(),
                    "updated_at": now(),
                },
                feature="projects",
            )
        )
    for (title, status, days), pid in zip(MILESTONES, project_ids, strict=True):
        get_or_create(
            ctx,
            "milestones",
            {"organization_id": ctx.org_id, "title": title},
            {
                "status": status,
                "tenant_id": ctx.tenant_id,
                "project_id": pid,
                "due_date": now() + timedelta(days=days),
                "created_at": now(),
                "updated_at": now(),
            },
            feature="milestones",
        )
    for name, color in LABELS:
        get_or_create(
            ctx,
            "labels",
            {"organization_id": ctx.org_id, "name": name},
            {"color": color, "created_at": now(), "updated_at": now()},
            feature="labels",
        )


def seed_oncall(ctx: SeedContext, staff: dict[str, int]) -> None:
    """Seed 3 on-call rotations with 1-2 participants each."""
    for name, schedule, people in ROTATIONS:
        rid = get_or_create(
            ctx,
            "on_call_rotations",
            {"tenant_id": ctx.tenant_id, "name": name},
            {
                "is_active": True,
                "scope_type": "organization",
                "schedule_type": schedule,
                "organization_id": ctx.org_id,
                "rotation_length_days": 7,
                "rotation_start_date": now(),
                "handoff_timezone": "UTC",
                "created_at": now(),
                "updated_at": now(),
            },
            feature="on-call rotations",
        )
        for idx, handle in enumerate(people):
            get_or_create(
                ctx,
                "on_call_rotation_participants",
                {"rotation_id": rid, "identity_id": staff[handle]},
                {
                    "order_index": idx,
                    "is_active": True,
                    "notification_email": f"{handle}@example.com",
                    "created_at": now(),
                    "updated_at": now(),
                },
                feature="on-call participants",
            )


def seed_docs_and_pages(ctx: SeedContext) -> None:
    """Seed 3 documents and 3 pages authored by the demo admin."""
    for table, items, feature in (
        ("doc_documents", DOCS, "documents"),
        ("pg_pages", PAGES, "pages"),
    ):
        for title, slug, summary in items:
            html = f"<h1>{title}</h1><p>{summary}</p>"
            extra = {
                "body_html": html,
                "author_identity_id": ctx.admin_id,
                "status": "published",
                "created_at": now(),
                "updated_at": now(),
            }
            if table == "doc_documents":
                extra["body_markdown"] = f"# {title}\n\n{summary}\n"
            get_or_create(
                ctx,
                table,
                {"tenant_id": ctx.tenant_id, "slug": slug},
                {"title": title, **extra},
                feature=feature,
            )


def seed_diagrams(ctx: SeedContext) -> None:
    """Seed 3 diagrams; the first carries 4 shapes and 3 connectors."""
    ids = []
    for title in DIAGRAMS:
        ids.append(
            get_or_create(
                ctx,
                "dg_diagrams",
                {"tenant_id": ctx.tenant_id, "title": title},
                {
                    "owner_identity_id": ctx.admin_id,
                    "status": "published",
                    "description": f"{title} (demo)",
                    "created_at": now(),
                    "updated_at": now(),
                },
                feature="diagrams",
            )
        )
    shape_ids = []
    for idx, label in enumerate(
        ("Storefront", "Orders API", "Billing Worker", "Orders DB")
    ):
        shape_ids.append(
            get_or_create(
                ctx,
                "dg_shapes",
                {"diagram_id": ids[0], "x_position": 80 + idx * 220, "y_position": 120},
                {
                    "tenant_id": ctx.tenant_id,
                    "shape_type": "rectangle",
                    "width": 160,
                    "height": 70,
                    "style_json": {"label": label},
                    "created_at": now(),
                    "updated_at": now(),
                },
                feature="diagram shapes",
            )
        )
    for a, b in zip(shape_ids, shape_ids[1:], strict=False):
        get_or_create(
            ctx,
            "dg_connectors",
            {"diagram_id": ids[0], "start_shape_id": a, "end_shape_id": b},
            {
                "tenant_id": ctx.tenant_id,
                "connector_type": "arrow",
                "created_at": now(),
                "updated_at": now(),
            },
            feature="diagram connectors",
        )


def seed_ai_resources(ctx: SeedContext) -> None:
    """Seed 4 AI resources (LLMs, gateway, MCP server) with fake endpoints."""
    for name, rtype, provider, model, url, status, level in AI_RESOURCES:
        get_or_create(
            ctx,
            "ai_resources",
            {"tenant_id": ctx.tenant_id, "organization_id": ctx.org_id, "name": name},
            {
                "resource_type": rtype,
                "provider": provider,
                "model_name": model,
                "endpoint_url": url,
                "status": status,
                "agreement_level": level,
                "is_active": True,
                "tags": {"env": "demo"},
                "created_at": now(),
                "updated_at": now(),
            },
            feature="ai resources",
        )


def seed_run_functions(ctx: SeedContext) -> None:
    """Seed 3 run functions (serverless scripts shown on the Runs page)."""
    for name, runtime, code in RUN_FUNCTIONS:
        get_or_create(
            ctx,
            "run_functions",
            {"tenant_id": ctx.tenant_id, "name": name},
            {
                "runtime": runtime,
                "code": code,
                "code_sha256": hashlib.sha256(code.encode()).hexdigest(),
                "timeout_seconds": 60,
                "is_enabled": True,
                "created_by_identity_id": ctx.admin_id,
                "created_at": now(),
                "updated_at": now(),
            },
            feature="run functions",
        )


def seed_inbound_webhooks(ctx: SeedContext) -> None:
    """Seed 4 inbound webhooks and 4 sample inbound webhook events."""
    hook_ids: dict[str, int] = {}
    for (
        name,
        slug,
        description,
        auth_type,
        token_preview,
        basic_user,
        scopes,
        is_active,
        receive_count,
        minutes_ago,
        metadata,
    ) in INBOUND_WEBHOOKS:
        digest = hashlib.sha256(f"demo-seed:inbound:{slug}".encode()).hexdigest()
        hook_ids[slug] = get_or_create(
            ctx,
            "inbound_webhooks",
            {"tenant_id": ctx.tenant_id, "slug": slug},
            {
                "organization_id": ctx.org_id,
                "name": name,
                "description": description,
                "auth_type": auth_type,
                "bearer_token_hash": digest if auth_type == "bearer" else None,
                "bearer_token_preview": token_preview,
                "basic_username": basic_user,
                "basic_password_hash": digest if auth_type == "basic" else None,
                "is_active": is_active,
                "stream_id": None,
                "scopes": scopes,
                "metadata": metadata,
                "last_received_at": (
                    now() - timedelta(minutes=minutes_ago)
                    if minutes_ago is not None
                    else None
                ),
                "receive_count": receive_count,
                "created_at": now(),
                "updated_at": now(),
            },
            feature="inbound webhooks",
        )
    for (
        slug,
        source_ip,
        action,
        status,
        minutes_ago,
        headers,
        payload,
        action_result,
    ) in INBOUND_WEBHOOK_EVENTS:
        get_or_create(
            ctx,
            "inbound_webhook_events",
            {
                "inbound_webhook_id": hook_ids[slug],
                "source_ip": source_ip,
                "action": action,
            },
            {
                "tenant_id": ctx.tenant_id,
                "headers": headers,
                "payload": payload,
                "action_result": action_result,
                "status": status,
                "received_at": now() - timedelta(minutes=minutes_ago),
            },
            feature="inbound webhook events",
        )


def seed_remote_workers(ctx: SeedContext) -> None:
    """Seed 3 online remote workers with no raw tokens stored."""
    for (
        worker_id,
        worker_type,
        name,
        cluster,
        hostname,
        version,
        config_json,
        capabilities_json,
    ) in REMOTE_WORKERS:
        get_or_create(
            ctx,
            "remote_workers",
            {"worker_id": worker_id},
            {
                "tenant_id": ctx.tenant_id,
                "worker_type": worker_type,
                "name": name,
                "cluster": cluster,
                "token_hash": None,
                "token_prefix": None,
                "config_json": config_json,
                "capabilities_json": capabilities_json,
                "hostname": hostname,
                "version": version,
                "is_enabled": True,
                "last_heartbeat_at": now() - timedelta(seconds=30),
                "revoked_at": None,
                "created_by_identity_id": ctx.admin_id,
                "created_at": now(),
                "updated_at": now(),
            },
            feature="remote workers",
        )
