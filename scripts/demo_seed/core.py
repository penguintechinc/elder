"""Core demo org: tenant bootstrap, staff identities, departments and teams."""

from __future__ import annotations

from typing import Any

from .common import SeedContext, get_or_create, now

# Fake staff — reserved example.com addresses only, no real people.
STAFF = (
    ("alex.rivera", "Alex Rivera", "maintainer"),
    ("sam.okafor", "Sam Okafor", "maintainer"),
    ("priya.nair", "Priya Nair", "viewer"),
)

TEAMS = (
    ("Platform Engineering", "Runs the Acme compute, network and delivery stack."),
    ("Security & Compliance", "Owns vulnerability triage, secrets and access reviews."),
    ("Site Reliability", "On-call, incident response and capacity planning."),
)


def ensure_login_tenant(db: Any) -> None:
    """Make sure a tenant exists that a tenant-less UI login resolves to.

    The portal login falls back to the ``system`` then ``default`` tenant; on a
    brand-new database neither exists yet, which would make the demo admin
    unreachable, so create ``default`` in that case only.
    """
    for slug in ("system", "default"):
        if db(db.tenants.slug == slug).select().first():
            return
    db.tenants.insert(
        name="Acme Corp",
        slug="default",
        subscription_tier="enterprise",
        is_active=True,
        created_at=now(),
        updated_at=now(),
    )
    db.commit()


def seed_staff(ctx: SeedContext) -> dict[str, int]:
    """Seed 3 fake staff identities (plus the admin created earlier)."""
    ids: dict[str, int] = {}
    for handle, full_name, role in STAFF:
        email = f"{handle}@example.com"
        ids[handle] = get_or_create(
            ctx,
            "identities",
            {"username": email},
            {
                "email": email,
                "full_name": full_name,
                "identity_type": "human",
                "auth_provider": "local",
                "is_active": True,
                "is_superuser": False,
                "mfa_enabled": False,
                "must_change_password": False,
                "portal_role": role,
                "organization_id": ctx.org_id,
                "metadata": {"title": "Demo staff", "office": "Remote"},
                "created_at": now(),
                "updated_at": now(),
            },
            feature="staff identities",
        )
    return ids


def seed_departments(ctx: SeedContext) -> int:
    """Seed one child organization (department) under the demo org."""
    return get_or_create(
        ctx,
        "organizations",
        {"tenant_id": ctx.tenant_id, "name": "Platform Engineering"},
        {
            "parent_id": ctx.org_id,
            "type": "department",
            "description": "Acme platform engineering department",
            "is_active": True,
            "created_at": now(),
            "updated_at": now(),
        },
        feature="departments",
    )


def seed_teams(ctx: SeedContext, staff: dict[str, int]) -> dict[str, int]:
    """Seed 3 identity groups (teams) with 1-2 members each."""
    members = {
        "Platform Engineering": ("alex.rivera", "sam.okafor"),
        "Security & Compliance": ("priya.nair", "sam.okafor"),
        "Site Reliability": ("alex.rivera",),
    }
    out: dict[str, int] = {}
    for name, description in TEAMS:
        gid = get_or_create(
            ctx,
            "identity_groups",
            {"name": name},
            {
                "description": description,
                "is_active": True,
                "review_enabled": False,
                "review_auto_apply": False,
                "created_at": now(),
                "updated_at": now(),
            },
            feature="teams",
        )
        out[name] = gid
        for handle in members[name]:
            get_or_create(
                ctx,
                "identity_group_memberships",
                {"identity_id": staff[handle], "group_id": gid},
                {"created_at": now(), "updated_at": now()},
                feature="team memberships",
            )
    return out
