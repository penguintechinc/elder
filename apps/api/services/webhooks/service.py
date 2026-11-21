"""Webhook & Notification Service for Elder v1.2.0 (Phase 9)."""

# flake8: noqa: E501

import hashlib
import hmac
import json
from datetime import UTC, datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

import requests
from penguin_dal import DAL
from sqlalchemy.exc import IntegrityError

#: Sentinel to distinguish "field absent from request" from "field present with null value".
#: Used in update_webhook to allow clearing filters (null) vs skipping them (absent).
_UNSET = object()

#: Matches IssueType member names (apps/api/modules/issues/models/issue.py).
#: filter_issue_type is stored uppercase (matching how issues.issue_type
#: itself is stored — see create_issue's `.upper()`), so it's validated
#: against this same set.
_VALID_ISSUE_TYPES_UPPER = {
    "OPERATIONS",
    "CODE",
    "CONFIG",
    "SECURITY",
    "ARCHITECTURE",
    "PROCESS",
    "APPROVAL",
    "FEATURE",
    "BUG",
    "SUPPORT",
    "OTHER",
}

#: Matches issues.assignee_type's two valid values (see
#: apps/api/modules/issues/routes/issues.py::_resolve_assignee_type).
_VALID_ASSIGNEE_TYPES = {"identity", "org_unit"}

#: Postgres's constraint name for webhooks.village_id's unique constraint
#: (see alembic/versions/037_webhook_assignment_events.py). Used to scope
#: the mint-collision retry to village_id ONLY.
_VILLAGE_ID_UNIQUE_CONSTRAINT = "uq_webhooks_village_id"
_VILLAGE_ID_CONFLICT_CONSTRAINTS = frozenset(
    {
        "uq_webhooks_village_id",
        "ix_webhooks_village_id",
        "village_ids_pkey",
    }
)

_MAX_VILLAGE_ID_MINT_ATTEMPTS = 3


def _parse_json_column(value: Any) -> Any:
    """Normalize a pydal JSON-column value to a native Python object.

    penguin-dal doesn't consistently hand back SQLAlchemy `JSON`-typed
    columns pre-decoded — empirically, a value inserted as a raw Python
    object (as `create_webhook`/`deliver_webhook` do for `events`/
    `headers`/`metadata`/`request_payload`) comes back from a fresh SELECT
    already parsed as a list/dict, not a JSON string. This mirrors the
    identical `isinstance(value, str)` guard in
    apps/api/services/secrets/vault_client.py for the same DAL behavior on
    a different table, so this service works regardless of which shape a
    given call site happens to receive.
    """
    if isinstance(value, str):
        return json.loads(value)
    return value


def generate_signature(secret: str, payload: str) -> str:
    """Generate an HMAC-SHA256 signature for a webhook payload string.

    The single shared signing implementation for every native webhook
    delivery — WebhookService's own delivery path (below) and the
    issue.assigned dispatcher (apps/api/services/webhooks/assignment.py)
    both call this, so no code path can ever sign differently than another.
    """
    return hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _is_village_id_conflict(exc: IntegrityError) -> bool:
    """Return True only if ``exc`` is a village_id unique-constraint violation."""
    orig = getattr(exc, "orig", None)
    constraint_name = getattr(getattr(orig, "diag", None), "constraint_name", None)
    if constraint_name is not None:
        return constraint_name in _VILLAGE_ID_CONFLICT_CONSTRAINTS
    msg = str(exc)
    return any(c in msg for c in _VILLAGE_ID_CONFLICT_CONSTRAINTS)


def _village_id_seq(village_id: str | None) -> int | None:
    """Parse the object-seq (trailing 16 hex chars) out of a village_id.

    Returns None for anything that doesn't match the `TTTTTTTT-OOOO...`
    format (notably the `test-<uuid>` fallback minted with no Redis) rather
    than raising.
    """
    if not village_id or "-" not in village_id:
        return None
    _, seq_hex = village_id.split("-", 1)
    try:
        return int(seq_hex, 16)
    except ValueError:
        return None


def _raise_village_id_counter_to_table_max(
    db: DAL, tenant_id: int, redis_client: Any
) -> None:
    """Raise the tenant's Redis village_id counter to >= the highest
    object-seq already persisted in `webhooks` for this tenant.

    O(1) recovery: the counter key is shared across every table that mints
    village_ids for this tenant, so this only ever raises it.
    """
    counter_key = f"elder:vid:{tenant_id:08x}"

    rows = db(db.webhooks.tenant_id == tenant_id).select(db.webhooks.village_id)
    table_max = 0
    for row in rows:
        seq = _village_id_seq(row.village_id)
        if seq is not None:
            table_max = max(table_max, seq)

    current = int(redis_client.get(counter_key) or 0)
    redis_client.set(counter_key, max(current, table_max))


def _insert_webhook_with_unique_village_id(
    db: DAL, tenant_id: int, redis_client: Any, insert_data: dict[str, Any]
) -> int:
    """Insert `insert_data` into webhooks with a collision-safe village_id.

    Mint-retry strategy against the shared per-tenant Redis village_id counter.
    """
    from apps.api.utils.village_id import mint_village_id

    last_error: IntegrityError | None = None
    for _ in range(_MAX_VILLAGE_ID_MINT_ATTEMPTS):
        village_id = mint_village_id(tenant_id, redis_client, db=db)

        try:
            return db.webhooks.insert(village_id=village_id, **insert_data)
        except IntegrityError as exc:
            if not _is_village_id_conflict(exc):
                raise
            last_error = exc
            if redis_client:
                _raise_village_id_counter_to_table_max(db, tenant_id, redis_client)

    if last_error is not None:
        raise last_error
    raise RuntimeError("Failed to insert webhook with unique village_id")


_VALID_ALERT_DESTINATIONS = frozenset({"EMAIL", "WEBHOOK", "PAGERDUTY", "SLACK"})


def _normalize_alert_destination_type(raw_value: Any) -> str:
    """Normalize and validate an AlertDestinationType member name (uppercase)."""
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise Exception(
            "Invalid destination_type. Must be one of: email, webhook, pagerduty, slack"
        )
    upper = raw_value.strip().upper()
    if upper not in _VALID_ALERT_DESTINATIONS:
        raise Exception(
            "Invalid destination_type. Must be one of: email, webhook, pagerduty, slack"
        )
    return upper


class WebhookService:
    """Service for managing webhooks, notification rules, and alert configurations."""

    def __init__(self, db: DAL):
        """
        Initialize WebhookService.

        Args:
            db: penguin-dal database instance
        """
        self.db = db

    # ===========================
    # Webhook Management Methods
    # ===========================

    def list_webhooks(
        self, tenant_id: int, enabled: bool | None = None
    ) -> list[dict[str, Any]]:
        """
        List all webhooks for a tenant, optionally filtered by active status.

        Args:
            tenant_id: Owning tenant (from the caller's validated JWT)
            enabled: Filter by is_active status

        Returns:
            List of webhook dictionaries
        """
        query = self.db.webhooks.tenant_id == tenant_id

        if enabled is not None:
            query &= self.db.webhooks.is_active == enabled

        webhooks = self.db(query).select(orderby=self.db.webhooks.created_at)

        return [self._sanitize_webhook(w) for w in webhooks]

    def get_webhook(self, webhook_id: int, tenant_id: int) -> dict[str, Any]:
        """
        Get webhook details by id, scoped to tenant_id.

        Args:
            webhook_id: Webhook id
            tenant_id: Owning tenant (from the caller's validated JWT)

        Returns:
            Webhook dictionary

        Raises:
            Exception: If webhook not found in this tenant
        """
        webhook = (
            self.db(
                (self.db.webhooks.id == webhook_id)
                & (self.db.webhooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not webhook:
            raise Exception(f"Webhook {webhook_id} not found")

        return self._sanitize_webhook(webhook)

    def create_webhook(
        self,
        tenant_id: int,
        name: str,
        url: str,
        events: list[str],
        redis_client: Any | None = None,
        organization_id: int | None = None,
        secret: str | None = None,
        headers: dict[str, str] | None = None,
        filter_issue_type: str | None = None,
        filter_assignee_type: str | None = None,
        filter_assignee_id: int | None = None,
        metadata: dict[str, Any] | None = None,
        is_active: bool = True,
    ) -> dict[str, Any]:
        """
        Create a new tenant-scoped webhook.

        Args:
            tenant_id: Owning tenant (from the caller's validated JWT, never client input)
            name: Webhook name
            url: Target URL for webhook deliveries
            events: List of event types to subscribe to (e.g. ["issue.assigned"])
            redis_client: Redis client for village_id minting (falls back to a
                test-safe random id when None)
            organization_id: Optional owning organization
            secret: Optional shared secret for HMAC signatures
            headers: Optional custom headers sent with every delivery
            filter_issue_type: Optional issue_type filter for issue.assigned
                (e.g. "support"); stored/compared uppercase
            filter_assignee_type: Optional assignee_type filter ("identity" or
                "org_unit"); required together with filter_assignee_id
            filter_assignee_id: Optional assignee_id filter; required together
                with filter_assignee_type
            metadata: Optional free-form JSON metadata bag
            is_active: Whether the webhook is active (defaults to True)

        Returns:
            Created webhook dictionary (see _sanitize_webhook for the exact shape)
        """
        if not url.startswith(("http://", "https://")):
            raise Exception("Webhook URL must start with http:// or https://")

        if not events or not isinstance(events, list):
            raise Exception("Events must be a non-empty list")

        if filter_issue_type is not None:
            filter_issue_type = filter_issue_type.upper()
            if filter_issue_type not in _VALID_ISSUE_TYPES_UPPER:
                raise Exception(
                    f"Invalid filter_issue_type. Must be one of: {', '.join(sorted(_VALID_ISSUE_TYPES_UPPER))}"
                )

        if (filter_assignee_type is None) != (filter_assignee_id is None):
            raise Exception(
                "filter_assignee_type and filter_assignee_id must be set together"
            )

        if (
            filter_assignee_type is not None
            and filter_assignee_type not in _VALID_ASSIGNEE_TYPES
        ):
            raise Exception(
                f"Invalid filter_assignee_type. Must be one of: {', '.join(sorted(_VALID_ASSIGNEE_TYPES))}"
            )

        now = datetime.now(UTC)
        insert_data = {
            "tenant_id": tenant_id,
            "organization_id": organization_id,
            "name": name,
            "url": url,
            "events": events,
            "secret": secret,
            "headers": headers,
            "is_active": is_active,
            "filter_issue_type": filter_issue_type,
            "filter_assignee_type": filter_assignee_type,
            "filter_assignee_id": filter_assignee_id,
            "metadata": metadata,
            "created_at": now,
            "updated_at": now,
        }

        webhook_id = _insert_webhook_with_unique_village_id(
            self.db, tenant_id, redis_client, insert_data
        )
        self.db.commit()

        webhook = self.db.webhooks[webhook_id]
        return self._sanitize_webhook(webhook)

    def update_webhook(
        self,
        webhook_id: int,
        tenant_id: int,
        name: str | None = None,
        url: str | None = None,
        events: list[str] | None = None,
        secret: str | None = None,
        headers: dict[str, str] | None = None,
        is_active: bool | None = None,
        filter_issue_type: Any = _UNSET,
        filter_assignee_type: Any = _UNSET,
        filter_assignee_id: Any = _UNSET,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Update a tenant-scoped webhook configuration.

        Args:
            webhook_id: Webhook id
            tenant_id: Owning tenant (from the caller's validated JWT)
            name, url, events, secret, headers, is_active, metadata: Optional fields to update
                (see create_webhook for semantics); _UNSET (default) means field is absent from the
                request and will not be updated; None means explicit null (clear the field)
            filter_issue_type, filter_assignee_type, filter_assignee_id: Filter fields using
                sentinel pattern — _UNSET (default) = absent (skip), None = explicit null (clear),
                real value = set. This allows both clearing and skipping filters in a single call.
                filter_assignee_type and filter_assignee_id must be cleared together (the route
                validates the effective pair); the service will only update whichever fields are
                not _UNSET.

        Returns:
            Updated webhook dictionary

        Raises:
            Exception: If webhook not found in this tenant
        """
        webhook = (
            self.db(
                (self.db.webhooks.id == webhook_id)
                & (self.db.webhooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not webhook:
            raise Exception(f"Webhook {webhook_id} not found")

        update_data: dict[str, Any] = {"updated_at": datetime.now(UTC)}

        if name is not None:
            update_data["name"] = name

        if url is not None:
            if not url.startswith(("http://", "https://")):
                raise Exception("Webhook URL must start with http:// or https://")
            update_data["url"] = url

        if events is not None:
            if not isinstance(events, list):
                raise Exception("Events must be a list")
            update_data["events"] = events

        if secret is not None:
            update_data["secret"] = secret

        if headers is not None:
            update_data["headers"] = headers

        if is_active is not None:
            update_data["is_active"] = is_active

        if filter_issue_type is not _UNSET:
            # Explicit None clears the filter; real value validates and sets
            if filter_issue_type is not None:
                resolved = filter_issue_type.upper()
                if resolved not in _VALID_ISSUE_TYPES_UPPER:
                    raise Exception(
                        f"Invalid filter_issue_type. Must be one of: {', '.join(sorted(_VALID_ISSUE_TYPES_UPPER))}"
                    )
                update_data["filter_issue_type"] = resolved
            else:
                update_data["filter_issue_type"] = None

        if filter_assignee_type is not _UNSET:
            # Explicit None clears the filter; real value validates and sets
            if filter_assignee_type is not None:
                if filter_assignee_type not in _VALID_ASSIGNEE_TYPES:
                    raise Exception(
                        f"Invalid filter_assignee_type. Must be one of: {', '.join(sorted(_VALID_ASSIGNEE_TYPES))}"
                    )
                update_data["filter_assignee_type"] = filter_assignee_type
            else:
                update_data["filter_assignee_type"] = None

        if filter_assignee_id is not _UNSET:
            # Explicit None clears the filter; real value sets it
            update_data["filter_assignee_id"] = filter_assignee_id

        if metadata is not None:
            update_data["metadata"] = metadata

        self.db(
            (self.db.webhooks.id == webhook_id)
            & (self.db.webhooks.tenant_id == tenant_id)
        ).update(**update_data)
        self.db.commit()

        webhook = self.db.webhooks[webhook_id]
        return self._sanitize_webhook(webhook)

    def delete_webhook(self, webhook_id: int, tenant_id: int) -> dict[str, str]:
        """
        Delete a tenant-scoped webhook and its delivery history.

        Args:
            webhook_id: Webhook id
            tenant_id: Owning tenant (from the caller's validated JWT)

        Returns:
            Success message

        Raises:
            Exception: If webhook not found in this tenant
        """
        webhook = (
            self.db(
                (self.db.webhooks.id == webhook_id)
                & (self.db.webhooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not webhook:
            raise Exception(f"Webhook {webhook_id} not found")

        self.db(self.db.webhook_deliveries.webhook_id == webhook_id).delete()
        self.db(
            (self.db.webhooks.id == webhook_id)
            & (self.db.webhooks.tenant_id == tenant_id)
        ).delete()
        self.db.commit()

        return {"message": "Webhook deleted successfully"}

    # ===========================
    # Webhook Delivery Methods
    # ===========================

    def deliver_webhook(
        self, webhook_id: int, tenant_id: int, event_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Deliver a webhook event.

        Args:
            webhook_id: Webhook id
            tenant_id: Owning tenant (from the caller's validated JWT)
            event_type: Event type (e.g., "issue.assigned")
            payload: Event payload

        Returns:
            Delivery result dictionary
        """
        webhook = (
            self.db(
                (self.db.webhooks.id == webhook_id)
                & (self.db.webhooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not webhook:
            raise Exception(f"Webhook {webhook_id} not found")

        if not webhook.is_active:
            raise Exception(f"Webhook {webhook_id} is disabled")

        # JSON columns may come back from a pydal SELECT already parsed or
        # as a raw string — see _parse_json_column.
        events = _parse_json_column(webhook.events) or []
        if event_type not in events:
            raise Exception(
                f"Webhook {webhook_id} does not subscribe to event {event_type}"
            )

        delivery_payload = {
            "event": event_type,
            "timestamp": datetime.now(UTC).isoformat(),
            "data": payload,
        }

        now = datetime.now(UTC)
        delivery_id = self.db.webhook_deliveries.insert(
            webhook_id=webhook_id,
            event_type=event_type,
            request_payload=delivery_payload,
            attempt_count=0,
            created_at=now,
        )
        self.db.commit()

        return self._attempt_delivery(delivery_id, webhook, delivery_payload)

    def _attempt_delivery(
        self, delivery_id: int, webhook: Any, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Attempt to deliver a webhook, HMAC-signed if a secret is configured.

        Args:
            delivery_id: Delivery id (row already inserted by the caller)
            webhook: Webhook row
            payload: Delivery payload

        Returns:
            Delivery result
        """
        # penguin-dal's FieldProxy doesn't support pydal's
        # `table.field + 1` update-expression syntax (see
        # apps/api/modules/flows/routes/hooks.py's identical
        # `(webhook.trigger_count or 0) + 1` pattern) — read the current
        # count and increment it in Python instead.
        current_delivery = self.db.webhook_deliveries[delivery_id]
        next_attempt_count = (
            current_delivery.attempt_count if current_delivery else 0
        ) or 0
        next_attempt_count += 1

        try:
            # Serialize once — this exact string is both signed and posted
            # as the raw body. `requests`' own `json=` re-serialization
            # doesn't preserve key order/formatting, so signing this string
            # while posting `json=payload` would sign different bytes than
            # a receiver actually gets and the HMAC would never verify.
            payload_str = json.dumps(payload, sort_keys=True)

            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Elder-Webhook/1.2.0",
            }

            if webhook.headers:
                headers.update(_parse_json_column(webhook.headers))

            if webhook.secret:
                signature = generate_signature(webhook.secret, payload_str)
                headers["X-Elder-Signature"] = signature

            response = requests.post(
                webhook.url,
                data=payload_str.encode("utf-8"),
                headers=headers,
                timeout=30,
            )

            success = 200 <= response.status_code < 300

            self.db(self.db.webhook_deliveries.id == delivery_id).update(
                attempt_count=next_attempt_count,
                status="success" if success else "failed",
                http_status=response.status_code,
                response_body=response.text[:1000],
                delivered_at=datetime.now(UTC) if success else None,
            )
            self.db.commit()

            return {
                "delivery_id": delivery_id,
                "success": success,
                "status_code": response.status_code,
            }

        except Exception as e:
            self.db(self.db.webhook_deliveries.id == delivery_id).update(
                attempt_count=next_attempt_count,
                status="failed",
                error_message=str(e)[:500],
            )
            self.db.commit()

            return {"delivery_id": delivery_id, "success": False, "error": str(e)}

    def redeliver_webhook(
        self, webhook_id: int, tenant_id: int, delivery_id: int
    ) -> dict[str, Any]:
        """
        Retry a failed webhook delivery.

        Args:
            webhook_id: Webhook id
            tenant_id: Owning tenant (from the caller's validated JWT)
            delivery_id: Delivery id

        Returns:
            Redelivery result

        Raises:
            Exception: If webhook or delivery not found
        """
        webhook = (
            self.db(
                (self.db.webhooks.id == webhook_id)
                & (self.db.webhooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not webhook:
            raise Exception(f"Webhook {webhook_id} not found")

        delivery = self.db.webhook_deliveries[delivery_id]
        if not delivery:
            raise Exception(f"Delivery {delivery_id} not found")

        if delivery.webhook_id != webhook_id:
            raise Exception(
                f"Delivery {delivery_id} does not belong to webhook {webhook_id}"
            )

        payload = _parse_json_column(delivery.request_payload) or {}

        return self._attempt_delivery(delivery_id, webhook, payload)

    def get_webhook_deliveries(
        self,
        webhook_id: int,
        tenant_id: int,
        limit: int = 50,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get webhook delivery history.

        Args:
            webhook_id: Webhook id
            tenant_id: Owning tenant (from the caller's validated JWT)
            limit: Maximum number of deliveries to return
            status: Filter by delivery status ("success" or "failed")

        Returns:
            List of delivery dictionaries

        Raises:
            Exception: If webhook not found in this tenant
        """
        webhook = (
            self.db(
                (self.db.webhooks.id == webhook_id)
                & (self.db.webhooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not webhook:
            raise Exception(f"Webhook {webhook_id} not found")

        query = self.db.webhook_deliveries.webhook_id == webhook_id

        if status is not None:
            query &= self.db.webhook_deliveries.status == status

        deliveries = self.db(query).select(
            orderby=~self.db.webhook_deliveries.created_at, limitby=(0, limit)
        )

        return [
            {
                "id": d.id,
                "webhook_id": d.webhook_id,
                "event_type": d.event_type,
                "status": d.status,
                "http_status": d.http_status,
                "request_payload": (
                    _parse_json_column(d.request_payload) if d.request_payload else None
                ),
                "response_body": d.response_body,
                "error_message": d.error_message,
                "attempt_count": d.attempt_count,
                "delivered_at": d.delivered_at.isoformat() if d.delivered_at else None,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in deliveries
        ]

    def test_webhook(self, webhook_id: int, tenant_id: int) -> dict[str, Any]:
        """
        Send a test event to webhook (uses its first subscribed event type).

        Args:
            webhook_id: Webhook id
            tenant_id: Owning tenant (from the caller's validated JWT)

        Returns:
            Test delivery result

        Raises:
            Exception: If webhook not found in this tenant
        """
        webhook = (
            self.db(
                (self.db.webhooks.id == webhook_id)
                & (self.db.webhooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not webhook:
            raise Exception(f"Webhook {webhook_id} not found")

        test_payload = {
            "test": True,
            "webhook_id": webhook_id,
            "message": "This is a test webhook delivery from Elder",
        }

        events = _parse_json_column(webhook.events) or []
        event_type = events[0] if events else "test.event"

        return self.deliver_webhook(webhook_id, tenant_id, event_type, test_payload)

    # ===========================
    # Notification Rule Methods
    # ===========================

    def list_notification_rules(
        self,
        tenant_id: int,
        organization_id: int | None = None,
        channel: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        List notification rules scoped to tenant_id.

        Tenant isolation: only rules whose organization_id belongs to
        tenant_id are returned. If organization_id filter is set, it is
        validated against tenant_id; an org not belonging to the tenant
        returns an empty list.

        Args:
            tenant_id: Tenant to scope results to (required)
            organization_id: Filter by organization (must belong to tenant_id)
            channel: Filter by channel type

        Returns:
            List of notification rule dictionaries scoped to tenant_id
        """
        # Resolve tenant's org_ids to scalar list (penguin-dal cannot adapt
        # column==column comparisons; must use scalar membership via .belongs)
        org_ids = [
            r.id
            for r in self.db(self.db.organizations.tenant_id == tenant_id).select(
                self.db.organizations.id
            )
        ]
        if not org_ids:
            return []

        # Base query: notification_rules.organization_id in the tenant's orgs
        query = self.db.notification_rules.organization_id.belongs(org_ids)

        if organization_id is not None:
            # Already tenant-bounded by belongs; just add the org filter
            query &= self.db.notification_rules.organization_id == organization_id

        if channel is not None:
            query &= self.db.notification_rules.channel == channel

        rules = self.db(query).select(orderby=self.db.notification_rules.created_at)

        return [self._sanitize_notification_rule(r) for r in rules]

    def _get_scoped_notification_rule_row(
        self, rule_id: int, tenant_id: int | None
    ) -> Any:
        """Fetch a notification_rules row, enforcing tenant scoping when ``tenant_id`` is given."""
        rule = self.db(self.db.notification_rules.id == rule_id).select().first()
        if not rule:
            raise Exception(f"Notification rule {rule_id} not found")

        if tenant_id is not None:
            org = (
                self.db(
                    (self.db.organizations.id == rule.organization_id)
                    & (self.db.organizations.tenant_id == tenant_id)
                )
                .select(self.db.organizations.id)
                .first()
            )
            if not org:
                raise Exception(f"Notification rule {rule_id} not found")
        return rule

    def _sanitize_notification_rule(self, rule: Any) -> dict[str, Any]:
        """Serialize a notification_rules row with explicit fields including village_id."""
        raw_events = getattr(rule, "events", None)
        if raw_events is None:
            raw_events = getattr(rule, "events_json", None)
        if raw_events is None:
            raw_events = getattr(rule, "event_types", None)
        raw_config = getattr(rule, "config_json", None)
        if raw_config is None:
            raw_config = getattr(rule, "channels", None)
        return {
            "id": rule.id,
            "village_id": getattr(rule, "village_id", None),
            "organization_id": rule.organization_id,
            "name": rule.name,
            "channel": getattr(rule, "channel", None),
            "events": _parse_json_column(raw_events) or [],
            "config_json": _parse_json_column(raw_config) or {},
            "enabled": bool(getattr(rule, "enabled", getattr(rule, "is_active", True))),
            "created_at": (
                rule.created_at.isoformat()
                if getattr(rule, "created_at", None)
                else None
            ),
            "updated_at": (
                rule.updated_at.isoformat()
                if getattr(rule, "updated_at", None)
                else None
            ),
        }

    def get_notification_rule(self, rule_id: int, tenant_id: int) -> dict[str, Any]:
        """
        Get notification rule by ID, scoped to tenant_id.

        Tenant isolation: returns the rule only if its organization_id
        resolves (via organizations.tenant_id) to tenant_id. Cross-tenant
        access returns "not found" (no existence leak).
        """
        rule = self._get_scoped_notification_rule_row(rule_id, tenant_id)
        return self._sanitize_notification_rule(rule)

    def create_notification_rule(
        self,
        name: str,
        channel: str,
        events: list[str],
        config: dict[str, Any],
        organization_id: int,
        description: str | None = None,
        tenant_id: int | None = None,
        redis_client: Any | None = None,
    ) -> dict[str, Any]:
        """
        Create a notification rule with a minted Village ID.
        """
        valid_channels = ["email", "slack", "teams", "pagerduty"]
        if channel not in valid_channels:
            raise Exception(
                f"Invalid channel. Must be one of: {', '.join(valid_channels)}"
            )

        if not events or not isinstance(events, list):
            raise Exception("Events must be a non-empty list")

        if not config or not isinstance(config, dict):
            raise Exception("Config must be a non-empty dictionary")

        if tenant_id is not None:
            org = (
                self.db(
                    (self.db.organizations.id == organization_id)
                    & (self.db.organizations.tenant_id == tenant_id)
                )
                .select(self.db.organizations.id, self.db.organizations.tenant_id)
                .first()
            )
            if not org:
                raise Exception("organization_id not found in tenant")

        from apps.api.utils.village_id import (
            mint_village_id,
            resolve_tenant_id_for_org,
        )

        now = datetime.now(UTC)
        effective_tenant_id = (
            tenant_id
            if tenant_id is not None
            else resolve_tenant_id_for_org(self.db, organization_id)
        )
        village_id = mint_village_id(
            effective_tenant_id, redis_client=redis_client, db=self.db
        )

        tbl = self.db.notification_rules
        insert_kwargs: dict[str, Any] = {
            "name": name,
            "organization_id": organization_id,
            "village_id": village_id,
            "created_at": now,
            "updated_at": now,
        }
        if hasattr(tbl, "channel"):
            insert_kwargs["channel"] = channel
        if hasattr(tbl, "events"):
            insert_kwargs["events"] = events
        elif hasattr(tbl, "events_json"):
            insert_kwargs["events_json"] = json.dumps(events)
        elif hasattr(tbl, "event_types"):
            insert_kwargs["event_types"] = events

        if hasattr(tbl, "config_json"):
            insert_kwargs["config_json"] = config
        elif hasattr(tbl, "channels"):
            insert_kwargs["channels"] = config

        if hasattr(tbl, "enabled"):
            insert_kwargs["enabled"] = True
        elif hasattr(tbl, "is_active"):
            insert_kwargs["is_active"] = True

        if description is not None and hasattr(tbl, "description"):
            insert_kwargs["description"] = description

        rule_id = tbl.insert(**insert_kwargs)
        self.db.commit()

        rule = self.db(tbl.id == rule_id).select().first()
        return self._sanitize_notification_rule(rule)

    def update_notification_rule(
        self,
        rule_id: int,
        name: str | None = None,
        events: list[str] | None = None,
        config: dict[str, Any] | None = None,
        description: str | None = None,
        enabled: bool | None = None,
        tenant_id: int | None = None,
    ) -> dict[str, Any]:
        """Update notification rule."""
        self._get_scoped_notification_rule_row(rule_id, tenant_id)
        tbl = self.db.notification_rules

        update_data: dict[str, Any] = {"updated_at": datetime.now(UTC)}

        if name is not None:
            update_data["name"] = name

        if events is not None:
            if not isinstance(events, list):
                raise Exception("Events must be a list")
            if hasattr(tbl, "events"):
                update_data["events"] = events
            elif hasattr(tbl, "events_json"):
                update_data["events_json"] = json.dumps(events)

        if config is not None:
            if not isinstance(config, dict):
                raise Exception("Config must be a dictionary")
            if hasattr(tbl, "config_json"):
                update_data["config_json"] = config

        if description is not None and hasattr(tbl, "description"):
            update_data["description"] = description

        if enabled is not None:
            if hasattr(tbl, "enabled"):
                update_data["enabled"] = enabled
            elif hasattr(tbl, "is_active"):
                update_data["is_active"] = enabled

        self.db(tbl.id == rule_id).update(**update_data)
        self.db.commit()

        rule = self.db(tbl.id == rule_id).select().first()
        return self._sanitize_notification_rule(rule)

    def delete_notification_rule(
        self, rule_id: int, tenant_id: int | None = None
    ) -> dict[str, str]:
        """Delete a notification rule."""
        self._get_scoped_notification_rule_row(rule_id, tenant_id)
        self.db(self.db.notification_rules.id == rule_id).delete()
        self.db.commit()

        return {"message": "Notification rule deleted successfully"}

    def test_notification_rule(
        self, rule_id: int, tenant_id: int | None = None
    ) -> dict[str, Any]:
        """Send a test notification for a rule."""
        rule = self._get_scoped_notification_rule_row(rule_id, tenant_id)

        test_payload = {
            "test": True,
            "rule_id": rule_id,
            "message": f"This is a test notification from Elder via {rule.channel}",
        }

        return self._send_notification(rule, test_payload)

    def _send_notification(self, rule: Any, payload: dict[str, Any]) -> dict[str, Any]:
        """Send notification via configured channel."""
        try:
            config = _parse_json_column(rule.config_json) or {}

            if rule.channel == "email":
                return self._send_email_notification(config, payload)
            elif rule.channel == "slack":
                return self._send_slack_notification(config, payload)
            elif rule.channel == "teams":
                return self._send_teams_notification(config, payload)
            elif rule.channel == "pagerduty":
                return self._send_pagerduty_notification(config, payload)
            else:
                raise Exception(f"Unsupported channel: {rule.channel}")

        except Exception as e:
            return {"success": False, "error": str(e)}

    # ===========================
    # Alert Configuration Methods
    # ===========================

    def _sanitize_alert_configuration(self, row: Any) -> dict[str, Any]:
        """Build explicit response shape for an alert_configurations row."""
        dest_type = getattr(row, "destination_type", "")
        if hasattr(dest_type, "value"):
            dest_str = str(dest_type.value).lower()
        else:
            dest_str = str(dest_type).lower()
        return {
            "id": row.id,
            "village_id": getattr(row, "village_id", None),
            "organization_id": row.organization_id,
            "destination_type": dest_str,
            "name": row.name,
            "enabled": int(getattr(row, "enabled", 1) or 0),
            "config": _parse_json_column(getattr(row, "config", None)) or {},
            "severity_filter": _parse_json_column(
                getattr(row, "severity_filter", None)
            ),
            "created_at": (
                row.created_at.isoformat() if getattr(row, "created_at", None) else None
            ),
            "updated_at": (
                row.updated_at.isoformat() if getattr(row, "updated_at", None) else None
            ),
        }

    def list_alert_configurations(
        self,
        tenant_id: int,
        organization_id: int | None = None,
        destination_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """List alert configurations scoped to ``tenant_id``."""
        org_ids = [
            r.id
            for r in self.db(self.db.organizations.tenant_id == tenant_id).select(
                self.db.organizations.id
            )
        ]
        if not org_ids:
            return []

        query = self.db.alert_configurations.organization_id.belongs(org_ids)
        if organization_id is not None:
            query &= self.db.alert_configurations.organization_id == organization_id
        if destination_type is not None:
            norm_dest = _normalize_alert_destination_type(destination_type)
            query &= self.db.alert_configurations.destination_type == norm_dest

        rows = self.db(query).select(orderby=self.db.alert_configurations.created_at)
        return [self._sanitize_alert_configuration(r) for r in rows]

    def get_alert_configuration(self, config_id: int, tenant_id: int) -> dict[str, Any]:
        """Get a single alert configuration scoped to ``tenant_id``."""
        row = self.db(self.db.alert_configurations.id == config_id).select().first()
        if not row:
            raise Exception(f"Alert configuration {config_id} not found")

        org = (
            self.db(
                (self.db.organizations.id == row.organization_id)
                & (self.db.organizations.tenant_id == tenant_id)
            )
            .select(self.db.organizations.id)
            .first()
        )
        if not org:
            raise Exception(f"Alert configuration {config_id} not found")

        return self._sanitize_alert_configuration(row)

    def create_alert_configuration(
        self,
        tenant_id: int,
        organization_id: int,
        destination_type: str,
        name: str,
        config: dict[str, Any],
        enabled: int | bool = 1,
        severity_filter: list[str] | None = None,
        redis_client: Any | None = None,
    ) -> dict[str, Any]:
        """Create a tenant-scoped alert configuration and mint its Village ID."""
        norm_dest = _normalize_alert_destination_type(destination_type)
        if not isinstance(name, str) or not name.strip():
            raise Exception("Alert configuration name is required")
        if not isinstance(config, dict) or not config:
            raise Exception("Config must be a non-empty dictionary")
        if severity_filter is not None and not isinstance(severity_filter, list):
            raise Exception("severity_filter must be a list")

        org = (
            self.db(
                (self.db.organizations.id == organization_id)
                & (self.db.organizations.tenant_id == tenant_id)
            )
            .select(self.db.organizations.id)
            .first()
        )
        if not org:
            raise Exception("organization_id not found in tenant")

        from apps.api.utils.village_id import mint_village_id

        now = datetime.now(UTC)
        village_id = mint_village_id(tenant_id, redis_client=redis_client, db=self.db)
        config_id = self.db.alert_configurations.insert(
            organization_id=organization_id,
            destination_type=norm_dest,
            name=name.strip(),
            enabled=1 if enabled else 0,
            config=config,
            severity_filter=severity_filter,
            village_id=village_id,
            created_at=now,
            updated_at=now,
        )
        self.db.commit()

        row = self.db(self.db.alert_configurations.id == config_id).select().first()
        return self._sanitize_alert_configuration(row)

    def update_alert_configuration(
        self,
        config_id: int,
        tenant_id: int,
        name: str | None = None,
        enabled: int | bool | None = None,
        config: dict[str, Any] | None = None,
        severity_filter: Any = _UNSET,
    ) -> dict[str, Any]:
        """Update an alert configuration scoped to ``tenant_id``."""
        self.get_alert_configuration(config_id, tenant_id)

        update_data: dict[str, Any] = {"updated_at": datetime.now(UTC)}
        if name is not None:
            if not isinstance(name, str) or not name.strip():
                raise Exception("Alert configuration name cannot be empty")
            update_data["name"] = name.strip()
        if enabled is not None:
            update_data["enabled"] = 1 if enabled else 0
        if config is not None:
            if not isinstance(config, dict) or not config:
                raise Exception("Config must be a non-empty dictionary")
            update_data["config"] = config
        if severity_filter is not _UNSET:
            if severity_filter is not None and not isinstance(severity_filter, list):
                raise Exception("severity_filter must be a list")
            update_data["severity_filter"] = severity_filter

        self.db(self.db.alert_configurations.id == config_id).update(**update_data)
        self.db.commit()

        row = self.db(self.db.alert_configurations.id == config_id).select().first()
        return self._sanitize_alert_configuration(row)

    def delete_alert_configuration(
        self, config_id: int, tenant_id: int
    ) -> dict[str, str]:
        """Delete an alert configuration scoped to ``tenant_id``."""
        self.get_alert_configuration(config_id, tenant_id)
        self.db(self.db.alert_configurations.id == config_id).delete()
        self.db.commit()
        return {"message": "Alert configuration deleted successfully"}

    def _send_email_notification(
        self, config: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Send email notification."""
        # NOTE: In production, integrate with SMTP or email service (SendGrid, SES, etc.)
        # For now, return success indicating email would be sent
        return {
            "success": True,
            "channel": "email",
            "recipients": config.get("recipients", []),
            "message": "Email notification would be sent in production",
        }

    def _send_slack_notification(
        self, config: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Send Slack notification."""
        webhook_url = config.get("webhook_url")
        if not webhook_url:
            raise Exception("Slack webhook_url is required in config")

        try:
            response = requests.post(
                webhook_url, json={"text": json.dumps(payload, indent=2)}, timeout=10
            )
            return {
                "success": response.status_code == 200,
                "channel": "slack",
                "status_code": response.status_code,
            }
        except Exception as e:
            return {"success": False, "channel": "slack", "error": str(e)}

    def _send_teams_notification(
        self, config: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Send Microsoft Teams notification."""
        webhook_url = config.get("webhook_url")
        if not webhook_url:
            raise Exception("Teams webhook_url is required in config")

        try:
            response = requests.post(
                webhook_url, json={"text": json.dumps(payload, indent=2)}, timeout=10
            )
            return {
                "success": response.status_code == 200,
                "channel": "teams",
                "status_code": response.status_code,
            }
        except Exception as e:
            return {"success": False, "channel": "teams", "error": str(e)}

    def _send_pagerduty_notification(
        self, config: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Send PagerDuty notification."""
        routing_key = config.get("routing_key")
        if not routing_key:
            raise Exception("PagerDuty routing_key is required in config")

        try:
            response = requests.post(
                "https://events.pagerduty.com/v2/enqueue",
                json={
                    "routing_key": routing_key,
                    "event_action": "trigger",
                    "payload": {
                        "summary": "Elder Notification",
                        "source": "elder",
                        "severity": "info",
                        "custom_details": payload,
                    },
                },
                timeout=10,
            )
            return {
                "success": response.status_code == 202,
                "channel": "pagerduty",
                "status_code": response.status_code,
            }
        except Exception as e:
            return {"success": False, "channel": "pagerduty", "error": str(e)}

    # ===========================
    # Event Broadcasting Methods
    # ===========================

    def broadcast_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        organization_id: int,
        tenant_id: int,
    ) -> dict[str, Any]:
        """
        Broadcast an event to all applicable webhooks and notification rules.

        Args:
            event_type: Event type (e.g., "entity.created")
            payload: Event payload
            organization_id: Organization ID
            tenant_id: Owning tenant (from the caller's validated JWT, never
                client input) — scopes webhook selection so a caller can
                never trigger delivery to another tenant's webhooks by
                supplying a foreign organization_id (cross-tenant IDOR fix)

        Returns:
            Broadcast result with counts
        """
        # Webhook half: reconciled to the real `is_active` column (was
        # `enabled`, compared with the always-false `is True` identity
        # check — see this plan's intro). The notification_rules half below
        # still references stale columns (events_json/enabled vs the real
        # event_types/channels/is_active) — that table's drift is a
        # separate, out-of-scope follow-up (see Task 1's model docstring);
        # left unchanged here.
        webhooks = self.db(
            (self.db.webhooks.organization_id == organization_id)
            & (self.db.webhooks.tenant_id == tenant_id)
            & (self.db.webhooks.is_active == True)  # noqa: E712
        ).select()

        webhook_results = []
        for webhook in webhooks:
            events = _parse_json_column(webhook.events) or []
            if event_type in events:
                try:
                    result = self.deliver_webhook(
                        webhook.id, webhook.tenant_id, event_type, payload
                    )
                    webhook_results.append(result)
                except Exception as e:
                    webhook_results.append(
                        {"webhook_id": webhook.id, "success": False, "error": str(e)}
                    )

        # Find notification rules for this event
        rules = self.db(
            (self.db.notification_rules.organization_id == organization_id)
            & (self.db.notification_rules.enabled == True)  # noqa: E712
        ).select()

        notification_results = []
        for rule in rules:
            raw_events = getattr(rule, "events", None)
            if raw_events is None:
                raw_events = getattr(rule, "events_json", None)
            events = _parse_json_column(raw_events) or []
            if event_type in events:
                try:
                    result = self._send_notification(rule, payload)
                    notification_results.append(result)
                except Exception as e:
                    notification_results.append(
                        {"rule_id": rule.id, "success": False, "error": str(e)}
                    )

        return {
            "event_type": event_type,
            "webhooks_triggered": len(webhook_results),
            "webhooks_successful": sum(1 for r in webhook_results if r.get("success")),
            "notifications_triggered": len(notification_results),
            "notifications_successful": sum(
                1 for r in notification_results if r.get("success")
            ),
            "webhook_results": webhook_results,
            "notification_results": notification_results,
        }

    # ===========================
    # Helper Methods
    # ===========================

    def _generate_signature(self, secret: str, payload: str) -> str:
        """Instance-method wrapper around the module-level generate_signature
        (back-compat for any existing caller of the instance method)."""
        return generate_signature(secret, payload)

    def _sanitize_webhook(self, webhook: Any) -> dict[str, Any]:
        """
        Build the exact public webhook response shape from a pydal Row.

        Explicit field allowlist — never a raw `as_dict()`/`__dict__`
        passthrough (see security.md Output Validation) — so a future
        column addition to `webhooks` never silently leaks into every
        webhook API response. JSON columns (events/headers/metadata) may
        come back from a pydal SELECT already parsed or as a raw string —
        see `_parse_json_column`. The secret is never returned in full,
        only a masked placeholder indicating whether one is configured.

        Args:
            webhook: pydal Row for a webhooks record

        Returns:
            Sanitized webhook dictionary
        """
        return {
            "id": webhook.id,
            "village_id": webhook.village_id,
            "tenant_id": webhook.tenant_id,
            "organization_id": webhook.organization_id,
            "name": webhook.name,
            "url": webhook.url,
            "secret": "***masked***" if webhook.secret else None,
            "is_active": webhook.is_active,
            "events": _parse_json_column(webhook.events) or [],
            "headers": _parse_json_column(webhook.headers) or {},
            "filter_issue_type": webhook.filter_issue_type,
            "filter_assignee_type": webhook.filter_assignee_type,
            "filter_assignee_id": webhook.filter_assignee_id,
            "metadata": (
                _parse_json_column(webhook.metadata) if webhook.metadata else None
            ),
            "last_triggered_at": (
                webhook.last_triggered_at.isoformat()
                if webhook.last_triggered_at
                else None
            ),
            "created_at": (
                webhook.created_at.isoformat() if webhook.created_at else None
            ),
            "updated_at": (
                webhook.updated_at.isoformat() if webhook.updated_at else None
            ),
        }
