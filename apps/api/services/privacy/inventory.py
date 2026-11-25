"""Personal-data inventory: every table/column that links to a data subject.

Single source of truth for right-to-erasure (GDPR Art. 17) and
access/export (Art. 15): PrivacyService walks these entries in order, the
erasure receipt reports them, and tests/unit/test_privacy_inventory.py fails
when a model gains a foreign key to ``identities`` / ``portal_users`` that
is not listed here -- so a new table cannot silently escape erasure.

Human-readable copy: docs/compliance/dsar-erasure-inventory.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class SubjectType(IntEnum):
    """Which human-identity table a data subject lives in."""

    IDENTITY = 1
    PORTAL_USER = 2


class Action(IntEnum):
    """What erasure does to rows linked to the subject through one column."""

    ANONYMIZE = 1  # overwrite the PII columns of the subject's own row
    DELETE = 2  # delete the linked rows outright
    UNLINK = 3  # NULL the link (and any `scrub` columns), keep the row
    RETAIN = 4  # keep row + link; it now points at an anonymised tombstone
    SCRUB_MATCH = 5  # NULL a free-text column where it equals a subject alias


class ReceiptStatus(IntEnum):
    """Lifecycle of a privacy_erasure_receipts row."""

    IN_PROGRESS = 1
    COMPLETED = 2


class ConsentPurpose(IntEnum):
    """Processing purpose a consent / opt-out record applies to."""

    SALE_OR_SHARING = 1  # CCPA/CPRA "Do Not Sell or Share"
    ANALYTICS = 2
    MARKETING = 3


class ConsentState(IntEnum):
    """Whether the subject currently permits the purpose."""

    GRANTED = 1
    WITHDRAWN = 2


class LawfulBasis(IntEnum):
    """GDPR Art. 6(1) basis recorded with each consent-ledger entry."""

    CONSENT = 1  # Art. 6(1)(a)
    LEGAL_OBLIGATION = 2  # Art. 6(1)(c) -- honouring an opt-out
    LEGITIMATE_INTEREST = 3  # Art. 6(1)(f)


# Legal-basis texts quoted verbatim in the erasure receipt. No PII.
LEGAL_BASIS: dict[str, str] = {
    "erasure": "GDPR Art. 17(1) / CCPA 1798.105 -- erased on request",
    "security_audit": (
        "GDPR Art. 17(3)(b),(e) + Art. 6(1)(c),(f) -- security audit trail "
        "retained in pseudonymised form: event kept, identity link / IP / "
        "user agent removed where the schema allows"
    ),
    "business_record": (
        "GDPR Art. 6(1)(f) -- tenant business record retained; its author "
        "link now resolves only to an irreversibly anonymised tombstone row"
    ),
    "access_review_evidence": (
        "GDPR Art. 17(3)(b),(e) + Art. 6(1)(c),(f) -- evidence of a completed "
        "access review (SOC 2 CC6.2/CC6.3) retained; the reviewed subject "
        "link now resolves only to an irreversibly anonymised tombstone row"
    ),
    "accountability": (
        "GDPR Art. 5(2) / Art. 7(1) -- record needed to demonstrate that a "
        "statutory request or consent choice was honoured; ids only"
    ),
}


@dataclass(frozen=True, slots=True)
class InventoryEntry:
    """One subject-linked column and the erasure/export treatment it gets.

    ``column`` holds the subject's id (or, for SCRUB_MATCH, a free-text alias
    such as a username). ``scrub`` lists further PII columns on the same rows
    that are NULLed together with the link. ``export`` is the explicit column
    allow-list returned by the Art. 15 export -- never the whole row.
    """

    table: str
    column: str
    action: Action
    category: str
    basis: str = "erasure"
    scrub: tuple[str, ...] = ()
    export: tuple[str, ...] = ("id",)
    # DELETE only: (table, column) of a retained record whose NOT NULL FK
    # points at these rows; referenced rows are kept (scrubbed), not deleted.
    keep_if_referenced_by: tuple[str, str] | None = None


def _unlink(
    table: str, column: str, category: str = "authored_content"
) -> InventoryEntry:
    """Shorthand for a nullable authorship/ownership link that is NULLed."""
    return InventoryEntry(table, column, Action.UNLINK, category)


def _retain(
    table: str, column: str, category: str = "authored_content"
) -> InventoryEntry:
    """Shorthand for a NOT NULL business-record link kept on the tombstone."""
    return InventoryEntry(
        table, column, Action.RETAIN, category, basis="business_record"
    )


# PII columns of the subject's own row, overwritten by Action.ANONYMIZE.
IDENTITY_PII_COLUMNS: tuple[str, ...] = (
    "username",
    "email",
    "full_name",
    "password_hash",
    "mfa_secret",
    "mfa_enabled",
    "auth_provider_id",
    "external_id",
    "metadata",
    "last_login_at",
    "is_active",
)
PORTAL_USER_PII_COLUMNS: tuple[str, ...] = (
    "email",
    "full_name",
    "password_hash",
    "mfa_secret",
    "mfa_backup_codes",
    "last_login_at",
    "is_active",
)

# Order is execution order: children before the rows they reference
# (access_review_items.membership_id -> identity_group_memberships).
IDENTITY_INVENTORY: tuple[InventoryEntry, ...] = (
    # Credentials: revoked by deletion.
    InventoryEntry(
        "api_keys",
        "identity_id",
        Action.DELETE,
        "credentials",
        export=("id", "name", "prefix", "last_used_at", "expires_at", "is_active"),
    ),
    # Preferences / personal settings.
    InventoryEntry(
        "saved_searches",
        "identity_id",
        Action.DELETE,
        "preferences",
        export=("id", "name", "query", "filters"),
    ),
    InventoryEntry(
        "on_call_rotation_participants",
        "identity_id",
        Action.DELETE,
        "notification_preferences",
        export=(
            "id",
            "rotation_id",
            "notification_email",
            "notification_phone",
            "notification_slack",
        ),
    ),
    InventoryEntry(
        "on_call_notifications",
        "identity_id",
        Action.DELETE,
        "notification_history",
        export=("id", "notification_type", "channel", "subject", "sent_at"),
    ),
    # Access grants and memberships.
    # Completed access reviews are audit evidence (SOC 2): the item is
    # kept, so a membership it references must be kept too.
    InventoryEntry(
        "access_review_items",
        "identity_id",
        Action.RETAIN,
        "access",
        basis="access_review_evidence",
        export=("id", "review_id", "decision", "reviewed_at"),
    ),
    InventoryEntry(
        "identity_group_memberships",
        "identity_id",
        Action.DELETE,
        "access",
        scrub=("provider_member_id",),
        export=("id", "group_id", "expires_at", "provider_member_id"),
        keep_if_referenced_by=("access_review_items", "membership_id"),
    ),
    InventoryEntry(
        "user_roles",
        "identity_id",
        Action.DELETE,
        "access",
        export=("id", "role_id", "scope", "scope_id", "organization_id"),
    ),
    InventoryEntry(
        "resource_roles",
        "identity_id",
        Action.DELETE,
        "access",
        export=("id", "resource_type", "resource_id", "role_type", "role"),
    ),
    InventoryEntry(
        "hd_team_members",
        "identity_id",
        Action.DELETE,
        "access",
        export=("hd_team_id", "role"),  # composite PK, no id
    ),
    InventoryEntry("iceflows_stage_approvers", "identity_id", Action.DELETE, "access"),
    InventoryEntry("dg_shares", "shared_with_identity_id", Action.DELETE, "access"),
    InventoryEntry(
        "dg_collection_shares", "shared_with_identity_id", Action.DELETE, "access"
    ),
    InventoryEntry("stream_shares", "shared_with_identity_id", Action.DELETE, "access"),
    # Live-session state (carries display name / socket ids).
    InventoryEntry(
        "stream_editor_locks", "locked_by_identity_id", Action.DELETE, "sessions"
    ),
    InventoryEntry(
        "dg_collaboration_sessions", "identity_id", Action.DELETE, "sessions"
    ),
    # Security audit trail: event retained, pseudonymised.
    InventoryEntry(
        "audit_logs",
        "identity_id",
        Action.UNLINK,
        "activity_log",
        basis="security_audit",
        scrub=("ip_address", "user_agent"),
        export=(
            "id",
            "action_name",
            "resource_type",
            "resource_id",
            "ip_address",
            "user_agent",
            "success",
            "created_at",
        ),
    ),
    InventoryEntry(
        "key_access_log",
        "identity_id",
        Action.UNLINK,
        "activity_log",
        basis="security_audit",
        export=("id", "key_id", "action", "operation", "accessed_at"),
    ),
    InventoryEntry(
        "secret_access_log",
        "identity_id",
        Action.RETAIN,
        "activity_log",
        basis="security_audit",
        export=("id", "secret_id", "action", "accessed_at"),
    ),
    InventoryEntry(
        "dg_share_analytics",
        "accessed_by_identity_id",
        Action.UNLINK,
        "activity_log",
        scrub=("access_ip", "user_agent"),
        export=("id", "share_type", "share_id", "access_ip", "user_agent"),
    ),
    InventoryEntry(
        "stream_form_submissions",
        "submitted_by_identity_id",
        Action.UNLINK,
        "submissions",
        scrub=("ip_address", "user_agent"),
        export=("id", "form_id", "submission_data", "ip_address", "user_agent"),
    ),
    # Free-text aliases denormalised outside the identity table.
    InventoryEntry("references", "created_by", Action.SCRUB_MATCH, "authored_content"),
    InventoryEntry(
        "stream_executions", "triggered_by", Action.SCRUB_MATCH, "authored_content"
    ),
    # Authorship / ownership links on tenant business records.
    InventoryEntry(
        "dg_comments",
        "author_identity_id",
        Action.UNLINK,
        "authored_content",
        export=("id", "diagram_id", "text_content"),
    ),
    InventoryEntry(
        "dg_comment_replies", "author_identity_id", Action.UNLINK, "authored_content"
    ),
    InventoryEntry(
        "issue_comments",
        "author_id",
        Action.UNLINK,
        "authored_content",
        export=("id", "issue_id", "content"),
    ),
    InventoryEntry(
        "hd_ticket_messages",
        "sender_identity_id",
        Action.RETAIN,
        "authored_content",
        basis="business_record",
        export=("id", "hd_ticket_id", "body_text"),
    ),
    _unlink("csv_import_jobs", "created_by_identity_id"),
    _unlink("dg_diagrams", "created_by_identity_id"),
    _unlink("dg_diagrams", "updated_by_identity_id"),
    _unlink("dg_storage_providers", "owner_identity_id"),
    _unlink("dg_templates", "created_by_identity_id"),
    _unlink("dg_collection_items", "added_by_identity_id"),
    _unlink("dg_collection_shares", "created_by_identity_id"),
    _unlink("dg_diagram_versions", "created_by_identity_id"),
    _unlink("dg_node_metadata", "updated_by_identity_id"),
    _unlink("dg_shares", "shared_by_identity_id"),
    _unlink("doc_versions", "author_identity_id"),
    _unlink("hd_canned_responses", "created_by_identity_id"),
    _unlink("hd_tickets", "requester_identity_id"),
    _unlink("hd_tickets", "assignee_identity_id"),
    _unlink("metadata_fields", "created_by_id"),
    _unlink("organizations", "owner_identity_id"),
    _unlink("identity_groups", "owner_identity_id"),
    _unlink("remote_workers", "created_by_identity_id"),
    _unlink("resource_roles", "granted_by_id"),
    _unlink("run_functions", "created_by_identity_id"),
    _unlink("run_executions", "triggered_by_identity_id"),
    _unlink("run_function_versions", "created_by_identity_id"),
    _unlink("stream_custom_modules", "uploaded_by_identity_id"),
    _unlink("stream_playbooks", "created_by_identity_id"),
    _unlink("stream_playbooks", "updated_by_identity_id"),
    _unlink("stream_templates", "created_by_identity_id"),
    _unlink("stream_executions", "triggered_by_identity_id"),
    _unlink("stream_node_metadata", "updated_by_identity_id"),
    _unlink("stream_shares", "shared_by_identity_id"),
    _unlink("stream_versions", "created_by_identity_id"),
    _unlink("stream_execution_approvals", "approver_identity_id"),
    _unlink("sync_conflicts", "resolved_by_id"),
    _unlink("issues", "reporter_id"),
    _unlink("issues", "closed_by_id"),
    _unlink("resource_costs", "created_by_identity_id"),
    _unlink("services", "poc_identity_id"),
    _unlink("ai_resources", "owner_id"),
    _unlink("certificates", "created_by_id"),
    _unlink("certificates", "updated_by_id"),
    _unlink("component_vulnerabilities", "remediated_by_id"),
    _unlink("software", "purchasing_poc_id"),
    _unlink("data_stores", "poc_identity_id"),
    _unlink("iceflows_promotions", "requested_by_identity_id"),
    _unlink("iceflows_promotions", "merged_by_identity_id"),
    _unlink("iceflows_executions", "started_by_identity_id"),
    _unlink("on_call_escalation_policies", "identity_id"),
    _unlink("on_call_overrides", "created_by_id"),
    # NOT NULL links: the row is a tenant record and cannot lose its author
    # without a schema change, so it stays attached to the tombstone.
    _retain("access_review_assignments", "reviewer_identity_id", "access"),
    _retain("group_access_requests", "requester_id", "access"),
    _retain("group_access_approvals", "approver_id", "access"),
    _retain("dg_collections", "owner_identity_id"),
    _retain("dg_diagrams", "owner_identity_id"),
    _retain("dg_shape_libraries", "owner_identity_id"),
    _retain("doc_documents", "author_identity_id"),
    _retain("pg_pages", "author_identity_id"),
    _retain("stream_playbooks", "owner_identity_id"),
    _retain("iceflows", "created_by_identity_id"),
    _retain("iceflows_credentials", "created_by_identity_id"),
    _retain("iceflows_approvals", "approver_identity_id"),
    _retain("on_call_shifts", "identity_id"),
    _retain("on_call_overrides", "original_identity_id"),
    _retain("on_call_overrides", "override_identity_id"),
    InventoryEntry(
        "dsar_requests",
        "identity_id",
        Action.RETAIN,
        "privacy_requests",
        basis="accountability",
        export=("id", "request_type", "status", "created_at"),
    ),
)

PORTAL_USER_INVENTORY: tuple[InventoryEntry, ...] = (
    InventoryEntry(
        "portal_user_org_assignments",
        "portal_user_id",
        Action.DELETE,
        "access",
        export=("id", "organization_id", "role"),
    ),
    InventoryEntry("data_stores", "created_by", Action.UNLINK, "authored_content"),
    InventoryEntry(
        "portal_mfa_challenges", "portal_user_id", Action.DELETE, "sessions"
    ),
    InventoryEntry(
        "dsar_requests",
        "portal_user_id",
        Action.RETAIN,
        "privacy_requests",
        basis="accountability",
        export=("id", "request_type", "status", "created_at"),
    ),
)

# What an erasure cannot remove synchronously -- quoted in every receipt and
# in docs/compliance/dsar-erasure-inventory.md. Keep the two in sync.
NOT_ERASED_SYNCHRONOUSLY: tuple[dict[str, str], ...] = (
    {
        "store": "database_backups",
        "reason": (
            "Backups are immutable snapshots; the subject's data ages out "
            "when the backup retention window expires and must be re-erased "
            "if a backup taken before this receipt is ever restored."
        ),
    },
    {
        "store": "free_text_and_attachments",
        "reason": (
            "Comment bodies, ticket messages, documents, form submissions "
            "and uploaded files are tenant records whose free text is not "
            "parsed; personal data typed into them needs manual review by "
            "the tenant administrator."
        ),
    },
    {
        "store": "issued_access_tokens",
        "reason": (
            "JWTs are stateless and are not individually revoked; they are "
            "rejected on every request because the subject row is "
            "deactivated before anything else is erased."
        ),
    },
    {
        "store": "external_systems",
        "reason": (
            "Identity providers (SSO/SCIM/LDAP), already-delivered webhooks "
            "and exported telemetry are outside this database; the tenant "
            "must erase the subject there separately."
        ),
    },
    {
        "store": "search_and_cache",
        "reason": (
            "Search runs directly on the database (no separate index) and "
            "there is no server-side session store; the only subject-bound "
            "cache entries are 60-second diagram-collaboration tickets, "
            "which expire on their own rather than being purged."
        ),
    },
)


def inventory_for(subject_type: SubjectType) -> tuple[InventoryEntry, ...]:
    """Return the ordered inventory for the given kind of data subject."""
    if subject_type is SubjectType.PORTAL_USER:
        return PORTAL_USER_INVENTORY
    return IDENTITY_INVENTORY
