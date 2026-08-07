"""Public intake-form submission: customer_contact upsert + native Issue creation.

Turns an anonymous public intake-form submission into two rows: a reused-or-
created `customer_contact` identity (tenant-scoped, keyed on a namespaced
username derived from email) and a native support Issue (see
`apps/api/modules/issues/models/issue.py`). Wires together the form model
(Task 1), the field validator (Task 2), and the Altcha challenge/verify pair
(Task 3) with the actual unauthenticated submit path.
"""

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from shared.utils.village_id import generate_village_id

#: Matches `issues.title` (VARCHAR(255) — see
#: apps/api/modules/issues/models/issue.py). A derived title longer than
#: this is truncated with an ellipsis rather than failing the insert; the
#: full text is still preserved in `description`.
_ISSUE_TITLE_MAX_LENGTH = 255

#: Matches `IssuePriority` (apps/api/modules/issues/models/issue.py). A
#: submitted `priority` outside this set is not a valid enum value for the
#: column, so it's defaulted to MEDIUM rather than passed through.
_VALID_ISSUE_PRIORITIES = {"LOW", "MEDIUM", "HIGH", "URGENT", "CRITICAL"}

#: Matches `IssueType` (apps/api/modules/issues/models/issue.py), lowercase
#: enum values. `hd_intake_forms.issue_type` is an unconstrained String(30)
#: — `intake_forms.py`'s admin routes allow-list new/updated values against
#: this same set, but a pre-existing/legacy form row could still carry a
#: stale out-of-enum value, so this is checked again defensively here too
#: (defense in depth): never let a bad value reach the strict Enum column
#: and 500 every public submit against that form.
_VALID_ISSUE_TYPES = {
    "operations",
    "code",
    "config",
    "security",
    "architecture",
    "process",
    "approval",
    "feature",
    "bug",
    "support",
    "other",
}


class ContactResolutionError(Exception):
    """Raised when a customer_contact identity can neither be found nor
    created (insert failed and a follow-up re-select still found nothing).

    The route maps this to a 409 rather than a 500 — this is an expected,
    if rare, concurrent-submission race, not a server bug.
    """


def _mint_village_id(tenant_id: int, redis_client: Optional[Any]) -> str:
    """Mint a village_id via Redis, falling back to a random id in tests.

    Mirrors the fallback used throughout the helpdesk routes (e.g.
    `intake_forms.create_form`) for environments without a live Redis
    connection, such as the unit test suite.
    """
    if redis_client:
        return generate_village_id(tenant_id, redis_client)
    return f"test-{uuid4().hex[:8]}"


def upsert_customer_contact(
    db: Any,
    tenant_id: int,
    email: str,
    details: Optional[dict[str, Any]],
    redis: Optional[Any],
) -> int:
    """Find or create a `customer_contact` identity for a public submitter.

    `identities.username` is GLOBALLY unique and is also where real staff
    accounts store their login (frequently their email). Keying a contact
    lookup/insert on the bare email would let an anonymous public submitter
    either (a) attach an issue to a real staff/admin identity whose username
    happens to match the submitted email, or (b) collide across tenants,
    since the same email submitted to two different tenants' forms would
    resolve to the same globally-unique username.

    Instead this keys on a namespaced `contact:{tenant_id}:{email}` username
    — globally unique, tenant-scoped, and structurally incapable of matching
    a real user's plain-email username. The real email is still stored in
    the `email` column for display/search. Returns the identity id either
    way — never creates a duplicate contact for the same email+tenant.

    Raises:
        ContactResolutionError: the insert failed (e.g. a concurrent
            request won a unique-constraint race) and a follow-up re-select
            still found no matching row.
    """
    contact_username = f"contact:{tenant_id}:{email}"

    def find_existing() -> Any:
        return (
            db(
                (db.identities.username == contact_username)
                & (db.identities.identity_type == "customer_contact")
            )
            .select()
            .first()
        )

    existing = find_existing()
    if existing:
        return existing.id

    now = datetime.now(timezone.utc)

    try:
        # penguin-dal's insert() does not apply SQLAlchemy Column `default=`
        # values, so every NOT NULL column is passed explicitly (see
        # tests/unit/test_crm_entity_types.py for the reference pattern).
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
            metadata=details,
            village_id=_mint_village_id(tenant_id, redis),
            created_at=now,
            updated_at=now,
        )
        db.commit()
        return contact_id
    except Exception:
        # Belt-and-suspenders: a concurrent submission may have inserted the
        # same contact_username between our lookup and this insert (unique
        # constraint collision on `username`). Roll back the failed insert
        # and re-select the now-existing row rather than 500ing.
        db.rollback()
        existing = find_existing()
        if existing:
            return existing.id
        raise ContactResolutionError(
            f"failed to create or resolve customer_contact identity for "
            f"tenant {tenant_id}"
        )


def _resolve_resource_id(db: Any, form: Any) -> Optional[int]:
    """Resolve the owning organization id for issues created from `form`.

    Uses the form's own `organization_id` when set; otherwise falls back to
    the tenant's first (lowest-id) organization. Returns None if neither is
    available so the caller can surface a clear error instead of failing a
    NOT NULL constraint on `issues.resource_id`.
    """
    if form.organization_id:
        return form.organization_id

    root_org = (
        db(db.organizations.tenant_id == form.tenant_id)
        .select(orderby=db.organizations.id, limitby=(0, 1))
        .first()
    )
    return root_org.id if root_org else None


def create_support_issue_from_form(
    db: Any,
    form: Any,
    validated: dict[str, Any],
    contact_id: int,
    redis: Optional[Any],
) -> Any:
    """Insert a native support Issue from a validated intake-form submission.

    Mirrors `issues/routes/issues.py::create_issue`'s field set for an
    anonymous submitter: `reporter_id` is the resolved customer_contact
    identity (never `g.current_user`, which doesn't exist here),
    `resource_type`/`resource_id` point at the form's owning organization,
    and a fresh village_id is minted as the public-safe reference returned
    to the submitter. `issue_type` honors the form's own admin-configured
    value (default "support") rather than a hardcoded constant, allow-listed
    against `IssueType` with a defensive fallback to "support" for any
    stale/out-of-enum value already on the row (see `_VALID_ISSUE_TYPES`);
    `priority` is allow-listed against `IssuePriority`, defaulting to MEDIUM
    for any unrecognized submitted value; `title` is truncated to fit
    `issues.title`'s VARCHAR(255) column, with the full text preserved in
    `description`. Raises ValueError if no owning organization can be
    resolved for the form's tenant.

    Returns:
        The created issue row (all columns, including village_id, id,
        assignee_id, assignee_type, issue_type, status, tenant_id — the
        caller uses these to decide whether to fire an issue.assigned
        webhook, since this function itself cannot: it runs inside
        run_in_threadpool, off the event loop, where asyncio.create_task
        has no running loop to attach to).
    """
    resource_id = _resolve_resource_id(db, form)
    if not resource_id:
        raise ValueError(
            f"no organization available for tenant {form.tenant_id}; "
            "cannot create support issue"
        )

    now = datetime.now(timezone.utc)

    raw_title = str(validated.get("subject") or form.name)
    if len(raw_title) > _ISSUE_TITLE_MAX_LENGTH:
        title = raw_title[: _ISSUE_TITLE_MAX_LENGTH - 1] + "…"
    else:
        title = raw_title
    description = "\n".join(
        f"{key}: {value}" for key, value in validated.items() if value is not None
    )

    priority = str(validated.get("priority") or "medium").upper()
    if priority not in _VALID_ISSUE_PRIORITIES:
        priority = "MEDIUM"

    issue_type = str(form.issue_type or "support").lower()
    if issue_type not in _VALID_ISSUE_TYPES:
        # Defensive fallback: intake_forms.py allow-lists issue_type at
        # create/update time, but a pre-existing/legacy form row could still
        # carry a stale out-of-enum value — never let that reach the strict
        # Enum column and 500 every public submit against the form.
        issue_type = "support"

    insert_data: dict[str, Any] = {
        "title": title,
        "description": description,
        "status": "OPEN",
        "priority": priority,
        "issue_type": issue_type.upper(),
        "is_incident": 0,
        "channel": "web",
        "reporter_id": contact_id,
        "resource_type": "organization",
        "resource_id": resource_id,
        "tenant_id": form.tenant_id,
        "village_id": _mint_village_id(form.tenant_id, redis),
        "created_at": now,
        "updated_at": now,
    }

    if form.default_assignee_type and form.default_assignee_id:
        insert_data["assignee_type"] = form.default_assignee_type
        insert_data["assignee_id"] = form.default_assignee_id

    issue_id = db.issues.insert(**insert_data)
    db.commit()

    return db(db.issues.id == issue_id).select().first()
