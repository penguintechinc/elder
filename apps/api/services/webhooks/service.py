"""Webhook & Notification Service for Elder v1.2.0 (Phase 9)."""

# flake8: noqa: E501


import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

import requests
from penguin_dal import DAL
from sqlalchemy.exc import IntegrityError

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
    """Return True only if `exc` is the webhooks village_id unique-constraint violation.

    Mirrors apps/api/modules/helpdesk/routes/intake_forms.py's identical
    helper for hd_intake_forms.
    """
    orig = getattr(exc, "orig", None)
    constraint_name = getattr(getattr(orig, "diag", None), "constraint_name", None)
    if constraint_name is not None:
        return constraint_name == _VILLAGE_ID_UNIQUE_CONSTRAINT
    return _VILLAGE_ID_UNIQUE_CONSTRAINT in str(exc)


def _village_id_seq(village_id: Optional[str]) -> Optional[int]:
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

    Mirrors apps/api/modules/helpdesk/routes/intake_forms.py's identical
    helper for hd_intake_forms — see that docstring for the full O(1)
    recovery rationale. The counter key is shared across every table that
    mints village_ids for this tenant, so this only ever raises it.
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
    db: DAL, tenant_id: int, redis_client: Any, insert_data: Dict[str, Any]
) -> int:
    """Insert `insert_data` into webhooks with a collision-safe village_id.

    Mirrors apps/api/modules/helpdesk/routes/intake_forms.py's
    _insert_form_with_unique_village_id for hd_intake_forms — same
    mint-retry strategy against the same shared per-tenant Redis counter.
    """
    from shared.utils.village_id import generate_village_id

    last_error: Optional[IntegrityError] = None
    for _ in range(_MAX_VILLAGE_ID_MINT_ATTEMPTS):
        if redis_client:
            village_id = generate_village_id(tenant_id, redis_client)
        else:
            village_id = f"test-{uuid4().hex[:8]}"

        try:
            return db.webhooks.insert(village_id=village_id, **insert_data)
        except IntegrityError as exc:
            if not _is_village_id_conflict(exc):
                raise
            last_error = exc
            if redis_client:
                _raise_village_id_counter_to_table_max(db, tenant_id, redis_client)

    assert last_error is not None  # loop always executes >= 1 iteration
    raise last_error


class WebhookService:
    """Service for managing webhooks and notification rules."""

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
        self, tenant_id: int, enabled: Optional[bool] = None
    ) -> List[Dict[str, Any]]:
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

    def get_webhook(self, webhook_id: int, tenant_id: int) -> Dict[str, Any]:
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
        events: List[str],
        redis_client: Optional[Any] = None,
        organization_id: Optional[int] = None,
        secret: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        filter_issue_type: Optional[str] = None,
        filter_assignee_type: Optional[str] = None,
        filter_assignee_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a new tenant-scoped webhook.

        Args:
            tenant_id: Owning tenant (from the caller's validated JWT, never client input)
            name: Webhook name
            url: Target URL for webhook deliveries
            events: List of event types to subscribe to (e.g. ["issue.assigned"])
            redis_client: Redis client for village_id minting (falls back to a
                test-safe random id when None, matching hd_intake_forms)
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

        now = datetime.now(timezone.utc)
        insert_data = {
            "tenant_id": tenant_id,
            "organization_id": organization_id,
            "name": name,
            "url": url,
            "events": events,
            "secret": secret,
            "headers": headers,
            "is_active": True,
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
        name: Optional[str] = None,
        url: Optional[str] = None,
        events: Optional[List[str]] = None,
        secret: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        is_active: Optional[bool] = None,
        filter_issue_type: Optional[str] = None,
        filter_assignee_type: Optional[str] = None,
        filter_assignee_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Update a tenant-scoped webhook configuration.

        Args:
            webhook_id: Webhook id
            tenant_id: Owning tenant (from the caller's validated JWT)
            (remaining args: see create_webhook — same semantics, all optional)

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

        update_data: Dict[str, Any] = {"updated_at": datetime.now(timezone.utc)}

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

        if filter_issue_type is not None:
            resolved = filter_issue_type.upper()
            if resolved not in _VALID_ISSUE_TYPES_UPPER:
                raise Exception(
                    f"Invalid filter_issue_type. Must be one of: {', '.join(sorted(_VALID_ISSUE_TYPES_UPPER))}"
                )
            update_data["filter_issue_type"] = resolved

        if filter_assignee_type is not None:
            if filter_assignee_type not in _VALID_ASSIGNEE_TYPES:
                raise Exception(
                    f"Invalid filter_assignee_type. Must be one of: {', '.join(sorted(_VALID_ASSIGNEE_TYPES))}"
                )
            update_data["filter_assignee_type"] = filter_assignee_type

        if filter_assignee_id is not None:
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

    def delete_webhook(self, webhook_id: int, tenant_id: int) -> Dict[str, str]:
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
        self, webhook_id: int, tenant_id: int, event_type: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
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
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": payload,
        }

        now = datetime.now(timezone.utc)
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
        self, delivery_id: int, webhook: Any, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
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
                delivered_at=datetime.now(timezone.utc) if success else None,
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
    ) -> Dict[str, Any]:
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
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
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

    def test_webhook(self, webhook_id: int, tenant_id: int) -> Dict[str, Any]:
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
        self, organization_id: Optional[int] = None, channel: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        List notification rules with optional filtering.

        Args:
            organization_id: Filter by organization
            channel: Filter by channel type

        Returns:
            List of notification rule dictionaries
        """
        query = self.db.notification_rules.id > 0

        if organization_id is not None:
            query &= self.db.notification_rules.organization_id == organization_id

        if channel is not None:
            query &= self.db.notification_rules.channel == channel

        rules = self.db(query).select(orderby=self.db.notification_rules.created_at)

        return [r.as_dict() for r in rules]

    def get_notification_rule(self, rule_id: int) -> Dict[str, Any]:
        """
        Get notification rule by ID.

        Args:
            rule_id: Notification rule ID

        Returns:
            Notification rule dictionary

        Raises:
            Exception: If rule not found
        """
        rule = self.db.notification_rules[rule_id]

        if not rule:
            raise Exception(f"Notification rule {rule_id} not found")

        return rule.as_dict()

    def create_notification_rule(
        self,
        name: str,
        channel: str,
        events: List[str],
        config: Dict[str, Any],
        organization_id: int,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a notification rule.

        Args:
            name: Rule name
            channel: Notification channel (email, slack, teams, pagerduty)
            events: List of event types
            config: Channel-specific configuration
            organization_id: Organization ID
            description: Optional description

        Returns:
            Created notification rule dictionary
        """
        # Validate channel
        valid_channels = ["email", "slack", "teams", "pagerduty"]
        if channel not in valid_channels:
            raise Exception(
                f"Invalid channel. Must be one of: {', '.join(valid_channels)}"
            )

        # Validate events
        if not events or not isinstance(events, list):
            raise Exception("Events must be a non-empty list")

        # Validate config
        if not config or not isinstance(config, dict):
            raise Exception("Config must be a non-empty dictionary")

        now = datetime.now(timezone.utc)
        rule_id = self.db.notification_rules.insert(
            name=name,
            channel=channel,
            events_json=json.dumps(events),
            config_json=json.dumps(config),
            organization_id=organization_id,
            description=description,
            enabled=True,
            created_at=now,
            updated_at=now,
        )

        self.db.commit()

        rule = self.db.notification_rules[rule_id]
        return rule.as_dict()

    def update_notification_rule(
        self,
        rule_id: int,
        name: Optional[str] = None,
        events: Optional[List[str]] = None,
        config: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Update notification rule.

        Args:
            rule_id: Rule ID
            name: New name
            events: New event list
            config: New configuration
            description: New description
            enabled: New enabled status

        Returns:
            Updated notification rule dictionary

        Raises:
            Exception: If rule not found
        """
        rule = self.db.notification_rules[rule_id]

        if not rule:
            raise Exception(f"Notification rule {rule_id} not found")

        update_data = {"updated_at": datetime.now(timezone.utc)}

        if name is not None:
            update_data["name"] = name

        if events is not None:
            if not isinstance(events, list):
                raise Exception("Events must be a list")
            update_data["events_json"] = json.dumps(events)

        if config is not None:
            if not isinstance(config, dict):
                raise Exception("Config must be a dictionary")
            update_data["config_json"] = json.dumps(config)

        if description is not None:
            update_data["description"] = description

        if enabled is not None:
            update_data["enabled"] = enabled

        self.db(self.db.notification_rules.id == rule_id).update(**update_data)
        self.db.commit()

        rule = self.db.notification_rules[rule_id]
        return rule.as_dict()

    def delete_notification_rule(self, rule_id: int) -> Dict[str, str]:
        """
        Delete a notification rule.

        Args:
            rule_id: Rule ID

        Returns:
            Success message

        Raises:
            Exception: If rule not found
        """
        rule = self.db.notification_rules[rule_id]

        if not rule:
            raise Exception(f"Notification rule {rule_id} not found")

        self.db(self.db.notification_rules.id == rule_id).delete()
        self.db.commit()

        return {"message": "Notification rule deleted successfully"}

    def test_notification_rule(self, rule_id: int) -> Dict[str, Any]:
        """
        Send a test notification for a rule.

        Args:
            rule_id: Rule ID

        Returns:
            Test result

        Raises:
            Exception: If rule not found
        """
        rule = self.db.notification_rules[rule_id]

        if not rule:
            raise Exception(f"Notification rule {rule_id} not found")

        # Create test notification
        test_payload = {
            "test": True,
            "rule_id": rule_id,
            "message": f"This is a test notification from Elder via {rule.channel}",
        }

        return self._send_notification(rule, test_payload)

    def _send_notification(self, rule: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send notification via configured channel.

        Args:
            rule: Notification rule record
            payload: Notification payload

        Returns:
            Send result
        """
        try:
            config = json.loads(rule.config_json)

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

    def _send_email_notification(
        self, config: Dict[str, Any], payload: Dict[str, Any]
    ) -> Dict[str, Any]:
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
        self, config: Dict[str, Any], payload: Dict[str, Any]
    ) -> Dict[str, Any]:
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
        self, config: Dict[str, Any], payload: Dict[str, Any]
    ) -> Dict[str, Any]:
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
        self, config: Dict[str, Any], payload: Dict[str, Any]
    ) -> Dict[str, Any]:
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
        self, event_type: str, payload: Dict[str, Any], organization_id: int
    ) -> Dict[str, Any]:
        """
        Broadcast an event to all applicable webhooks and notification rules.

        Args:
            event_type: Event type (e.g., "entity.created")
            payload: Event payload
            organization_id: Organization ID

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
            & (self.db.notification_rules.enabled is True)
        ).select()

        notification_results = []
        for rule in rules:
            events = json.loads(rule.events_json)
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

    def _sanitize_webhook(self, webhook: Any) -> Dict[str, Any]:
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
