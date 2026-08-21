#!/usr/bin/env python3
"""Seed a demo internal-helpdesk experience using the native Issue model.

Seeds real demo data (support issues, comments, webhooks, streams) into the
native model. Shares the same demo tenant/org/admin as seed_cloud_discovery.py
so one login works for both the graph/map and the support experience.

Usage:
    python3 scripts/seed_demo_unified.py
"""

import json
import os
import secrets
import sys
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

# Add parent directory to path (matches scripts/seed_access_reviews.py)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from werkzeug.security import generate_password_hash  # noqa: E402

from apps.api.main import create_app  # noqa: E402
from shared.utils.village_id import generate_village_id  # noqa: E402

# Demo tenant/org/admin identity — shared with seed_cloud_discovery.py
DEMO_TENANT_SLUG = os.environ.get("DEMO_TENANT_SLUG", "demo-cloud-discovery")
DEMO_TENANT_NAME = os.environ.get("DEMO_TENANT_NAME", "Demo Cloud Discovery")
DEMO_ORG_NAME = os.environ.get("DEMO_ORG_NAME", "Demo Cloud Discovery Org")
DEMO_ADMIN_USERNAME = os.environ.get("DEMO_ADMIN_USERNAME", "demo-admin@elderrms.app")
DEMO_ADMIN_PASSWORD = os.environ.get("DEMO_ADMIN_PASSWORD") or secrets.token_urlsafe(12)


def _resolve_or_create_demo_tenant(db: Any) -> int:
    """Return the demo tenant (reuses seed_cloud_discovery.py logic)."""
    for slug in ("system", "default"):
        existing = db(db.tenants.slug == slug).select().first()
        if existing:
            return int(existing.id)

    existing = db(db.tenants.slug == DEMO_TENANT_SLUG).select().first()
    if existing:
        return int(existing.id)

    now = datetime.now(UTC)
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
    """Return the demo organization (reuses seed_cloud_discovery.py logic)."""
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

    now = datetime.now(UTC)
    org_id = db.organizations.insert(
        tenant_id=tenant_id,
        name=DEMO_ORG_NAME,
        type="organization",
        description="Seeded demo organization for support/CRM",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return int(org_id)


def _resolve_or_create_demo_admin(db: Any, tenant_id: int) -> int:
    """Return the demo admin identity (reuses seed_cloud_discovery.py logic)."""
    existing = db(db.identities.username == DEMO_ADMIN_USERNAME).select().first()
    if existing:
        return int(existing.id)

    now = datetime.now(UTC)
    pwd_hash = generate_password_hash(DEMO_ADMIN_PASSWORD)

    admin_id = db.identities.insert(
        tenant_id=tenant_id,
        username=DEMO_ADMIN_USERNAME,
        email=DEMO_ADMIN_USERNAME,
        full_name="Demo Admin",
        identity_type="human",
        auth_provider="local",
        password_hash=pwd_hash,
        is_active=True,
        is_superuser=True,
        mfa_enabled=False,
        must_change_password=False,
        portal_role="admin",
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return int(admin_id)


def _mint_village_id(tenant_id: int, redis_client: Any | None) -> str:
    """Mint a village_id via Redis, falling back to a random id in tests."""
    if redis_client:
        return generate_village_id(tenant_id, redis_client)
    return f"seed-{uuid4().hex[:8]}"


def seed_org_units(db: Any, tenant_id: int) -> dict[str, int]:
    """Seed customer companies and team organizational units.

    Returns a dict mapping org names to their IDs for later reference.
    """
    now = datetime.now(UTC)
    created_orgs = {}

    # Create 2 customer companies
    for company_name in ("Acme Corp", "Globex Industries"):
        existing = (
            db(
                (db.organizations.tenant_id == tenant_id)
                & (db.organizations.name == company_name)
            )
            .select()
            .first()
        )
        if existing:
            created_orgs[company_name] = int(existing.id)
            print(f"  {company_name}: {existing.id} (existing)")
            continue

        org_id = db.organizations.insert(
            tenant_id=tenant_id,
            name=company_name,
            type="customer_company",
            description=f"Demo customer: {company_name}",
            is_active=True,
            settings=json.dumps({"auto_close_days": 7}),
            created_at=now,
            updated_at=now,
        )
        db.commit()
        created_orgs[company_name] = int(org_id)
        print(f"  {company_name}: {org_id} (created)")

    # Create 1 team
    team_name = "Support Team"
    existing = (
        db(
            (db.organizations.tenant_id == tenant_id)
            & (db.organizations.name == team_name)
        )
        .select()
        .first()
    )
    if existing:
        created_orgs[team_name] = int(existing.id)
        print(f"  {team_name}: {existing.id} (existing)")
    else:
        org_id = db.organizations.insert(
            tenant_id=tenant_id,
            name=team_name,
            type="team",
            description="Internal support team",
            is_active=True,
            settings=json.dumps({"auto_close_days": 14}),
            created_at=now,
            updated_at=now,
        )
        db.commit()
        created_orgs[team_name] = int(org_id)
        print(f"  {team_name}: {org_id} (created)")

    return created_orgs


def seed_customer_contacts(
    db: Any, tenant_id: int, redis_client: Any | None
) -> dict[str, int]:
    """Seed customer contact identities (external CRM contacts).

    Returns a dict mapping email to identity IDs.
    """
    now = datetime.now(UTC)
    created_contacts = {}

    for email, company in [
        ("alice@acme.local", "Acme Corp"),
        ("bob@acme.local", "Acme Corp"),
        ("charlie@globex.local", "Globex Industries"),
        ("diana@globex.local", "Globex Industries"),
    ]:
        contact_username = f"contact:{tenant_id}:{email}"
        existing = (
            db(
                (db.identities.username == contact_username)
                & (db.identities.identity_type == "customer_contact")
            )
            .select()
            .first()
        )
        if existing:
            created_contacts[email] = int(existing.id)
            print(f"  {email}: {existing.id} (existing)")
            continue

        try:
            contact_id = db.identities.insert(
                username=contact_username,
                email=email,
                identity_type="customer_contact",
                auth_provider="local",
                is_active=True,
                is_superuser=False,
                mfa_enabled=False,
                must_change_password=False,
                portal_role="observer",
                tenant_id=tenant_id,
                metadata=json.dumps({"company": company, "phone": "+1-555-0100"}),
                village_id=_mint_village_id(tenant_id, redis_client),
                created_at=now,
                updated_at=now,
            )
            db.commit()
            created_contacts[email] = int(contact_id)
            print(f"  {email}: {contact_id} (created)")
        except Exception as e:
            # Idempotency: concurrent insert may have won; retry select
            db.rollback()
            existing = (
                db(
                    (db.identities.username == contact_username)
                    & (db.identities.identity_type == "customer_contact")
                )
                .select()
                .first()
            )
            if existing:
                created_contacts[email] = int(existing.id)
                print(f"  {email}: {existing.id} (after rollback)")
            else:
                raise

    return created_contacts


def seed_support_bot(db: Any, tenant_id: int, redis_client: Any | None) -> int:
    """Seed a support-bot service account for issue assignments."""
    bot_username = "support-bot"
    existing = (
        db(
            (db.identities.username == bot_username)
            & (db.identities.identity_type == "service_account")
        )
        .select()
        .first()
    )
    if existing:
        print(f"  {bot_username}: {existing.id} (existing)")
        return int(existing.id)

    now = datetime.now(UTC)
    bot_id = db.identities.insert(
        username=bot_username,
        email="support-bot@elderrms.app",
        identity_type="service_account",
        auth_provider="local",
        is_active=True,
        is_superuser=False,
        mfa_enabled=False,
        must_change_password=False,
        portal_role="admin",
        tenant_id=tenant_id,
        village_id=_mint_village_id(tenant_id, redis_client),
        created_at=now,
        updated_at=now,
    )
    db.commit()
    print(f"  {bot_username}: {bot_id} (created)")
    return int(bot_id)


def seed_support_issues(
    db: Any,
    tenant_id: int,
    redis_client: Any | None,
    admin_id: int,
    bot_id: int,
    customer_contacts: dict[str, int],
    org_units: dict[str, int],
) -> list[int]:
    """Seed support and non-support issues."""
    now = datetime.now(UTC)
    created_issue_ids = []

    issues = [
        {
            "title": "Payment processing fails on checkout",
            "description": "Our customers are unable to complete purchases. Error: 'Gateway timeout' appears after 5 seconds.",
            "issue_type": "SUPPORT",
            "status": "OPEN",
            "priority": "URGENT",
            "channel": "email",
            "category": "billing",
            "requester_id": admin_id,
            "resource_type": "organization",
            "resource_id": org_units["Acme Corp"],
            "assignee_type": "identity",
            "assignee_id": bot_id,
        },
        {
            "title": "API documentation missing authentication examples",
            "description": "The REST API docs at /docs do not show how to pass JWT tokens. This is blocking integrations.",
            "issue_type": "SUPPORT",
            "status": "IN_PROGRESS",
            "priority": "HIGH",
            "channel": "web",
            "category": "technical",
            "requester_id": admin_id,
            "resource_type": "organization",
            "resource_id": org_units["Acme Corp"],
            "assignee_type": "org_unit",
            "assignee_id": org_units["Support Team"],
        },
        {
            "title": "Database connection pool exhaustion under load",
            "description": "After ~500 concurrent users, queries start timing out. Max pool size is 10 (default). Need guidance on scaling.",
            "issue_type": "SUPPORT",
            "status": "OPEN",
            "priority": "HIGH",
            "channel": "email",
            "category": "technical",
            "requester_id": admin_id,
            "resource_type": "organization",
            "resource_id": org_units["Globex Industries"],
            "assignee_type": "identity",
            "assignee_id": bot_id,
        },
        {
            "title": "Feature request: Bulk user import via CSV",
            "description": "Currently we manually create users one by one. A bulk import would save hours during onboarding.",
            "issue_type": "SUPPORT",
            "status": "OPEN",
            "priority": "MEDIUM",
            "channel": "web",
            "category": "feature",
            "requester_id": admin_id,
            "resource_type": "organization",
            "resource_id": org_units["Globex Industries"],
            "assignee_type": None,
            "assignee_id": None,
        },
        {
            "title": "XSS vulnerability in user-profile template",
            "description": "Custom fields on user profile are not escaped; injecting <img onerror=alert(1)> renders in the UI.",
            "issue_type": "SUPPORT",
            "status": "RESOLVED",
            "priority": "CRITICAL",
            "channel": "email",
            "category": "security",
            "requester_id": admin_id,
            "resource_type": "organization",
            "resource_id": org_units["Acme Corp"],
            "assignee_type": "identity",
            "assignee_id": bot_id,
        },
        # Non-support issues
        {
            "title": "Refactor authentication middleware",
            "description": "Auth middleware is too tightly coupled to Flask; extract to decorator for reuse in gRPC.",
            "issue_type": "CODE",
            "status": "OPEN",
            "priority": "MEDIUM",
            "channel": None,
            "category": None,
            "requester_id": admin_id,
            "resource_type": "organization",
            "resource_id": org_units["Demo Cloud Discovery Org"],
            "assignee_type": None,
            "assignee_id": None,
        },
        {
            "title": "Upgrade to Python 3.13",
            "description": "Current version is 3.12. Upgrade for async improvements and performance.",
            "issue_type": "FEATURE",
            "status": "OPEN",
            "priority": "LOW",
            "channel": None,
            "category": None,
            "requester_id": admin_id,
            "resource_type": "organization",
            "resource_id": org_units["Demo Cloud Discovery Org"],
            "assignee_type": "identity",
            "assignee_id": admin_id,
        },
    ]

    for issue_data in issues:
        # Check idempotency by title + resource
        existing = (
            db(
                (db.issues.tenant_id == tenant_id)
                & (db.issues.title == issue_data["title"])
                & (db.issues.resource_type == issue_data["resource_type"])
                & (db.issues.resource_id == issue_data["resource_id"])
            )
            .select()
            .first()
        )
        if existing:
            created_issue_ids.append(int(existing.id))
            print(f"  {issue_data['title'][:50]}: {existing.id} (existing)")
            continue

        issue_id = db.issues.insert(
            tenant_id=tenant_id,
            title=issue_data["title"],
            description=issue_data["description"],
            issue_type=issue_data["issue_type"],
            status=issue_data["status"],
            priority=issue_data["priority"],
            channel=issue_data.get("channel"),
            category=issue_data.get("category"),
            reporter_id=issue_data.get("requester_id"),
            resource_type=issue_data["resource_type"],
            resource_id=issue_data["resource_id"],
            assignee_type=issue_data.get("assignee_type"),
            assignee_id=issue_data.get("assignee_id"),
            is_incident=0,
            village_id=_mint_village_id(tenant_id, redis_client),
            created_at=now,
            updated_at=now,
        )
        db.commit()
        created_issue_ids.append(int(issue_id))
        print(f"  {issue_data['title'][:50]}: {issue_id} (created)")

    return created_issue_ids


def seed_issue_comments(
    db: Any,
    tenant_id: int,
    redis_client: Any | None,
    admin_id: int,
    customer_contacts: dict[str, int],
    first_issue_id: int,
) -> None:
    """Seed issue comments with email metadata for one issue."""
    now = datetime.now(UTC)

    # Find or create comments on the first issue
    existing_count = db(db.issue_comments.issue_id == first_issue_id).count()
    if existing_count > 0:
        print(f"  Issue {first_issue_id}: {existing_count} comments (existing)")
        return

    comments = [
        {
            "content": "Hi, we're investigating the payment gateway timeout. Initial analysis shows it's a rate-limiting issue at our provider. ETA: 4 hours.",
            "author_id": admin_id,
            "metadata": {
                "channel": "email",
                "direction": "outbound",
                "from": "support@elderrms.app",
                "to": "alice@acme.local",
                "message_id": f"<demo-msg-{uuid4().hex[:8]}@elderrms.app>",
            },
        },
        {
            "content": "Thanks for the update. Our checkout page is handling the error gracefully now, but we're concerned this could happen again during peak hours.",
            "author_id": customer_contacts["alice@acme.local"],
            "metadata": {
                "channel": "email",
                "direction": "inbound",
                "from": "alice@acme.local",
                "to": "support@elderrms.app",
                "message_id": "<alice-reply-123@acme.local>",
            },
        },
        {
            "content": "We've increased the rate limit for your account to 5K req/min and implemented a circuit breaker on our end. This should resolve future occurrences. Testing complete.",
            "author_id": admin_id,
            "metadata": {
                "channel": "email",
                "direction": "outbound",
                "from": "support@elderrms.app",
                "to": "alice@acme.local",
                "message_id": f"<demo-msg-{uuid4().hex[:8]}@elderrms.app>",
            },
        },
    ]

    for comment_data in comments:
        comment_id = db.issue_comments.insert(
            tenant_id=tenant_id,
            issue_id=first_issue_id,
            author_id=comment_data["author_id"],
            content=comment_data["content"],
            # The DB column is literally "metadata" (the SQLAlchemy attr is renamed
            # comment_metadata to dodge the reserved name, but penguin-dal addresses
            # the real column name) — same pattern as the identities insert above.
            metadata=json.dumps(comment_data["metadata"]),
            village_id=_mint_village_id(tenant_id, redis_client),
            created_at=now,
            updated_at=now,
        )
        db.commit()
        print(f"  Comment on issue {first_issue_id}: {comment_id} (created)")


def seed_streams(
    db: Any,
    tenant_id: int,
    redis_client: Any | None,
    admin_id: int,
) -> list[int]:
    """Seed demo stream playbooks and executions for the tenant.

    Returns a list of stream playbook IDs created.
    """
    now = datetime.now(UTC)
    created_stream_ids = []

    streams = [
        {
            "name": "Payment Processing Workflow",
            "description": "Automated workflow for processing customer payments",
            "trigger_type": "webhook",
            "is_template": False,
        },
        {
            "name": "Data Import Pipeline",
            "description": "Bulk import and transformation pipeline for customer data",
            "trigger_type": "manual",
            "is_template": True,
        },
    ]

    for stream_data in streams:
        # Idempotency check by name + tenant
        existing = (
            db(
                (db.stream_playbooks.tenant_id == tenant_id)
                & (db.stream_playbooks.name == stream_data["name"])
            )
            .select()
            .first()
        )
        if existing:
            created_stream_ids.append(int(existing.id))
            print(f"  {stream_data['name']}: {existing.id} (existing)")
            continue

        # Create stream playbook
        stream_id = db.stream_playbooks.insert(
            tenant_id=tenant_id,
            village_id=_mint_village_id(tenant_id, redis_client),
            name=stream_data["name"],
            description=stream_data["description"],
            owner_identity_id=admin_id,
            created_by_identity_id=admin_id,
            trigger_type=stream_data["trigger_type"],
            # Public so the demo's global executions view shows them regardless of
            # which admin identity is logged in (owner-only would hide them from
            # the bootstrap admin). _can_read_stream lets any tenant user read a
            # public stream.
            is_public=True,
            is_template=stream_data.get("is_template", False),
            is_enabled=True,
            status="active",
            tags=["demo"],
            execution_count=0,
            success_count=0,
            failure_count=0,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Create demo executions for this stream
        statuses = ["completed", "running", "failed"]
        for i, status in enumerate(statuses):
            exec_uuid = str(uuid4())
            start_time = now - timedelta(hours=3 - i)
            end_time = (
                start_time + timedelta(seconds=45) if status == "completed" else None
            )
            duration = 45000 if status == "completed" else None

            db.stream_executions.insert(
                tenant_id=tenant_id,
                playbook_id=stream_id,
                execution_id=exec_uuid,
                status=status,
                trigger_type="manual",
                triggered_by_identity_id=admin_id,
                input_json={"sample": "input", "timestamp": start_time.isoformat()},
                output_json={"result": "success"} if status == "completed" else None,
                error_message="Execution timeout" if status == "failed" else None,
                started_at=start_time,
                completed_at=end_time,
                duration_ms=duration,
                created_at=start_time,
                updated_at=start_time if end_time is None else end_time,
            )
            db.commit()

        created_stream_ids.append(int(stream_id))
        print(f"  {stream_data['name']}: {stream_id} (created, 3 executions)")

    return created_stream_ids


def seed_webhooks(
    db: Any,
    tenant_id: int,
    redis_client: Any | None,
    bot_id: int,
    org_units: dict[str, int],
) -> None:
    """Seed webhook configurations for issue.assigned events."""
    now = datetime.now(UTC)

    webhooks = [
        {
            "name": "Support Bot Assignment Alert",
            "url": "https://example.com/webhook/bot-assigned",
            "is_active": True,
            "events": json.dumps(["issue.assigned"]),
            "filter_issue_type": "SUPPORT",
            "filter_assignee_type": "identity",
            "filter_assignee_id": bot_id,
        },
        {
            "name": "Support Team Assignment Alert",
            "url": "https://example.com/webhook/team-assigned",
            "is_active": True,
            "events": json.dumps(["issue.assigned"]),
            "filter_issue_type": None,
            "filter_assignee_type": "org_unit",
            "filter_assignee_id": org_units["Support Team"],
        },
    ]

    for webhook_data in webhooks:
        # Idempotency: check if this exact webhook exists
        existing = (
            db(
                (db.webhooks.tenant_id == tenant_id)
                & (db.webhooks.url == webhook_data["url"])
                & (db.webhooks.filter_assignee_id == webhook_data["filter_assignee_id"])
            )
            .select()
            .first()
        )
        if existing:
            print(f"  {webhook_data['name']}: {existing.id} (existing)")
            continue

        webhook_id = db.webhooks.insert(
            tenant_id=tenant_id,
            name=webhook_data["name"],
            url=webhook_data["url"],
            is_active=webhook_data["is_active"],
            events=webhook_data["events"],
            filter_issue_type=webhook_data.get("filter_issue_type"),
            filter_assignee_type=webhook_data.get("filter_assignee_type"),
            filter_assignee_id=webhook_data.get("filter_assignee_id"),
            village_id=_mint_village_id(tenant_id, redis_client),
            created_at=now,
            updated_at=now,
        )
        db.commit()
        print(f"  {webhook_data['name']}: {webhook_id} (created)")


def seed_demo_unified() -> None:
    """Seed the complete unified demo data."""
    print("Seeding demo support/CRM experience (unified native model)...\n")

    app = create_app()
    db = app.db
    redis_client = app.redis_client

    # Resolve or create shared demo tenant/org/admin
    print("Resolving demo tenant/org/admin (shared with seed_cloud_discovery):")
    tenant_id = _resolve_or_create_demo_tenant(db)
    org_id = _resolve_or_create_demo_org(db, tenant_id)
    admin_id = _resolve_or_create_demo_admin(db, tenant_id)
    print(f"  tenant_id={tenant_id}, org_id={org_id}, admin_id={admin_id}\n")

    # Seed organizational units
    print("Seeding organizational units (customer companies + teams):")
    org_units = seed_org_units(db, tenant_id)
    org_units["Demo Cloud Discovery Org"] = org_id
    print()

    # Seed customer contacts
    print("Seeding customer contact identities:")
    customer_contacts = seed_customer_contacts(db, tenant_id, redis_client)
    print()

    # Seed support bot
    print("Seeding support-bot service account:")
    bot_id = seed_support_bot(db, tenant_id, redis_client)
    print()

    # Seed issues
    print("Seeding support and non-support issues:")
    issue_ids = seed_support_issues(
        db, tenant_id, redis_client, admin_id, bot_id, customer_contacts, org_units
    )
    print()

    # Seed issue comments on first issue
    if issue_ids:
        print("Seeding issue comments (email thread on first issue):")
        seed_issue_comments(
            db, tenant_id, redis_client, admin_id, customer_contacts, issue_ids[0]
        )
        print()

    # Seed webhooks
    print("Seeding webhook configurations:")
    seed_webhooks(db, tenant_id, redis_client, bot_id, org_units)
    print()

    # Seed streams
    print("Seeding stream playbooks and executions:")
    stream_ids = seed_streams(db, tenant_id, redis_client, admin_id)
    print()

    # Summary
    print("=" * 72)
    print("DEMO DATA SEEDED — unified native model ready")
    print("=" * 72)
    print(f"  tenant_id       : {tenant_id}")
    print(f"  login email     : {DEMO_ADMIN_USERNAME}")
    print(f"  password        : {DEMO_ADMIN_PASSWORD}")
    print(f"  support issues  : {sum(1 for iid in issue_ids)}")
    print(f"  customer_contacts : {len(customer_contacts)}")
    print(f"  org_units       : {len(org_units)}")
    print("=" * 72)


if __name__ == "__main__":
    try:
        seed_demo_unified()
    except Exception as e:  # noqa: BLE001 - top-level script error boundary
        print(f"Error seeding demo unified data: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
