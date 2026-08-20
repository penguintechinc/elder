"""Per-tenant/global counters backing the license-enforcement guard.

All functions are synchronous penguin-dal queries (mirrors
apps/api/common/modules/tenant_toggle.py) -- call them from async code via
``apps.api.utils.async_utils.run_in_threadpool``, never directly in a
coroutine.

Admin-granularity heuristic (documented, approximate)
------------------------------------------------------
Elder has two independent login paths with no shared foreign key between
them: IAM `identities` (superuser flag, `portal_role`) and portal
`portal_users` (`global_role`, `tenant_role`). Distinguishing "global admin"
from "tenant admin" is therefore a heuristic, not a precise join, pending the
RBAC model cleanup (see the Phase 2 plan's Out of scope):

- **Global admin** = `Identity.is_superuser` (any tenant), OR
  `Identity.portal_role == 'admin'` *on the deployment's default/system
  tenant* (the common single-tenant case -- Free/Professional have exactly
  one tenant), OR `PortalUser.global_role in ('admin', 'superadmin')`.
- **Tenant admin** = `PortalUser.tenant_role == 'admin'`, OR
  `Identity.portal_role == 'admin'` on any tenant that is *not* the
  default/system tenant (non-superuser). This is the exact complement of the
  identity-side global-admin branch, so the two counters never double-count
  the same identity row.

Because identities and portal_users have no shared key, a person who holds
both an identity and a portal_user row can be counted once per table --
this over-counts rather than under-counts, which is the safer failure mode
for a limit-enforcement counter.
"""

# flake8: noqa: E501

from typing import Any

import structlog

from apps.api.models.service_node import count_active_nodes

logger = structlog.get_logger()

# Tenant-scoped tables that carry a village_id (cross-object reference) AND a
# `tenant_id` column, i.e. every table Free tier's 1000-object quota counts
# against. Of the 32 village_id-carrying models, 3 have no tenant_id column
# and are excluded here: `entities` (apps/api/modules/infrastructure/models/
# entity.py), `resource_roles` (apps/api/modules/access_reviews/models/
# resource_role.py), `metadata_fields` (apps/api/modules/issues/models/
# metadata.py) -- all three are scoped indirectly, via the
# organization/entity they attach to, not a direct column.
OBJECT_TABLES: tuple[str, ...] = (
    "dg_diagrams",
    "dg_collections",
    "dg_templates",
    "doc_documents",
    "projects",
    "milestones",
    "access_reviews",
    "group_access_requests",
    "organizations",
    "hd_intake_forms",
    "hd_companies",
    "hd_contacts",
    "hd_teams",
    "hd_tickets",
    "dependencies",
    "issues",
    "issue_comments",
    "data_stores",
    "iceflows",
    "stream_playbooks",
    "stream_templates",
    "ipam_prefixes",
    "ipam_addresses",
    "ipam_vlans",
    "on_call_rotations",
    "pg_pages",
    "services",
    "software",
    "webhooks",
)


def _default_tenant_id(db: Any) -> int | None:
    """Best-effort lookup of the deployment's default/system tenant id.

    Mirrors the bootstrap heuristic in shared.database._create_default_admin:
    tries slug 'system' first, falling back to 'default'. Used only to
    distinguish "global admin" from "tenant admin" -- see module docstring.
    """
    row = db(db.tenants.slug == "system").select().first()
    if not row:
        row = db(db.tenants.slug == "default").select().first()
    return row.id if row else None


def count_users(db: Any, tenant_id: int) -> int:
    """Count all identities + portal_users belonging to tenant_id.

    Elder's two login paths have no shared key, so this is a sum of both
    tables' tenant-scoped row counts, not a deduplicated person count.

    Args:
        db: penguin-dal DAL instance.
        tenant_id: Tenant to scope the count to.

    Returns:
        int: identities count + portal_users count for tenant_id.
    """
    return (
        db(db.identities.tenant_id == tenant_id).count()
        + db(db.portal_users.tenant_id == tenant_id).count()
    )


def count_global_admins(db: Any, tenant_id: int) -> int:
    """Count global-admin rows scoped to tenant_id.

    See module docstring for the heuristic.

    Args:
        db: penguin-dal DAL instance.
        tenant_id: Tenant to scope the count to.

    Returns:
        int: Number of global-admin identities + portal_users for tenant_id.
    """
    default_tenant_id = _default_tenant_id(db)

    identity_query = (db.identities.tenant_id == tenant_id) & (
        db.identities.is_superuser == True  # noqa: E712
    )
    if tenant_id == default_tenant_id:
        identity_query = identity_query | (
            (db.identities.tenant_id == tenant_id)
            & (db.identities.portal_role == "admin")
        )
    identities_admin = db(identity_query).count()

    portal_admin = db(
        (db.portal_users.tenant_id == tenant_id)
        & (db.portal_users.global_role.belongs(["admin", "superadmin"]))
    ).count()

    return identities_admin + portal_admin


def count_tenant_admins(db: Any, tenant_id: int) -> int:
    """Count tenant-admin rows scoped to tenant_id.

    See module docstring for the heuristic. Deliberately the complement of
    count_global_admins' identity-side branch (same predicate, opposite
    default-tenant condition, and superusers always excluded) so the two
    counters never double-count the same identity row.

    Args:
        db: penguin-dal DAL instance.
        tenant_id: Tenant to scope the count to.

    Returns:
        int: Number of tenant-admin identities + portal_users for tenant_id.
    """
    default_tenant_id = _default_tenant_id(db)

    portal_admin = db(
        (db.portal_users.tenant_id == tenant_id)
        & (db.portal_users.tenant_role == "admin")
    ).count()

    identities_admin = 0
    if tenant_id != default_tenant_id:
        identities_admin = db(
            (db.identities.tenant_id == tenant_id)
            & (db.identities.portal_role == "admin")
            & (db.identities.is_superuser == False)  # noqa: E712
        ).count()

    return portal_admin + identities_admin


def count_teams(db: Any, tenant_id: int) -> int:
    """Count organizations of type='team' scoped to tenant_id.

    'team' as an organization type is introduced by this license-enforcement
    framework -- Task 7 of the Phase 2 plan is what starts writing it on
    create. This counter is forward-looking: it returns 0 until team
    creation is wired.

    Args:
        db: penguin-dal DAL instance.
        tenant_id: Tenant to scope the count to.

    Returns:
        int: Number of type='team' organizations for tenant_id.
    """
    return db(
        (db.organizations.tenant_id == tenant_id) & (db.organizations.type == "team")
    ).count()


def count_tenants(db: Any) -> int:
    """Count all tenants in the deployment.

    Global, not tenant-scoped -- the `max_tenants` limit bounds how many
    tenants may exist at all, not how many a given tenant "owns".

    Args:
        db: penguin-dal DAL instance.

    Returns:
        int: Total tenant count.
    """
    return db(db.tenants.id > 0).count()


def count_objects(db: Any, tenant_id: int, redis_client: Any = None) -> int:
    """Sum tenant-scoped rows across every OBJECT_TABLES table for tenant_id.

    Cached in Redis for 60s per tenant (`elder:objcount:{tenant:08x}`,
    distinct from the village_id sequence-counter key `elder:vid:*` in
    shared/utils/village_id.py) since this is a ~29-table fan-out otherwise.
    Caching is best-effort: any Redis failure just falls back to a live
    recount, and `redis_client=None` skips caching entirely.

    Each table is read in its own try/except so one missing/unreflected
    table (e.g. a disabled optional module whose migrations never ran)
    degrades that table's contribution to 0 instead of failing the whole
    count.

    Args:
        db: penguin-dal DAL instance.
        tenant_id: Tenant to scope the count to.
        redis_client: Optional Redis client for caching (sync `redis.Redis`
            or any object exposing `.get(key)` / `.setex(key, ttl, value)`).

    Returns:
        int: Total object count across OBJECT_TABLES for tenant_id.
    """
    cache_key = f"elder:objcount:{tenant_id:08x}"

    if redis_client is not None:
        try:
            cached = redis_client.get(cache_key)
            if cached is not None:
                return int(cached)
        except Exception as exc:
            logger.debug("license_object_count_cache_read_failed", error=str(exc))

    total = 0
    for table_name in OBJECT_TABLES:
        try:
            # penguin-dal's DB resolves dynamic table names via __getattr__
            # only -- it does not support subscript (`db[table_name]`)
            # access, unlike a TableProxy's row-by-id lookup.
            table = getattr(db, table_name)
            total += db(table.tenant_id == tenant_id).count()
        except Exception as exc:
            logger.debug(
                "license_object_count_table_skipped",
                table=table_name,
                error=str(exc),
            )

    if redis_client is not None:
        try:
            redis_client.setex(cache_key, 60, total)
        except Exception as exc:
            logger.debug("license_object_count_cache_write_failed", error=str(exc))

    return total


# Task 2's node counter, re-exported under the licensing package for a
# single import surface (apps.api.common.licensing.counters.count_nodes).
count_nodes = count_active_nodes
