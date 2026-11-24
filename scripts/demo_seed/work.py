"""Work-management demo data: projects, on-call, docs, diagrams, AI, runs."""

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
