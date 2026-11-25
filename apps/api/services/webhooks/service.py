"""Webhook & Notification Service for Elder v1.2.0 (Phase 9)."""

# flake8: noqa: E501

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import NAMESPACE_OID, uuid4, uuid5

import structlog
from penguin_dal import DAL
from sqlalchemy.exc import IntegrityError

from shared.utils.env import env_float, env_int
from shared.webhooks import delivery as _delivery

logger = structlog.get_logger(__name__)

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


#: Outbox states of a webhook_deliveries row. ``pending`` is written before
#: any network I/O and ``retrying`` after each failed attempt, so a delivery
#: interrupted by a restart is visible and can be resumed; ``success``,
#: ``failed`` and ``expired`` are terminal.
DELIVERY_PENDING = "pending"
DELIVERY_RETRYING = "retrying"
DELIVERY_SUCCESS = "success"
DELIVERY_FAILED = "failed"
DELIVERY_EXPIRED = "expired"
_RESUMABLE_STATUSES = (DELIVERY_PENDING, DELIVERY_RETRYING)

# tenant id -> opaque reference. Tenants are few and the reference is
# immutable, but the map is still bounded so it can never grow unchecked.
_TENANT_REF_CACHE_MAX = 4096
_tenant_refs: dict[int, str] = {}


def _fallback_tenant_ref(tenant_id: Any) -> str:
    """Return a stable opaque reference for a tenant with no stored UUID.

    Derived (UUIDv5) rather than the raw numeric key so that logs never
    carry an enumerable internal identifier.
    """
    return uuid5(NAMESPACE_OID, f"elder-tenant-{tenant_id}").hex


def tenant_ref_nowait(tenant_id: Any) -> str:
    """Return the tenant's cached reference without touching the database.

    For synchronous scheduling points on the event loop: a cache miss
    yields the derived fallback (still unique per tenant, so capacity
    accounting stays correct) instead of a blocking query.
    """
    cached = _tenant_refs.get(tenant_id)
    return cached if cached is not None else _fallback_tenant_ref(tenant_id)


def tenant_ref(db: Any, tenant_id: Any) -> str:
    """Return the tenant's opaque UUID reference (blocking on a cache miss).

    The reference keys per-tenant delivery capacity and is the only tenant
    identifier allowed into delivery logs. Never raises: a failed lookup
    falls back to the derived reference.
    """
    cached = _tenant_refs.get(tenant_id)
    if cached is not None:
        return cached
    reference = None
    try:
        row = db(db.tenants.id == tenant_id).select().first()
        reference = getattr(row, "village_id", None) if row else None
    except Exception as exc:
        logger.debug("webhook_tenant_ref_lookup_failed", error=type(exc).__name__)
    if not reference:
        return _fallback_tenant_ref(tenant_id)
    if len(_tenant_refs) >= _TENANT_REF_CACHE_MAX:
        _tenant_refs.clear()
    _tenant_refs[tenant_id] = str(reference)
    return str(reference)


async def resolve_tenant_ref(db: Any, tenant_id: Any) -> str:
    """Async form of :func:`tenant_ref`: off-loop lookup only on a cache miss."""
    cached = _tenant_refs.get(tenant_id)
    if cached is not None:
        return cached
    return await _delivery.run_in_io_pool(tenant_ref, db, tenant_id)


def _as_utc(value: Any) -> datetime:
    """Return ``value`` as an aware UTC datetime (naive values are UTC).

    Some backends hand timestamps back naive; comparing those with an aware
    cutoff would raise, so they are normalised first.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return datetime.now(UTC)


def delivery_idempotency_key(webhook_id: Any, delivery_id: Any) -> str:
    """Return the stable ``Idempotency-Key`` of one outbox delivery row.

    Derived from the row identity instead of generated per send, so a
    delivery resumed after a restart (possibly by another replica) carries
    the same key as the interrupted attempt and receivers can de-duplicate.
    """
    return uuid5(
        NAMESPACE_OID, f"elder-webhook-delivery:{webhook_id}:{delivery_id}"
    ).hex


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

    def _prepare_delivery(
        self, webhook_id: int, tenant_id: int, event_type: str, payload: dict[str, Any]
    ) -> tuple[int, Any, dict[str, Any]]:
        """Validate the webhook and insert its pending delivery row.

        The synchronous, database-only half of a delivery, split out so the
        row exists (and can be reported as queued) before any network I/O
        is scheduled off the request path.

        Raises:
            Exception: If the webhook is missing, disabled or not subscribed
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
            status=DELIVERY_PENDING,
            attempt_count=0,
            created_at=now,
        )
        self.db.commit()
        return delivery_id, webhook, delivery_payload

    def _insert_event_delivery(
        self, webhook_id: int, event_type: str, payload: dict[str, Any]
    ) -> int:
        """Insert the pending outbox row for an already-matched event webhook.

        Used by the event dispatchers (issue.assigned, user.created) that
        select their webhooks themselves. The stored payload is exactly the
        body that is signed and sent, so a resumed delivery is byte-identical.
        """
        delivery_id = self.db.webhook_deliveries.insert(
            webhook_id=webhook_id,
            event_type=event_type,
            request_payload=payload,
            status=DELIVERY_PENDING,
            attempt_count=0,
            created_at=datetime.now(UTC),
        )
        self.db.commit()
        return int(delivery_id)

    async def deliver_event(
        self,
        webhook: Any,
        event_type: str,
        payload: dict[str, Any],
        *,
        kind: str,
    ) -> dict[str, Any]:
        """Record and deliver one internally-triggered event to ``webhook``.

        The single path for event dispatchers: outbox row first, then the
        pinned, retrying engine - so they share SSRF vetting, retry policy,
        signing and telemetry with native deliveries. Never raises: a
        bookkeeping failure is returned as a failed result.
        """
        try:
            delivery_id = await _delivery.run_in_io_pool(
                self._insert_event_delivery, webhook.id, event_type, payload
            )
        except Exception as exc:
            logger.error(
                "webhook_delivery_record_failed",
                webhook_id=getattr(webhook, "id", None),
                event_type=event_type,
                error=_delivery.scrub_error(exc),
            )
            return {"success": False, "error": _delivery.scrub_error(exc)}
        return await self._attempt_delivery(
            delivery_id,
            webhook,
            payload,
            kind=kind,
            idempotency_key=delivery_idempotency_key(webhook.id, delivery_id),
        )

    async def deliver_webhook(
        self,
        webhook_id: int,
        tenant_id: int,
        event_type: str,
        payload: dict[str, Any],
        *,
        max_attempts: int | None = None,
        kind: str = "native",
    ) -> dict[str, Any]:
        """
        Deliver a webhook event and wait for the outcome without blocking the loop.

        Args:
            webhook_id: Webhook id
            tenant_id: Owning tenant (from the caller's validated JWT)
            event_type: Event type (e.g., "issue.assigned")
            payload: Event payload
            max_attempts: Attempt cap; None uses the configured retry budget
            kind: Bounded metric label for this delivery

        Returns:
            Delivery result dictionary

        Raises:
            DeliveryShedError: If the pending-delivery cap is reached (the
                caller is waiting on this send, so it is told to back off)
        """
        tenant = await resolve_tenant_ref(self.db, tenant_id)
        slot = _delivery.reserve_slots_or_raise(tenant)[0]
        try:
            delivery_id, webhook, delivery_payload = await _delivery.run_in_io_pool(
                self._prepare_delivery, webhook_id, tenant_id, event_type, payload
            )
            return await self._attempt_delivery(
                delivery_id,
                webhook,
                delivery_payload,
                max_attempts=max_attempts,
                kind=kind,
                idempotency_key=delivery_idempotency_key(webhook_id, delivery_id),
            )
        finally:
            _delivery.release_slot(slot)

    def _record_delivery_progress(
        self, delivery_id: int, attempt_base: int, result: "_delivery.DeliveryResult"
    ) -> None:
        """Mark a delivery ``retrying`` after a failed attempt (outbox state).

        Written before the backoff sleep so a process that dies mid-retry
        leaves an accurate, resumable row. Advancing attempt_count on every
        attempt also acts as the liveness signal the recovery pass checks
        before taking a delivery over. Blocking: run off-loop.
        """
        fields: dict[str, Any] = {
            "status": DELIVERY_RETRYING,
            "attempt_count": attempt_base + max(result.attempts, 1),
            "error_message": (result.error or result.outcome)[:500],
        }
        if result.status_code is not None:
            fields["http_status"] = result.status_code
        self.db(self.db.webhook_deliveries.id == delivery_id).update(**fields)
        self.db.commit()

    def _record_delivery_result(
        self,
        delivery_id: int,
        result: "_delivery.DeliveryResult",
        attempt_base: int = 0,
    ) -> None:
        """Persist a finished delivery's outcome on its webhook_deliveries row.

        Blocking database work: always run off-loop so the event loop keeps
        serving requests. attempt_count is ``attempt_base`` (attempts made
        before this send - non-zero for redeliveries and resumed rows) plus
        this send's attempts. Only a status code and a short, sanitized
        excerpt are stored - never response headers, and no response text at
        all for a private-network target (see shared.webhooks.delivery).
        """
        fields: dict[str, Any] = {
            "attempt_count": attempt_base + max(result.attempts, 1),
            "status": DELIVERY_SUCCESS if result.success else DELIVERY_FAILED,
        }
        if result.status_code is not None:
            fields["http_status"] = result.status_code
            fields["response_body"] = (
                _delivery.PRIVATE_TARGET_EXCERPT
                if result.private_target
                else _delivery.sanitize_excerpt(result.response_excerpt)
            )
        if result.success:
            fields["delivered_at"] = datetime.now(UTC)
        else:
            fields["error_message"] = (result.error or result.outcome)[:500]
        self.db(self.db.webhook_deliveries.id == delivery_id).update(**fields)
        self.db.commit()

    async def _attempt_delivery(
        self,
        delivery_id: int,
        webhook: Any,
        payload: dict[str, Any],
        *,
        max_attempts: int | None = None,
        kind: str = "native",
        attempt_base: int = 0,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """
        Deliver a webhook through the shared engine, HMAC-signed if a secret is set.

        attempt_base is the number of attempts already recorded on the row;
        idempotency_key, when given, is the row's stable key (omitted for a
        manual redelivery, which is a deliberate new send).

        Non-blocking: HTTP runs on the event loop via httpx with timeouts,
        retry/backoff and SSRF vetting (see shared.webhooks.delivery) and
        the result row is written in a worker thread. Never raises - a
        failure is recorded on the row and returned.

        Args:
            delivery_id: Delivery id (row already inserted by the caller)
            webhook: Webhook row
            payload: Delivery payload
            max_attempts: Attempt cap; None uses the configured retry budget
            kind: Bounded metric label for this delivery

        Returns:
            Delivery result
        """
        try:
            # Serialize once — this exact string is both signed and sent as
            # the raw body, so the HMAC a receiver computes over the bytes
            # it gets always matches.
            payload_str = json.dumps(payload, sort_keys=True)

            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Elder-Webhook/1.2.0",
            }

            if webhook.headers:
                headers.update(_parse_json_column(webhook.headers))

            if webhook.secret:
                headers["X-Elder-Signature"] = generate_signature(
                    webhook.secret, payload_str
                )

            tenant = await resolve_tenant_ref(
                self.db, getattr(webhook, "tenant_id", None)
            )

            async def _progress(partial: "_delivery.DeliveryResult") -> None:
                """Persist the retrying state before the engine backs off."""
                await _delivery.run_in_io_pool(
                    self._record_delivery_progress, delivery_id, attempt_base, partial
                )

            result = await _delivery.deliver(
                _delivery.DeliveryRequest(
                    url=webhook.url,
                    body=payload_str.encode("utf-8"),
                    headers=headers,
                    kind=kind,
                    idempotency_key=idempotency_key,
                    tenant=tenant,
                    # Adds X-Elder-Timestamp + X-Elder-Signature-V2 per
                    # attempt; the legacy header above is left untouched.
                    signing_secret=webhook.secret or None,
                ),
                policy=_delivery.default_policy(max_attempts=max_attempts),
                on_retry=_progress,
            )
        except Exception as e:
            result = _delivery.DeliveryResult(
                success=False,
                attempts=1,
                outcome="error",
                idempotency_key="",
                error=_delivery.scrub_error(e),
            )

        try:
            await _delivery.run_in_io_pool(
                self._record_delivery_result, delivery_id, result, attempt_base
            )
        except Exception as e:
            # The HTTP outcome is already known; a bookkeeping failure must
            # be loud in logs but must not turn into a raised delivery.
            logger.error(
                "webhook_delivery_record_failed",
                delivery_id=delivery_id,
                error=_delivery.scrub_error(e),
            )

        response: dict[str, Any] = {
            "delivery_id": delivery_id,
            "success": result.success,
            "attempts": result.attempts,
        }
        if result.status_code is not None:
            response["status_code"] = result.status_code
        if not result.success:
            response["error"] = result.error or result.outcome
        return response

    def _load_redelivery(
        self, webhook_id: int, tenant_id: int, delivery_id: int
    ) -> tuple[Any, dict[str, Any], int]:
        """Load a tenant-scoped webhook, one delivery's payload and attempt count.

        Blocking database half of a redelivery; always run off the event loop.

        Raises:
            Exception: If the webhook or delivery is missing or mismatched
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

        return (
            webhook,
            _parse_json_column(delivery.request_payload) or {},
            int(delivery.attempt_count or 0),
        )

    async def redeliver_webhook(
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
            DeliveryShedError: If the pending-delivery cap is reached
        """
        tenant = await resolve_tenant_ref(self.db, tenant_id)
        slot = _delivery.reserve_slots_or_raise(tenant)[0]
        try:
            webhook, payload, attempt_base = await _delivery.run_in_io_pool(
                self._load_redelivery, webhook_id, tenant_id, delivery_id
            )

            # Interactive: the caller waits for the answer, so a single
            # attempt (no backoff sleeps holding the HTTP request open).
            return await self._attempt_delivery(
                delivery_id,
                webhook,
                payload,
                max_attempts=1,
                kind="redelivery",
                attempt_base=attempt_base,
            )
        finally:
            _delivery.release_slot(slot)

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
                # Rows written before the excerpt cap may hold more text;
                # the same cap is applied on the way out.
                "response_body": (
                    _delivery.sanitize_excerpt(d.response_body)
                    if d.response_body
                    else d.response_body
                ),
                "error_message": d.error_message,
                "attempt_count": d.attempt_count,
                "delivered_at": d.delivered_at.isoformat() if d.delivered_at else None,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in deliveries
        ]

    async def test_webhook(self, webhook_id: int, tenant_id: int) -> dict[str, Any]:
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
        webhook = await _delivery.run_in_io_pool(
            lambda: (
                self.db(
                    (self.db.webhooks.id == webhook_id)
                    & (self.db.webhooks.tenant_id == tenant_id)
                )
                .select()
                .first()
            )
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

        return await self.deliver_webhook(
            webhook_id, tenant_id, event_type, test_payload, max_attempts=1, kind="test"
        )

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

    async def test_notification_rule(
        self, rule_id: int, tenant_id: int | None = None
    ) -> dict[str, Any]:
        """Send a test notification for a rule."""
        rule = await _delivery.run_in_io_pool(
            self._get_scoped_notification_rule_row, rule_id, tenant_id
        )

        test_payload = {
            "test": True,
            "rule_id": rule_id,
            "message": f"This is a test notification from Elder via {rule.channel}",
        }

        tenant = (
            await resolve_tenant_ref(self.db, tenant_id)
            if tenant_id is not None
            else None
        )
        slot = _delivery.reserve_slots_or_raise(tenant)[0]
        try:
            return await self._send_notification(rule, test_payload, max_attempts=1)
        finally:
            _delivery.release_slot(slot)

    async def _send_notification(
        self, rule: Any, payload: dict[str, Any], *, max_attempts: int | None = None
    ) -> dict[str, Any]:
        """Send a notification via the rule's channel without blocking the loop.

        Never raises: an unsupported channel or bad config is returned as a
        failed result so one rule cannot break a broadcast.
        """
        try:
            config = _parse_json_column(rule.config_json) or {}

            if rule.channel == "email":
                return self._send_email_notification(config, payload)
            elif rule.channel == "slack":
                return await self._send_chat_notification(
                    "slack", config, payload, max_attempts
                )
            elif rule.channel == "teams":
                return await self._send_chat_notification(
                    "teams", config, payload, max_attempts
                )
            elif rule.channel == "pagerduty":
                return await self._send_pagerduty_notification(
                    config, payload, max_attempts
                )
            else:
                raise Exception(f"Unsupported channel: {rule.channel}")

        except Exception as e:
            return {"success": False, "error": _delivery.scrub_error(e)}

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

    async def _post_notification(
        self,
        channel: str,
        url: str,
        body: dict[str, Any],
        expected_status: int,
        max_attempts: int | None,
    ) -> dict[str, Any]:
        """POST a channel notification through the shared delivery engine.

        One code path for Slack/Teams/PagerDuty so each gets the same
        timeouts, retry/backoff and SSRF vetting. The hook URL is itself the
        credential for Slack/Teams, so the result carries no URL.
        """
        result = await _delivery.deliver(
            _delivery.DeliveryRequest(
                url=url,
                body=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                kind="notification",
            ),
            policy=_delivery.default_policy(max_attempts=max_attempts),
        )
        response: dict[str, Any] = {
            "success": result.status_code == expected_status,
            "channel": channel,
        }
        if result.status_code is not None:
            response["status_code"] = result.status_code
        else:
            response["error"] = result.error or result.outcome
        return response

    async def _send_chat_notification(
        self,
        channel: str,
        config: dict[str, Any],
        payload: dict[str, Any],
        max_attempts: int | None = None,
    ) -> dict[str, Any]:
        """Send a Slack or Microsoft Teams incoming-webhook notification.

        Both channels take the same `{"text": ...}` body and answer 200.
        """
        webhook_url = config.get("webhook_url")
        if not webhook_url:
            raise Exception(f"{channel.capitalize()} webhook_url is required in config")

        return await self._post_notification(
            channel,
            webhook_url,
            {"text": json.dumps(payload, indent=2)},
            200,
            max_attempts,
        )

    async def _send_pagerduty_notification(
        self,
        config: dict[str, Any],
        payload: dict[str, Any],
        max_attempts: int | None = None,
    ) -> dict[str, Any]:
        """Send a PagerDuty Events v2 trigger (accepted with HTTP 202)."""
        routing_key = config.get("routing_key")
        if not routing_key:
            raise Exception("PagerDuty routing_key is required in config")

        return await self._post_notification(
            "pagerduty",
            "https://events.pagerduty.com/v2/enqueue",
            {
                "routing_key": routing_key,
                "event_action": "trigger",
                "payload": {
                    "summary": "Elder Notification",
                    "source": "elder",
                    "severity": "info",
                    "custom_details": payload,
                },
            },
            202,
            max_attempts,
        )

    # Outbox recovery: resume deliveries a previous process left unfinished.

    def _find_resumable_deliveries(
        self, min_age: float, batch: int
    ) -> list[tuple[int, int, Any]]:
        """Return ``(id, attempt_count, created_at)`` of old unfinished rows.

        Only rows older than ``min_age`` seconds qualify, so a delivery a
        live process queued moments ago is never mistaken for an orphan;
        oldest first, at most ``batch`` rows. Blocking: run off-loop.
        """
        table = self.db.webhook_deliveries
        cutoff = datetime.now(UTC) - timedelta(seconds=min_age)
        rows = self.db(
            table.status.belongs(_RESUMABLE_STATUSES)
            & (table.created_at < cutoff)
            & self._unclaimed(min_age)
        ).select(orderby=table.created_at, limitby=(0, batch))
        return [(int(r.id), int(r.attempt_count or 0), r.created_at) for r in rows]

    def _unclaimed(self, min_age: float) -> Any:
        """Rows no recovery pass has claimed within the last ``min_age`` seconds.

        A claim stamps ``claimed_at``; until it ages out, the row is in
        flight with some replica and must not be observed as an orphan.
        """
        table = self.db.webhook_deliveries
        stale = datetime.now(UTC) - timedelta(seconds=min_age)
        return (table.claimed_at == None) | (table.claimed_at < stale)  # noqa: E711

    def _transition_delivery(
        self,
        delivery_id: int,
        seen_attempts: int,
        min_age: float = 0.0,
        **fields: Any,
    ) -> bool:
        """Atomically move a resumable row on, if nobody else has touched it.

        A single conditional UPDATE (compare-and-swap on status and
        attempt_count): of several replicas racing for the same row exactly
        one sees a row count of 1 and owns it; the rest back off. A row a
        live process is still working on has advanced its attempt_count
        since it was observed, so the swap fails there too.
        """
        table = self.db.webhook_deliveries
        # Rows written before the outbox may hold NULL instead of 0.
        attempts_match = (
            (table.attempt_count == seen_attempts)
            if seen_attempts
            else ((table.attempt_count == 0) | (table.attempt_count == None))  # noqa: E711
        )
        changed = self.db(
            (table.id == delivery_id)
            & table.status.belongs(_RESUMABLE_STATUSES)
            & attempts_match
            & self._unclaimed(min_age)
        ).update(**fields)
        self.db.commit()
        return changed == 1

    def _load_claimed_delivery(self, delivery_id: int) -> tuple[Any, Any]:
        """Return ``(delivery, webhook)`` for a claimed row (webhook may be None)."""
        delivery = self.db.webhook_deliveries[delivery_id]
        webhook = None
        if delivery is not None:
            webhook = (
                self.db(self.db.webhooks.id == delivery.webhook_id).select().first()
            )
        return delivery, webhook

    def _fail_delivery(self, delivery_id: int, reason: str) -> None:
        """Mark a claimed row terminally failed with a short reason."""
        self.db(self.db.webhook_deliveries.id == delivery_id).update(
            status=DELIVERY_FAILED, error_message=reason[:500]
        )
        self.db.commit()

    async def _resume_delivery(
        self, delivery_id: int, seen_attempts: int, min_age: float = 0.0
    ) -> str:
        """Claim one orphaned row and deliver it; return the outcome label.

        The claim is taken immediately before sending (not for the whole
        batch up front) so the time a claimed row sits idle - during which
        another starting replica could not tell it from an orphan - stays
        as short as one delivery.
        """
        claimed = await _delivery.run_in_io_pool(
            lambda: self._transition_delivery(
                delivery_id,
                seen_attempts,
                min_age,
                status=DELIVERY_RETRYING,
                attempt_count=seen_attempts + 1,
                claimed_at=datetime.now(UTC),
            )
        )
        if not claimed:
            return "skipped"
        delivery, webhook = await _delivery.run_in_io_pool(
            self._load_claimed_delivery, delivery_id
        )
        if delivery is None or webhook is None or not webhook.is_active:
            await _delivery.run_in_io_pool(
                self._fail_delivery, delivery_id, "webhook disabled or deleted"
            )
            return "failed"
        tenant = await resolve_tenant_ref(self.db, webhook.tenant_id)
        slot = _delivery.reserve_slot(tenant, host=_delivery.target_host(webhook.url))
        if slot is None:
            # Shed: the row stays ``retrying`` and a later pass resumes it.
            return "shed"
        try:
            await self._attempt_delivery(
                delivery_id,
                webhook,
                _parse_json_column(delivery.request_payload) or {},
                kind="recovery",
                attempt_base=seen_attempts,
                idempotency_key=delivery_idempotency_key(webhook.id, delivery_id),
            )
        finally:
            _delivery.release_slot(slot)
        return "resumed"

    async def recover_pending_deliveries(
        self, *, sleep: Any = asyncio.sleep
    ) -> dict[str, int]:
        """Resume or expire deliveries left ``pending``/``retrying`` by a restart.

        Startup pass over the webhook_deliveries outbox. Bounded
        (``WEBHOOK_RECOVERY_BATCH`` rows, 0 disables) and safe with several
        replicas: candidates are observed, the pass waits
        ``WEBHOOK_RECOVERY_OBSERVE_SECONDS`` (longer than one attempt plus
        backoff, so a live owner advances the row meanwhile), then each row
        is taken with an atomic compare-and-swap and delivered with its
        original Idempotency-Key. Rows older than
        ``WEBHOOK_RECOVERY_MAX_AGE_SECONDS`` are marked ``expired`` instead:
        a stale event is worse than a missing one. Delivery remains
        at-least-once; receivers de-duplicate on the key. Never raises.
        """
        counts = {"candidates": 0, "resumed": 0, "expired": 0, "skipped": 0}
        counts.update({"failed": 0, "shed": 0})
        batch = env_int("WEBHOOK_RECOVERY_BATCH", 100, minimum=0, maximum=5000)
        if batch == 0:
            return counts
        min_age = env_float(
            "WEBHOOK_RECOVERY_MIN_AGE_SECONDS", 300.0, minimum=0.0, maximum=86400.0
        )
        max_age = env_float(
            "WEBHOOK_RECOVERY_MAX_AGE_SECONDS", 86400.0, minimum=60.0, maximum=2592000.0
        )
        observe = env_float(
            "WEBHOOK_RECOVERY_OBSERVE_SECONDS", 120.0, minimum=0.0, maximum=3600.0
        )
        try:
            candidates = await _delivery.run_in_io_pool(
                self._find_resumable_deliveries, min_age, batch
            )
            counts["candidates"] = len(candidates)
            if not candidates:
                return counts
            await sleep(observe)

            expiry_cutoff = datetime.now(UTC) - timedelta(seconds=max_age)
            queue: asyncio.Queue[tuple[int, int, Any]] = asyncio.Queue()
            for candidate in candidates:
                queue.put_nowait(candidate)

            async def _worker() -> None:
                """Drain the candidate queue, one claim-and-send at a time."""
                while True:
                    try:
                        delivery_id, seen, created_at = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    try:
                        if _as_utc(created_at) < expiry_cutoff:
                            expired = await _delivery.run_in_io_pool(
                                lambda d=delivery_id, a=seen: self._transition_delivery(
                                    d,
                                    a,
                                    min_age,
                                    status=DELIVERY_EXPIRED,
                                    error_message="expired before delivery",
                                )
                            )
                            counts["expired" if expired else "skipped"] += 1
                        else:
                            counts[
                                await self._resume_delivery(delivery_id, seen, min_age)
                            ] += 1
                    except Exception as exc:
                        counts["failed"] += 1
                        logger.error(
                            "webhook_delivery_recovery_row_failed",
                            delivery_id=delivery_id,
                            error=_delivery.scrub_error(exc),
                        )

            workers = env_int("WEBHOOK_RECOVERY_WORKERS", 4, minimum=1, maximum=32)
            await asyncio.gather(*(_worker() for _ in range(workers)))
        except Exception as exc:
            logger.error(
                "webhook_delivery_recovery_failed", error=_delivery.scrub_error(exc)
            )
        log = logger.info if counts["candidates"] else logger.debug
        log("webhook_delivery_recovery_finished", **counts)
        return counts

    # ===========================
    # Event Broadcasting Methods
    # ===========================

    async def broadcast_event(
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

        Deliveries are queued, not awaited: each matching webhook gets its
        pending delivery row here and is then delivered (with retry and
        backoff) by a background task, so a slow or dead receiver can never
        delay or fail the broadcasting request. Outcomes land on the
        webhook_deliveries rows (GET /webhooks/<id>/deliveries).

        Returns:
            Broadcast result with queued counts and delivery ids

        Raises:
            DeliveryShedError: If the fan-out does not fit under the global
                or per-tenant pending-delivery cap (nothing is queued)
        """
        # Webhook half: reconciled to the real `is_active` column (was
        # `enabled`, compared with the always-false `is True` identity
        # check — see this plan's intro). The notification_rules half below
        # still references stale columns (events_json/enabled vs the real
        # event_types/channels/is_active) — that table's drift is a
        # separate, out-of-scope follow-up (see Task 1's model docstring);
        # left unchanged here.
        webhooks = await _delivery.run_in_io_pool(
            lambda: self.db(
                (self.db.webhooks.organization_id == organization_id)
                & (self.db.webhooks.tenant_id == tenant_id)
                & (self.db.webhooks.is_active == True)  # noqa: E712
            ).select()
        )

        rules = await _delivery.run_in_io_pool(
            lambda: self.db(
                (self.db.notification_rules.organization_id == organization_id)
                & (self.db.notification_rules.enabled == True)  # noqa: E712
            ).select()
        )

        matched_webhooks = [
            webhook
            for webhook in webhooks
            if event_type in (_parse_json_column(webhook.events) or [])
        ]
        matched_rules = []
        for rule in rules:
            raw_events = getattr(rule, "events", None)
            if raw_events is None:
                raw_events = getattr(rule, "events_json", None)
            if event_type in (_parse_json_column(raw_events) or []):
                matched_rules.append(rule)

        # Admission is all-or-nothing and happens before any row is written:
        # a broadcast that does not fit under the pending caps is refused
        # whole (429 at the route) rather than half-queued, so the caller
        # can retry it without duplicating the part that got through.
        tenant = await resolve_tenant_ref(self.db, tenant_id)
        fan_out = len(matched_webhooks) + len(matched_rules)
        slots = _delivery.reserve_slots_or_raise(tenant, fan_out) if fan_out else []

        webhook_results = []
        for webhook in matched_webhooks:
            slot = slots.pop()
            try:
                delivery_id, row, delivery_payload = await _delivery.run_in_io_pool(
                    self._prepare_delivery,
                    webhook.id,
                    webhook.tenant_id,
                    event_type,
                    payload,
                )
                _delivery.schedule_background(
                    self._attempt_delivery(
                        delivery_id,
                        row,
                        delivery_payload,
                        idempotency_key=delivery_idempotency_key(
                            webhook.id, delivery_id
                        ),
                    ),
                    name="webhook-broadcast-delivery",
                    slot=slot,
                    host=_delivery.target_host(row.url),
                    delivery_id=delivery_id,
                )
                webhook_results.append(
                    {
                        "webhook_id": webhook.id,
                        "delivery_id": delivery_id,
                        "queued": True,
                    }
                )
            except Exception as e:
                _delivery.release_slot(slot)
                webhook_results.append(
                    {
                        "webhook_id": webhook.id,
                        "queued": False,
                        "error": _delivery.scrub_error(e),
                    }
                )

        notification_results = []
        for rule in matched_rules:
            slot = slots.pop()
            try:
                _delivery.schedule_background(
                    self._send_notification(rule, payload),
                    name="webhook-broadcast-notification",
                    slot=slot,
                )
                notification_results.append({"rule_id": rule.id, "queued": True})
            except Exception as e:
                _delivery.release_slot(slot)
                notification_results.append(
                    {
                        "rule_id": rule.id,
                        "queued": False,
                        "error": _delivery.scrub_error(e),
                    }
                )

        return {
            "event_type": event_type,
            "webhooks_triggered": len(webhook_results),
            "webhooks_queued": sum(1 for r in webhook_results if r.get("queued")),
            "notifications_triggered": len(notification_results),
            "notifications_queued": sum(
                1 for r in notification_results if r.get("queued")
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
