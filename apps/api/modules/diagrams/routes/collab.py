"""Real-time diagram collaboration via WebSocket with Redis pub/sub.

Provides:
- Short-lived ticket endpoint for WebSocket auth (avoids Authorization header issues)
- WebSocket endpoint with permission-based editing
- Redis pub/sub broadcast for cursor tracking and drawing changes
- License + PostHog flag gating (collaboration feature is DARK by default)
"""

# flake8: noqa: E501

import asyncio
import logging
import secrets
from datetime import UTC, datetime, timezone
from typing import Optional

import redis.asyncio as aioredis
import structlog
from quart import Blueprint, current_app, g, jsonify, request, websocket

from apps.api.auth.decorators import login_required, require_scope
from apps.api.common.flags.posthog_client import flag_enabled
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool

from .diagrams import _can_edit_diagram, _can_read_diagram, _get_tenant_id

logger = logging.getLogger(__name__)
log = structlog.get_logger()

collab_bp = Blueprint("diagram_collab", __name__)


def _is_collaboration_enabled(app, identity_id: str) -> tuple[bool, str | None]:
    """Check if collaboration is enabled via license + PostHog flag.

    Args:
        app: Quart application instance
        identity_id: Identity ID for flag evaluation

    Returns:
        tuple: (enabled: bool, reason: Optional error message)
    """
    # Check license (layer 1)
    try:
        license_client = app.extensions.get("license_client")
        if license_client:
            try:
                validation = license_client.validate()
                # For now, treat collaboration as available if license is valid
                # In future, can check specific feature entitlements
                if not validation:
                    return False, "License validation failed"
            except Exception as e:
                log.warning("license_check_failed_collab", error=str(e))
                # Fail gracefully on license infra errors
                pass
    except Exception as e:
        log.warning("license_client_not_available", error=str(e))

    # Check PostHog flag (layer 2)
    try:
        flag_key = "elder.diagrams.collaboration"
        is_enabled = flag_enabled(flag_key, identity_id, default=False)
        if not is_enabled:
            return False, "Collaboration feature is not enabled"
    except Exception as e:
        log.warning("posthog_flag_check_failed_collab", flag_key=flag_key, error=str(e))
        # Fail gracefully: if PostHog unavailable, treat as disabled (safe default)
        return False, "Collaboration feature check unavailable"

    return True, None


@collab_bp.route("/<int:diagram_id>/collab/ticket", methods=["POST"])
@login_required
@require_scope("diagrams:read")
async def issue_collab_ticket(diagram_id: int):
    """Issue a short-lived ticket for WebSocket authentication.

    WebSocket cannot set Authorization headers, so we mint a ticket in REST
    and pass it as a query parameter. Ticket is single-use and expires in 60s.

    Requires: diagrams:read scope

    Returns:
        200: {ticket, ws_path}
        403: License/flag not enabled, or diagram not readable
        404: Diagram not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")
    if not identity_id:
        return ApiResponse.error("Identity not found in token", 403)

    # Check collaboration gating
    enabled, reason = _is_collaboration_enabled(current_app, str(identity_id))
    if not enabled:
        return ApiResponse.error(reason or "Collaboration not enabled", 403)

    # Verify diagram exists and is readable
    def fetch_diagram():
        return (
            db(
                (db.dg_diagrams.id == diagram_id)
                & (db.dg_diagrams.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

    diagram = await run_in_threadpool(fetch_diagram)

    if not diagram:
        return ApiResponse.not_found("Diagram")

    if not _can_read_diagram(db, diagram, tenant_id, identity_id):
        return ApiResponse.not_found("Diagram")

    # Determine permission level
    can_edit = _can_edit_diagram(db, diagram, tenant_id, identity_id)
    permission = "editor" if can_edit else "viewer"

    # Mint ticket: random token stored in Redis with 60s TTL
    ticket = secrets.token_urlsafe(32)
    ticket_data = {
        "diagram_id": diagram_id,
        "identity_id": identity_id,
        "tenant_id": tenant_id,
        "permission": permission,
    }

    try:
        redis_client = current_app.extensions.get("module_redis")
        if redis_client:
            redis_client.setex(
                f"elder:dg:ticket:{ticket}",
                60,  # 60s TTL
                __import__("json").dumps(ticket_data),
            )
        else:
            log.warning("redis_not_available_for_ticket")
            return ApiResponse.error(
                "Collaboration service temporarily unavailable", 503
            )
    except Exception as e:
        log.error("ticket_mint_failed", error=str(e))
        return ApiResponse.error("Failed to issue collaboration ticket", 500)

    return (
        jsonify(
            {
                "ticket": ticket,
                "ws_path": f"/api/v1/diagrams/{diagram_id}/collab/ws?ticket={ticket}",
            }
        ),
        200,
    )


@collab_bp.websocket("/<int:diagram_id>/collab/ws")
async def collab_ws(diagram_id: int):
    """WebSocket endpoint for real-time diagram collaboration.

    Protocol:
    - ticket: query param with single-use auth token (validated once at connect)
    - Events from client:
        - {type: "cursor", x: int, y: int}: Broadcast cursor position
        - {type: "op", payload: {...}}: Drawing op (editor only)
        - {type: "presence"}: Get active collaborators
    - Events to client:
        - {type: "user_joined", identity_id, session_id}
        - {type: "cursor_moved", identity_id, x, y, session_id}
        - {type: "drawing_changed", payload, session_id}
        - {type: "user_left", identity_id, session_id}
        - {type: "collaborators", [...]}

    Permissions:
    - viewer: cursor/presence only
    - editor: cursor/presence + drawing ops
    """
    import json

    session_id = None
    can_edit = False
    identity_id = None
    redis_pubsub = None

    try:
        # Validate ticket from query params
        ticket = request.args.get("ticket")
        if not ticket:
            log.warning("ws_connect_no_ticket", diagram_id=diagram_id)
            await websocket.close(1008)
            return

        # Retrieve and delete ticket (single-use)
        redis_client = current_app.extensions.get("module_redis")
        if not redis_client:
            log.error("redis_not_available_ws", diagram_id=diagram_id)
            await websocket.close(1011)
            return

        try:
            ticket_json = redis_client.getdel(f"elder:dg:ticket:{ticket}")
        except Exception as e:
            log.error("ticket_validation_failed", error=str(e))
            await websocket.close(1008)
            return

        if not ticket_json:
            log.warning("ws_invalid_ticket", diagram_id=diagram_id, ticket=ticket[:8])
            await websocket.close(1008)
            return

        try:
            ticket_data = json.loads(ticket_json)
        except json.JSONDecodeError:
            log.error("ticket_parse_failed", diagram_id=diagram_id)
            await websocket.close(1008)
            return

        # Verify ticket matches path
        if ticket_data.get("diagram_id") != diagram_id:
            log.warning(
                "ws_ticket_mismatch",
                ticket_diagram=ticket_data.get("diagram_id"),
                path_diagram=diagram_id,
            )
            await websocket.close(1008)
            return

        identity_id = ticket_data.get("identity_id")
        tenant_id = ticket_data.get("tenant_id")
        permission = ticket_data.get("permission", "viewer")
        can_edit = permission == "editor"
        session_id = secrets.token_hex(16)

        # Accept the connection
        await websocket.accept()
        log.info(
            "ws_connected",
            diagram_id=diagram_id,
            identity_id=identity_id,
            session_id=session_id,
            permission=permission,
        )

        # Register presence in database
        def register_session():
            now = datetime.now(UTC)
            return current_app.db.dg_collaboration_sessions.insert(
                diagram_id=diagram_id,
                identity_id=identity_id,
                tenant_id=tenant_id,
                session_id=session_id,
                socket_id=session_id,
                permission=permission,
                is_active=True,
                joined_at=now,
                last_active_at=now,
            )

        try:
            await run_in_threadpool(register_session)
        except Exception as e:
            log.error("session_registration_failed", error=str(e))
            await websocket.close(1011)
            return

        # Connect to Redis pub/sub for this diagram
        channel_name = f"elder:dg:{diagram_id}"

        async def publish_to_redis(message_type: str, payload: dict):
            """Publish message to Redis pub/sub channel."""
            try:
                msg = json.dumps(
                    {
                        "type": message_type,
                        "sender_session": session_id,
                        **payload,
                    }
                )
                redis_client.publish(channel_name, msg)
            except Exception as e:
                log.warning(
                    "publish_failed",
                    channel=channel_name,
                    message_type=message_type,
                    error=str(e),
                )

        async def fetch_active_collaborators():
            """Fetch list of active collaborators for this diagram."""
            try:

                def query():
                    rows = current_app.db(
                        (
                            current_app.db.dg_collaboration_sessions.diagram_id
                            == diagram_id
                        )
                        & (current_app.db.dg_collaboration_sessions.is_active == True)
                    ).select()
                    return [
                        {
                            "identity_id": r.identity_id,
                            "session_id": r.session_id,
                            "permission": r.permission,
                        }
                        for r in rows
                    ]

                return await run_in_threadpool(query)
            except Exception as e:
                log.warning("fetch_collaborators_failed", error=str(e))
                return []

        # Publish user_joined
        await publish_to_redis("user_joined", {"identity_id": identity_id})

        # Send current collaborators to this client
        collaborators = await fetch_active_collaborators()
        try:
            await websocket.send(
                json.dumps({"type": "collaborators", "users": collaborators})
            )
        except Exception as e:
            log.warning("send_collaborators_failed", error=str(e))

        # Coroutine 1: Receive from websocket and broadcast to Redis
        async def handle_client_messages():
            try:
                while True:
                    data_str = await websocket.receive()
                    if not data_str:
                        break

                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        log.warning("ws_invalid_json", session_id=session_id)
                        continue

                    msg_type = data.get("type")

                    if msg_type == "cursor":
                        # Cursor move (allowed for all)
                        x = data.get("x")
                        y = data.get("y")
                        if x is not None and y is not None:
                            # Update last cursor in DB
                            def update_cursor():
                                current_app.db(
                                    current_app.db.dg_collaboration_sessions.session_id
                                    == session_id
                                ).update(
                                    last_cursor_x=x,
                                    last_cursor_y=y,
                                    last_active_at=datetime.now(UTC),
                                )
                                current_app.db.commit()

                            try:
                                await run_in_threadpool(update_cursor)
                            except Exception as e:
                                log.warning("cursor_update_failed", error=str(e))

                            # Broadcast
                            await publish_to_redis(
                                "cursor_moved",
                                {"identity_id": identity_id, "x": x, "y": y},
                            )

                    elif msg_type == "op" or msg_type == "drawing_change":
                        # Drawing operation (editor only)
                        if not can_edit:
                            log.warning(
                                "drawing_op_rejected_unauthorized",
                                session_id=session_id,
                                permission=permission,
                            )
                            continue

                        payload = data.get("payload", {})
                        # Broadcast the op to other clients
                        await publish_to_redis("drawing_changed", {"payload": payload})

                    elif msg_type == "presence":
                        # Get current collaborators
                        collaborators = await fetch_active_collaborators()
                        try:
                            await websocket.send(
                                json.dumps(
                                    {"type": "collaborators", "users": collaborators}
                                )
                            )
                        except Exception as e:
                            log.warning("send_collaborators_failed", error=str(e))

            except Exception as e:
                if not isinstance(e, asyncio.CancelledError):
                    log.warning("client_message_handler_error", error=str(e))

        # Coroutine 2: Subscribe to Redis and forward to this websocket
        async def handle_redis_messages():
            try:
                # Create async Redis connection
                redis_sub = aioredis.from_url(
                    current_app.config.get("REDIS_URL", "redis://localhost:6379/0")
                )
                async with redis_sub.pubsub() as pubsub:
                    await pubsub.subscribe(channel_name)

                    async for msg in pubsub.listen():
                        if msg["type"] != "message":
                            continue

                        try:
                            payload = json.loads(msg["data"])
                        except (json.JSONDecodeError, TypeError):
                            continue

                        # Skip echo (don't send sender's own message back)
                        if payload.get("sender_session") == session_id:
                            continue

                        try:
                            await websocket.send(json.dumps(payload))
                        except Exception as e:
                            log.warning("send_redis_message_failed", error=str(e))
                            break

            except Exception as e:
                if not isinstance(e, asyncio.CancelledError):
                    log.warning("redis_message_handler_error", error=str(e))

        # Run both coroutines concurrently until one fails
        try:
            await asyncio.gather(handle_client_messages(), handle_redis_messages())
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning("ws_gather_error", error=str(e))

    except Exception as e:
        log.error("ws_handler_unexpected_error", error=str(e))
    finally:
        # Cleanup: mark session as inactive
        if session_id:
            try:

                def mark_inactive():
                    current_app.db(
                        current_app.db.dg_collaboration_sessions.session_id
                        == session_id
                    ).update(
                        is_active=False,
                        left_at=datetime.now(UTC),
                    )
                    current_app.db.commit()

                await run_in_threadpool(mark_inactive)

                # Broadcast user_left
                if identity_id:
                    try:
                        redis_client = current_app.extensions.get("module_redis")
                        if redis_client:
                            msg = json.dumps(
                                {
                                    "type": "user_left",
                                    "identity_id": identity_id,
                                    "sender_session": session_id,
                                }
                            )
                            redis_client.publish(f"elder:dg:{diagram_id}", msg)
                    except Exception as e:
                        log.warning("broadcast_user_left_failed", error=str(e))

                log.info(
                    "ws_disconnected",
                    diagram_id=diagram_id,
                    identity_id=identity_id,
                    session_id=session_id,
                )

            except Exception as e:
                log.error("session_cleanup_failed", error=str(e))
