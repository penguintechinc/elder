"""Streams CRUD, duplicate, lock, execute, and executions endpoints using penguin-dal."""

# flake8: noqa: E501

import logging
import uuid
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.common.licensing.enforce import check_limit
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams
from shared.utils.village_id import generate_village_id

logger = logging.getLogger(__name__)

bp = Blueprint("streams", __name__)

# Editor lock timeout in minutes
EDITOR_LOCK_TIMEOUT_MINUTES = 30


def _get_tenant_id() -> int:
    """Extract tenant_id from g.claims (populated by before_request)."""
    claims = getattr(g, "claims", {}) or {}
    tenant_str = claims.get("tenant", "")
    if not tenant_str:
        return None
    try:
        return int(tenant_str)
    except (ValueError, TypeError):
        return None


def _get_identity_id() -> int:
    """Extract identity_id from g.claims."""
    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")
    if identity_id:
        try:
            return int(identity_id)
        except (ValueError, TypeError):
            pass
    return None


def _can_read_stream(db, stream_row, tenant_id, identity_id=None) -> bool:
    """Check if current user can read this stream based on visibility and sharing.

    Args:
        db: PyDAL database instance
        stream_row: Stream row from database
        tenant_id: Current tenant ID
        identity_id: Current identity ID (optional, from token)

    Returns:
        True if user can read, False otherwise
    """
    # Cross-tenant isolation: stream must belong to this tenant
    if stream_row.tenant_id != tenant_id:
        return False

    # Public streams visible to everyone
    if stream_row.is_public:
        return True

    # Unauthenticated users can't read private streams
    if identity_id is None:
        return False

    # Owner can always read their own stream
    if stream_row.owner_identity_id == identity_id:
        return True

    # Check shared access: query stream_shares table
    share_row = (
        db(
            (db.stream_shares.playbook_id == stream_row.id)
            & (db.stream_shares.shared_with_identity_id == identity_id)
        )
        .select()
        .first()
    )
    if share_row:
        return True

    return False


def _can_edit_stream(db, stream_row, tenant_id, identity_id=None) -> bool:
    """Write authorization for a stream — STRICTER than read.

    Only the owner, or a user holding an ``editor``-permission share, may
    mutate a stream.

    Args:
        db: PyDAL database instance
        stream_row: Stream row from database
        tenant_id: Current tenant ID
        identity_id: Current identity ID (from token)

    Returns:
        True if the caller may modify this stream, False otherwise.
    """
    # Cross-tenant isolation.
    if stream_row.tenant_id != tenant_id:
        return False

    if identity_id is None:
        return False

    # Owner always has write access.
    if stream_row.owner_identity_id == identity_id:
        return True

    # Editor-permission share grants write; viewer shares do not.
    share_row = (
        db(
            (db.stream_shares.playbook_id == stream_row.id)
            & (db.stream_shares.shared_with_identity_id == identity_id)
            & (db.stream_shares.permission == "editor")
        )
        .select()
        .first()
    )
    return bool(share_row)


def _serialize_stream(db, stream_row, version_row=None, lock_info=None):
    """Serialize a stream playbook row to JSON-friendly dict."""
    result = {
        "id": stream_row.id,
        "village_id": stream_row.village_id,
        "name": stream_row.name,
        "description": stream_row.description or "",
        "owner_identity_id": stream_row.owner_identity_id,
        "created_by_identity_id": stream_row.created_by_identity_id,
        "trigger_type": stream_row.trigger_type,
        "is_public": stream_row.is_public,
        "is_template": stream_row.is_template,
        "is_enabled": stream_row.is_enabled,
        "status": stream_row.status,
        "tags": stream_row.tags or [],
        "execution_count": stream_row.execution_count or 0,
        "success_count": stream_row.success_count or 0,
        "failure_count": stream_row.failure_count or 0,
        "last_execution_at": (
            stream_row.last_execution_at.isoformat()
            if stream_row.last_execution_at
            else None
        ),
        "created_at": (
            stream_row.created_at.isoformat() if stream_row.created_at else None
        ),
        "updated_at": (
            stream_row.updated_at.isoformat() if stream_row.updated_at else None
        ),
    }
    if version_row:
        result["canvas_data"] = version_row.canvas_json or {"nodes": [], "edges": []}
        result["version"] = version_row.version_number
    if lock_info:
        result["editor_lock"] = lock_info
    return result


def _serialize_execution(execution, stream_id=None, stream_name=None):
    """Serialize an execution record to JSON-friendly dict.

    Args:
        execution: Execution row from database
        stream_id: Optional stream/playbook ID (for tenant-wide queries)
        stream_name: Optional stream/playbook name (for tenant-wide queries)

    Returns:
        Dict with execution details including stream identity for tenant-wide views
    """
    result = {
        "id": execution.id,
        "execution_id": execution.execution_id,
        "playbook_id": execution.playbook_id,
        "status": execution.status,
        "trigger_type": execution.trigger_type,
        "input_data": execution.input_json,
        "output_data": execution.output_json,
        "error_message": execution.error_message,
        "started_at": (
            execution.started_at.isoformat() if execution.started_at else None
        ),
        "completed_at": (
            execution.completed_at.isoformat() if execution.completed_at else None
        ),
        "created_at": (
            execution.started_at.isoformat() if execution.started_at else None
        ),
        "duration_ms": execution.duration_ms,
    }
    # Include stream identity if provided (used in tenant-wide listing)
    if stream_id is not None:
        result["stream_id"] = stream_id
    if stream_name is not None:
        result["stream_name"] = stream_name
    return result


def _serialize_lock(lock, user_is_holder=False):
    """Serialize an editor lock to JSON-friendly dict."""
    return {
        "playbook_id": lock.playbook_id,
        "locked_by_identity_id": lock.locked_by_identity_id,
        "locked_by_name": lock.locked_by_name,
        "locked_at": lock.locked_at.isoformat() if lock.locked_at else None,
        "expires_at": lock.expires_at.isoformat() if lock.expires_at else None,
        "is_holder": user_is_holder,
    }


def _sync_stream_nodes_edges(db, tenant_id: int, stream_id: int, canvas: dict) -> None:
    """Sync normalized `stream_nodes` and `stream_edges` tables from canvas JSON."""
    now = datetime.now(UTC)
    db(
        (db.stream_nodes.playbook_id == stream_id)
        & (db.stream_nodes.tenant_id == tenant_id)
    ).delete()
    db(
        (db.stream_edges.playbook_id == stream_id)
        & (db.stream_edges.tenant_id == tenant_id)
    ).delete()

    raw_nodes = canvas.get("nodes") or []
    for idx, raw_node in enumerate(raw_nodes):
        if not isinstance(raw_node, dict):
            continue
        raw_data = raw_node.get("data")
        data_obj: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        node_id = str(raw_node.get("id") or raw_node.get("node_id") or "")[:100]
        if not node_id:
            continue
        node_type = str(
            raw_node.get("nodeType")
            or raw_node.get("node_type")
            or data_obj.get("nodeType")
            or data_obj.get("node_type")
            or raw_node.get("type")
            or ""
        )[:50]
        node_category = str(
            raw_node.get("category")
            or raw_node.get("node_category")
            or data_obj.get("category")
            or "transform"
        )[:50]
        label = str(raw_node.get("label") or data_obj.get("label") or node_type)[:255]
        raw_pos = raw_node.get("position")
        pos: dict[str, Any] = raw_pos if isinstance(raw_pos, dict) else {}
        pos_x = int(round(float(raw_node.get("position_x", pos.get("x", 0)) or 0)))
        pos_y = int(round(float(raw_node.get("position_y", pos.get("y", 0)) or 0)))
        config = raw_node.get("config")
        if config is None:
            config = data_obj.get("config")
        if not isinstance(config, dict):
            config = {}

        db.stream_nodes.insert(
            tenant_id=tenant_id,
            playbook_id=stream_id,
            node_id=node_id,
            node_type=node_type,
            node_category=node_category,
            label=label,
            position_x=pos_x,
            position_y=pos_y,
            config=config,
            is_enabled=True,
            execution_order=idx,
            created_at=now,
            updated_at=now,
        )

    raw_edges = canvas.get("edges") or []
    for idx, raw_edge in enumerate(raw_edges):
        if not isinstance(raw_edge, dict):
            continue
        source_id = str(raw_edge.get("source") or raw_edge.get("source_node_id") or "")[
            :100
        ]
        target_id = str(raw_edge.get("target") or raw_edge.get("target_node_id") or "")[
            :100
        ]
        if not source_id or not target_id:
            continue
        edge_id = str(raw_edge.get("id") or f"e-{source_id}-{target_id}-{idx}")[:100]
        src_handle = raw_edge.get("sourceHandle") or raw_edge.get("source_handle")
        tgt_handle = raw_edge.get("targetHandle") or raw_edge.get("target_handle")
        db.stream_edges.insert(
            tenant_id=tenant_id,
            playbook_id=stream_id,
            edge_id=edge_id,
            source_node_id=source_id,
            target_node_id=target_id,
            source_handle=str(src_handle)[:50] if src_handle else None,
            target_handle=str(tgt_handle)[:50] if tgt_handle else None,
            label=(str(raw_edge.get("label"))[:255] if raw_edge.get("label") else None),
            created_at=now,
            updated_at=now,
        )


def _load_stream_nodes_edges(
    db, tenant_id: int, stream_id: int
) -> tuple[list[dict], list[dict]]:
    """Load stream nodes & edges from normalized tables or latest version snapshot."""
    node_rows = db(
        (db.stream_nodes.playbook_id == stream_id)
        & (db.stream_nodes.tenant_id == tenant_id)
    ).select()
    edge_rows = db(
        (db.stream_edges.playbook_id == stream_id)
        & (db.stream_edges.tenant_id == tenant_id)
    ).select()

    if node_rows:
        nodes = [
            {
                "id": n.node_id,
                "type": n.node_type,
                "category": n.node_category,
                "label": n.label,
                "config": n.config or {},
                "data": {
                    "label": n.label,
                    "category": n.node_category,
                    "nodeType": n.node_type,
                    "config": n.config or {},
                },
            }
            for n in node_rows
        ]
        edges = [
            {
                "id": e.edge_id,
                "source": e.source_node_id,
                "target": e.target_node_id,
                "sourceHandle": e.source_handle,
                "targetHandle": e.target_handle,
            }
            for e in edge_rows
        ]
        return nodes, edges

    latest_version = (
        db(
            (db.stream_versions.playbook_id == stream_id)
            & (db.stream_versions.tenant_id == tenant_id)
        )
        .select(orderby=~db.stream_versions.version_number, limitby=(0, 1))
        .first()
    )
    if not latest_version:
        return [], []
    canvas = latest_version.canvas_json or {}
    nodes = list(canvas.get("nodes") or latest_version.nodes_json or [])
    edges = list(canvas.get("edges") or latest_version.edges_json or [])
    return nodes, edges


def _mint_execution_jwt(
    app_config: dict,
    claims: dict,
    current_user,
    tenant_id: int,
    identity_id: int,
) -> str | None:
    """Mint a short-lived (15m) tenant-scoped JWT for Elder connector actions."""
    secret = app_config.get("JWT_SECRET_KEY") or app_config.get("SECRET_KEY")
    if not secret:
        return None
    import jwt

    now = datetime.now(UTC)
    is_portal = (
        hasattr(current_user, "_portal_user") or claims.get("type") == "portal_user"
    )
    sub = str(claims.get("sub") or getattr(current_user, "id", None) or identity_id)
    payload = {
        "sub": sub,
        "type": "portal_user" if is_portal else "access",
        "iat": now,
        "exp": now + timedelta(minutes=15),
        "tenant": str(tenant_id),
        "identity_id": identity_id,
        "scope": list(claims.get("scope") or []),
        "roles": list(claims.get("roles") or []),
        "iss": app_config.get("JWT_ISSUER", "elder-api"),
        "aud": app_config.get("JWT_AUDIENCE", "elder-api"),
    }
    algorithm = app_config.get("JWT_ALGORITHM", "HS256")
    return jwt.encode(payload, secret, algorithm=algorithm)


# ============================================================================
# Playbook CRUD & Node Catalog
# ============================================================================


@bp.route("/node-catalog", methods=["GET"])
@login_required
@require_scope("streams:read")
async def get_stream_node_catalog():
    """List generic and Elder playbook nodes with live tenant/route availability."""
    app = current_app._get_current_object()
    db = app.db
    tenant_id = _get_tenant_id()
    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    from apps.api.modules.streams.node_catalog import get_node_catalog

    catalog = await run_in_threadpool(lambda: get_node_catalog(app, db, tenant_id))
    return ApiResponse.success(data=catalog)


@bp.route("", methods=["GET"])
@login_required
@require_scope("streams:read")
async def list_streams():
    """List streams with optional filtering and pagination.

    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 20, max: 100)
        - status: Filter by status (draft, active, archived)
        - q: Full-text search query on name/description
        - is_template: Filter templates (true/false)

    Returns:
        200: Paginated list of streams
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Extract pagination params and filters BEFORE threadpool
    pagination = PaginationParams.from_request()
    status_filter = request.args.get("status")
    q = request.args.get("q", "").strip()
    is_template = request.args.get("is_template")

    def list_stms():
        # Build query scoped to tenant
        query = db.stream_playbooks.tenant_id == tenant_id

        # Filter by status if provided
        if status_filter:
            query = query & (db.stream_playbooks.status == status_filter)

        # Filter by template flag if provided
        if is_template is not None:
            is_template_bool = is_template.lower() in ("true", "1", "yes")
            query = query & (db.stream_playbooks.is_template == is_template_bool)

        # Full-text search if q provided
        if q:
            query = query & (
                (db.stream_playbooks.name.ilike(f"%{q}%"))
                | (db.stream_playbooks.description.ilike(f"%{q}%"))
            )

        # Apply visibility filter (owner OR public OR in shares)
        if identity_id:
            query = query & (
                (db.stream_playbooks.owner_identity_id == identity_id)
                | (db.stream_playbooks.is_public == True)  # noqa: E712
                | (
                    db.stream_playbooks.id.belongs(
                        db(
                            db.stream_shares.shared_with_identity_id == identity_id
                        ).select(db.stream_shares.playbook_id)
                    )
                )
            )
        else:
            # Unauthenticated: public only
            query = query & (db.stream_playbooks.is_public == True)  # noqa: E712

        # Count total
        total = db(query).count()

        # Fetch paginated results
        streams = db(query).select(
            orderby=~db.stream_playbooks.updated_at,
            limitby=(
                pagination.offset,
                pagination.offset + pagination.per_page,
            ),
        )

        return [_serialize_stream(db, s) for s in streams], total

    result, total = await run_in_threadpool(list_stms)

    return ApiResponse.success(
        {
            "data": result,
            "total": total,
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total_pages": (total + pagination.per_page - 1) // pagination.per_page,
        }
    )


@bp.route("", methods=["POST"])
@login_required
@require_scope("streams:write")
async def create_stream():
    """Create a new stream.

    Request body:
        {
            "name": "required string",
            "description": "optional string",
            "trigger_type": "optional string",
            "is_template": "optional boolean",
            "tags": "optional array",
            "canvas_data": "optional object"
        }

    Returns:
        201: Created stream object
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id or not identity_id:
        return ApiResponse.error("Tenant or identity not found", 403)

    blocked = await check_limit("object", tenant_id)
    if blocked is not None:
        return blocked

    data = await request.get_json() or {}

    # Validate required fields
    name = data.get("name", "").strip()
    if not name:
        return ApiResponse.error("Stream name is required", 400)

    # Mint the hierarchical village_id in request context (generate_village_id
    # needs current_app.redis_client, unavailable inside run_in_threadpool).
    village_id = generate_village_id(tenant_id, current_app.redis_client)

    def create():
        now = datetime.now(UTC)

        # Create stream playbook record
        stream_id = db.stream_playbooks.insert(
            tenant_id=tenant_id,
            village_id=village_id,
            name=name,
            description=data.get("description") or "",
            owner_identity_id=identity_id,
            created_by_identity_id=identity_id,
            trigger_type=data.get("trigger_type"),
            is_public=data.get("is_public", False),
            is_template=data.get("is_template", False),
            is_enabled=False,  # Start disabled
            tags=data.get("tags") or [],
            status="draft",
            execution_count=0,
            success_count=0,
            failure_count=0,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Create initial version with canvas data
        canvas = data.get("canvas_data") or {"nodes": [], "edges": []}
        db.stream_versions.insert(
            tenant_id=tenant_id,
            playbook_id=stream_id,
            version_number=1,
            created_by_identity_id=identity_id,
            nodes_json=canvas.get("nodes", []),
            edges_json=canvas.get("edges", []),
            canvas_json=canvas,
            change_summary="Initial version",
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Fetch the created stream
        stream = db(db.stream_playbooks.id == stream_id).select().first()
        version = db(db.stream_versions.playbook_id == stream_id).select().first()
        return _serialize_stream(db, stream, version)

    result = await run_in_threadpool(create)
    return ApiResponse.success(data=result, status_code=201)


@bp.route("/<int:stream_id>", methods=["GET"])
@login_required
@require_scope("streams:read")
async def get_stream(stream_id):
    """Get a specific stream by ID.

    Args:
        stream_id: Stream identifier

    Returns:
        200: Stream object with canvas data and metadata
        404: Stream not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def get():
        stream = (
            db(
                (db.stream_playbooks.id == stream_id)
                & (db.stream_playbooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not stream:
            return None, None, None

        # Check access
        if not _can_read_stream(db, stream, tenant_id, identity_id):
            return None, None, None

        # Get latest version with canvas data
        version = (
            db(db.stream_versions.playbook_id == stream.id)
            .select(orderby=~db.stream_versions.version_number, limitby=(0, 1))
            .first()
        )

        # Check if there's an active editor lock
        lock_info = None
        lock = db(db.stream_editor_locks.playbook_id == stream.id).select().first()
        if lock:
            # Check if lock is expired
            if lock.expires_at and lock.expires_at < datetime.now(UTC):
                # Lock expired, remove it
                db(db.stream_editor_locks.playbook_id == stream.id).delete()
                db.commit()
            else:
                lock_info = _serialize_lock(
                    lock, user_is_holder=(lock.locked_by_identity_id == identity_id)
                )

        return stream, version, lock_info

    stream, version, lock_info = await run_in_threadpool(get)

    if not stream:
        return ApiResponse.not_found("Stream")

    result = _serialize_stream(db, stream, version, lock_info)
    return ApiResponse.success(data=result)


@bp.route("/<int:stream_id>", methods=["PUT", "PATCH"])
@login_required
@require_scope("streams:write")
async def update_stream(stream_id):
    """Update a stream.

    Args:
        stream_id: Stream identifier

    Request body:
        {
            "name": "optional string",
            "description": "optional string",
            "trigger_type": "optional string",
            "tags": "optional array",
            "status": "optional string",
            "is_enabled": "optional boolean",
            "is_public": "optional boolean",
            "canvas_data": "optional object (creates new version & syncs nodes/edges)"
        }

    Returns:
        200: Updated stream object
        404: Stream not found
        403: Access denied
    """
    app = current_app._get_current_object()
    db = app.db
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id or not identity_id:
        return ApiResponse.error("Tenant or identity not found", 403)

    data = await request.get_json() or {}

    def update():
        stream = (
            db(
                (db.stream_playbooks.id == stream_id)
                & (db.stream_playbooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not stream:
            return None, None

        # Check edit access
        if not _can_edit_stream(db, stream, tenant_id, identity_id):
            return None, None

        # Check editor lock
        lock = db(db.stream_editor_locks.playbook_id == stream.id).select().first()
        if lock:
            if lock.expires_at and lock.expires_at < datetime.now(UTC):
                db(db.stream_editor_locks.playbook_id == stream.id).delete()
                db.commit()
            elif lock.locked_by_identity_id != identity_id:
                # Another user holds the lock
                return None, _serialize_lock(lock, user_is_holder=False)

        # Update stream metadata
        update_data = {"updated_at": datetime.now(UTC)}
        if "name" in data:
            update_data["name"] = data.get("name", "").strip()
        if "description" in data:
            update_data["description"] = data.get("description") or ""
        if "trigger_type" in data:
            update_data["trigger_type"] = data.get("trigger_type")
        if "is_public" in data:
            update_data["is_public"] = data.get("is_public", False)
        if "is_template" in data:
            update_data["is_template"] = data.get("is_template", False)
        if "tags" in data:
            update_data["tags"] = data.get("tags") or []
        if "status" in data:
            update_data["status"] = data.get("status")
        if "is_enabled" in data:
            update_data["is_enabled"] = data.get("is_enabled", False)

        db(db.stream_playbooks.id == stream_id).update(**update_data)
        db.commit()

        validation_summary = None
        # If canvas_data is provided, create new version & sync normalized nodes/edges
        if "canvas_data" in data:
            # penguin-dal has no field .max(); order desc + take 1
            last_version = (
                db(db.stream_versions.playbook_id == stream_id)
                .select(orderby=~db.stream_versions.version_number, limitby=(0, 1))
                .first()
            )
            max_version = last_version.version_number if last_version else 0

            new_version = max_version + 1
            canvas = data.get("canvas_data") or {}
            now = datetime.now(UTC)
            db.stream_versions.insert(
                tenant_id=tenant_id,
                playbook_id=stream_id,
                version_number=new_version,
                created_by_identity_id=identity_id,
                nodes_json=canvas.get("nodes", []),
                edges_json=canvas.get("edges", []),
                canvas_json=canvas,
                change_summary="Updated",
                created_at=now,
                updated_at=now,
            )
            _sync_stream_nodes_edges(db, tenant_id, stream_id, canvas)
            db.commit()

            from apps.api.modules.streams.node_catalog import validate_playbook_nodes

            validation_summary = validate_playbook_nodes(
                app,
                db,
                tenant_id,
                canvas.get("nodes", []),
                canvas.get("edges", []),
            )

        # Fetch updated stream with latest version
        stream = db(db.stream_playbooks.id == stream_id).select().first()
        version = (
            db(db.stream_versions.playbook_id == stream_id)
            .select(orderby=~db.stream_versions.version_number, limitby=(0, 1))
            .first()
        )
        serialized = _serialize_stream(db, stream, version)
        if validation_summary is not None:
            serialized["validation"] = validation_summary
        return serialized, None

    result, lock_error = await run_in_threadpool(update)

    if result is None:
        if lock_error:
            return ApiResponse.error(
                f"Stream is locked for editing: {lock_error['locked_by_name']}",
                423,
            )
        return ApiResponse.not_found("Stream")

    return ApiResponse.success(data=result)


@bp.route("/<int:stream_id>/validate", methods=["POST"])
@login_required
@require_scope("streams:read")
async def validate_stream(stream_id):
    """Validate playbook graph, node types, module availability, and config."""
    app = current_app._get_current_object()
    db = app.db
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json(silent=True) or {}

    def _validate():
        stream = (
            db(
                (db.stream_playbooks.id == stream_id)
                & (db.stream_playbooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not stream:
            return None, 404
        if not _can_read_stream(db, stream, tenant_id, identity_id):
            return None, 403

        if "canvas_data" in data and isinstance(data["canvas_data"], dict):
            nodes = data["canvas_data"].get("nodes") or []
            edges = data["canvas_data"].get("edges") or []
        elif "nodes" in data:
            nodes = data.get("nodes") or []
            edges = data.get("edges") or []
        else:
            nodes, edges = _load_stream_nodes_edges(db, tenant_id, stream_id)

        from apps.api.modules.streams.node_catalog import validate_playbook_nodes

        return validate_playbook_nodes(app, db, tenant_id, nodes, edges), 200

    result, status_code = await run_in_threadpool(_validate)
    if status_code == 404:
        return ApiResponse.not_found("Stream")
    if status_code == 403:
        return ApiResponse.error("Access denied", 403)
    return ApiResponse.success(data=result, status_code=200)


@bp.route("/<int:stream_id>", methods=["DELETE"])
@login_required
@require_scope("streams:write")
async def delete_stream(stream_id):
    """Delete a stream.

    Args:
        stream_id: Stream identifier

    Returns:
        200: Success response
        404: Stream not found
        403: Access denied
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id or not identity_id:
        return ApiResponse.error("Tenant or identity not found", 403)

    def delete():
        stream = (
            db(
                (db.stream_playbooks.id == stream_id)
                & (db.stream_playbooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not stream:
            return False

        # Only owner can delete
        if stream.owner_identity_id != identity_id:
            return False

        # Delete stream (cascade will handle versions, nodes, edges, etc.)
        db(db.stream_playbooks.id == stream_id).delete()
        db.commit()
        return True

    success = await run_in_threadpool(delete)

    if not success:
        return ApiResponse.not_found("Stream")

    return ApiResponse.success({"message": "Stream deleted successfully"})


@bp.route("/<int:stream_id>/duplicate", methods=["POST"])
@login_required
@require_scope("streams:write")
async def duplicate_stream(stream_id):
    """Duplicate a stream.

    Args:
        stream_id: Stream identifier to duplicate

    Returns:
        201: Created stream object
        404: Stream not found
        403: Access denied
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id or not identity_id:
        return ApiResponse.error("Tenant or identity not found", 403)

    # Mint the hierarchical village_id in request context (see create_stream).
    village_id = generate_village_id(tenant_id, current_app.redis_client)

    def duplicate():
        original = (
            db(
                (db.stream_playbooks.id == stream_id)
                & (db.stream_playbooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not original:
            return None

        # Check access (owner, public, or template)
        has_access = (
            original.owner_identity_id == identity_id
            or original.is_public
            or original.is_template
        )
        if not has_access:
            return None

        # Get latest version
        version = (
            db(db.stream_versions.playbook_id == stream_id)
            .select(orderby=~db.stream_versions.version_number, limitby=(0, 1))
            .first()
        )

        now = datetime.now(UTC)

        # Create duplicate
        new_stream_id = db.stream_playbooks.insert(
            tenant_id=tenant_id,
            village_id=village_id,
            name=f"{original.name} (Copy)",
            description=original.description,
            owner_identity_id=identity_id,
            created_by_identity_id=identity_id,
            trigger_type=original.trigger_type,
            is_public=False,
            is_template=False,
            is_enabled=False,
            tags=original.tags or [],
            status="draft",
            execution_count=0,
            success_count=0,
            failure_count=0,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Copy version
        if version:
            canvas = version.canvas_json or {"nodes": [], "edges": []}
            db.stream_versions.insert(
                tenant_id=tenant_id,
                playbook_id=new_stream_id,
                version_number=1,
                created_by_identity_id=identity_id,
                nodes_json=canvas.get("nodes", []),
                edges_json=canvas.get("edges", []),
                canvas_json=canvas,
                change_summary="Duplicated from stream",
                created_at=now,
                updated_at=now,
            )
            db.commit()

        new_stream = db(db.stream_playbooks.id == new_stream_id).select().first()
        return _serialize_stream(db, new_stream)

    result = await run_in_threadpool(duplicate)

    if not result:
        return ApiResponse.not_found("Stream")

    return ApiResponse.success(data=result, status_code=201)


# ============================================================================
# Editor Locking
# ============================================================================


@bp.route("/<int:stream_id>/lock", methods=["GET"])
@login_required
@require_scope("streams:read")
async def get_lock_status(stream_id):
    """Get the current editor lock status for a stream.

    Args:
        stream_id: Stream identifier

    Returns:
        200: Lock status
        404: Stream not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def get():
        stream = (
            db(
                (db.stream_playbooks.id == stream_id)
                & (db.stream_playbooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not stream:
            return None, None

        lock = db(db.stream_editor_locks.playbook_id == stream.id).select().first()

        if not lock:
            return stream, None

        # Check if lock is expired
        if lock.expires_at and lock.expires_at < datetime.now(UTC):
            db(db.stream_editor_locks.playbook_id == stream.id).delete()
            db.commit()
            return stream, None

        return stream, _serialize_lock(
            lock, user_is_holder=(lock.locked_by_identity_id == identity_id)
        )

    stream, lock_info = await run_in_threadpool(get)

    if not stream:
        return ApiResponse.not_found("Stream")

    return ApiResponse.success(
        data={"locked": lock_info is not None, "lock": lock_info}
    )


@bp.route("/<int:stream_id>/lock", methods=["POST"])
@login_required
@require_scope("streams:write")
async def acquire_lock(stream_id):
    """Acquire an editor lock for a stream.

    Only one user can hold the editor lock at a time.

    Args:
        stream_id: Stream identifier

    Request body:
        {
            "socket_id": "optional WebSocket session ID"
        }

    Returns:
        200/201: Lock info
        403: Access denied
        423: Locked by another user
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id or not identity_id:
        return ApiResponse.error("Tenant or identity not found", 403)

    data = await request.get_json() or {}

    def acquire():
        stream = (
            db(
                (db.stream_playbooks.id == stream_id)
                & (db.stream_playbooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not stream:
            return None, None, 404

        # Check if user has edit access
        if not _can_edit_stream(db, stream, tenant_id, identity_id):
            return None, None, 403

        # Check existing lock
        existing_lock = (
            db(db.stream_editor_locks.playbook_id == stream.id).select().first()
        )

        if existing_lock:
            # Check if expired
            if existing_lock.expires_at and existing_lock.expires_at < datetime.now(
                UTC
            ):
                # Remove expired lock
                db(db.stream_editor_locks.playbook_id == stream.id).delete()
                db.commit()
            elif existing_lock.locked_by_identity_id == identity_id:
                # User already holds the lock, refresh it
                now = datetime.now(UTC)
                expires = now + timedelta(minutes=EDITOR_LOCK_TIMEOUT_MINUTES)
                db(db.stream_editor_locks.id == existing_lock.id).update(
                    locked_at=now,
                    expires_at=expires,
                    socket_id=data.get("socket_id"),
                )
                db.commit()
                existing_lock = (
                    db(db.stream_editor_locks.playbook_id == stream.id).select().first()
                )
                return _serialize_lock(existing_lock, user_is_holder=True), None, 200
            else:
                # Another user holds the lock
                return None, _serialize_lock(existing_lock, user_is_holder=False), 423

        # Create new lock
        now = datetime.now(UTC)
        expires = now + timedelta(minutes=EDITOR_LOCK_TIMEOUT_MINUTES)

        # Get identity name for display
        identity_name = "Unknown"
        identity_row = db(db.identities.id == identity_id).select().first()
        if identity_row:
            identity_name = identity_row.full_name or identity_row.email or "Unknown"

        db.stream_editor_locks.insert(
            tenant_id=tenant_id,
            playbook_id=stream.id,
            locked_by_identity_id=identity_id,
            locked_by_name=identity_name,
            locked_at=now,
            expires_at=expires,
            socket_id=data.get("socket_id"),
            created_at=now,
            updated_at=now,
        )
        db.commit()

        lock = db(db.stream_editor_locks.playbook_id == stream.id).select().first()
        return _serialize_lock(lock, user_is_holder=True), None, 201

    lock_info, error_info, status_code = await run_in_threadpool(acquire)

    if status_code == 404:
        return ApiResponse.not_found("Stream")
    elif status_code == 403:
        return ApiResponse.error("Access denied - no editor permission", 403)
    elif status_code == 423:
        return ApiResponse.error(
            f"Stream is locked by {error_info['locked_by_name']}", 423
        )

    return ApiResponse.success(data={"lock": lock_info}, status_code=status_code)


@bp.route("/<int:stream_id>/lock", methods=["DELETE"])
@login_required
@require_scope("streams:write")
async def release_lock(stream_id):
    """Release an editor lock for a stream.

    Only the lock holder can release the lock.

    Args:
        stream_id: Stream identifier

    Returns:
        200: Success response
        404: Stream not found
        403: Not the lock holder
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id or not identity_id:
        return ApiResponse.error("Tenant or identity not found", 403)

    def release():
        stream = (
            db(
                (db.stream_playbooks.id == stream_id)
                & (db.stream_playbooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not stream:
            return 404

        lock = db(db.stream_editor_locks.playbook_id == stream.id).select().first()

        if not lock:
            return 200

        # Only lock holder can release
        if lock.locked_by_identity_id != identity_id:
            return 403

        db(db.stream_editor_locks.playbook_id == stream.id).delete()
        db.commit()
        return 200

    status_code = await run_in_threadpool(release)

    if status_code == 404:
        return ApiResponse.not_found("Stream")
    elif status_code == 403:
        return ApiResponse.error("Only the lock holder can release the lock", 403)

    return ApiResponse.success({"message": "Lock released successfully"})


# ============================================================================
# Execution
# ============================================================================


@bp.route("/<int:stream_id>/execute", methods=["POST"])
@login_required
@require_scope("streams:execute")
async def execute_stream(stream_id):
    """Manually execute a stream.

    Creates an execution record and queues the task for processing.

    Args:
        stream_id: Stream identifier

    Request body:
        {
            "input_data": "optional object",
            "dry_run": "optional boolean"
        }

    Returns:
        202: Execution queued
        404: Stream not found
        403: Access denied or disabled/unlicensed module
        400: Playbook validation error
    """
    app = current_app._get_current_object()
    db = app.db
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()
    claims = dict(getattr(g, "claims", {}) or {})
    current_user = getattr(g, "current_user", None)

    if not tenant_id or not identity_id:
        return ApiResponse.error("Tenant or identity not found", 403)

    data = await request.get_json() or {}

    def execute():
        stream = (
            db(
                (db.stream_playbooks.id == stream_id)
                & (db.stream_playbooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not stream:
            return None, 404, None, None

        # Check edit access (execute is a write operation)
        if not _can_edit_stream(db, stream, tenant_id, identity_id):
            return None, 403, None, None

        from apps.api.modules.streams.node_catalog import validate_playbook_nodes

        nodes, edges = _load_stream_nodes_edges(db, tenant_id, stream_id)
        validation = validate_playbook_nodes(app, db, tenant_id, nodes, edges)

        if data.get("dry_run"):
            return (
                {
                    "dry_run": True,
                    "valid": validation["valid"],
                    "message": (
                        "Stream validation successful"
                        if validation["valid"]
                        else "Stream validation failed"
                    ),
                    "validation": validation,
                },
                200,
                None,
                None,
            )

        if validation.get("has_disabled_modules"):
            return (
                {
                    "error": (
                        "Playbook uses disabled or unlicensed module(s): "
                        + ", ".join(validation.get("disabled_modules") or [])
                    ),
                    "code": "MODULE_DISABLED",
                    "disabled_modules": validation.get("disabled_modules") or [],
                    "validation": validation,
                },
                403,
                None,
                None,
            )

        if not validation.get("valid", True):
            return (
                {
                    "error": "Playbook validation failed",
                    "code": "VALIDATION_ERROR",
                    "validation": validation,
                },
                400,
                None,
                None,
            )

        # Create execution record
        execution_uuid = str(uuid.uuid4())
        now = datetime.now(UTC)

        exec_id = db.stream_executions.insert(
            tenant_id=tenant_id,
            playbook_id=stream_id,
            execution_id=execution_uuid,
            status="queued",
            trigger_type="manual",
            triggered_by_identity_id=identity_id,
            input_json=data.get("input_data") or {},
            started_at=now,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Update playbook execution stats
        db(db.stream_playbooks.id == stream_id).update(
            execution_count=(stream.execution_count or 0) + 1,
            last_execution_at=now,
        )
        db.commit()

        logger.info(
            f"Execution created: execution_id={execution_uuid}, "
            f"stream_id={stream_id}, triggered_by={identity_id}"
        )

        execution = db(db.stream_executions.id == exec_id).select().first()
        return _serialize_execution(execution), 202, execution_uuid, now

    result, status_code, execution_uuid, enqueue_time = await run_in_threadpool(execute)

    # Phase 4b-Streams-d: Enqueue job to Redis Streams job bus (async)
    # (Enqueue failures do NOT fail the request — row exists for reconciliation)
    if execution_uuid and enqueue_time and status_code == 202:
        try:
            import redis.asyncio

            from apps.worker.config.settings import settings
            from shared.jobbus import JobBus

            if settings.redis_url:
                execution_jwt = _mint_execution_jwt(
                    dict(app.config), claims, current_user, tenant_id, identity_id
                )
                job_payload = {
                    "execution_id": execution_uuid,
                    "playbook_id": stream_id,
                    "tenant_id": tenant_id,
                }
                if execution_jwt:
                    job_payload["auth_token"] = execution_jwt
                redis_client = redis.asyncio.from_url(settings.redis_url)
                jobbus = JobBus(redis_client)
                await jobbus.ensure_group("streams")
                await jobbus.enqueue(
                    "streams",
                    "execute_playbook",
                    job_payload,
                    enqueued_at=enqueue_time.isoformat(),
                    tenant_id=tenant_id,
                    idempotency_key=execution_uuid,
                )
                await redis_client.close()
                logger.info(
                    f"Execution enqueued to job bus: execution_id={execution_uuid}"
                )
            else:
                logger.warning(
                    f"REDIS_URL not configured; execution {execution_uuid} queued in DB but not enqueued to job bus"
                )
        except Exception as e:
            logger.warning(
                f"Failed to enqueue execution {execution_uuid} to job bus; "
                f"worker will pick it up via reconcile: {e}"
            )

    if status_code == 404:
        return ApiResponse.not_found("Stream")
    elif status_code == 403:
        if isinstance(result, dict) and result.get("code") == "MODULE_DISABLED":
            return ApiResponse.error(
                result["error"],
                403,
                code="MODULE_DISABLED",
                disabled_modules=result.get("disabled_modules", []),
                validation=result.get("validation"),
            )
        return ApiResponse.error("Access denied - cannot execute this stream", 403)
    elif status_code == 400:
        err_msg = (
            result.get("error", "Validation failed")
            if isinstance(result, dict)
            else "Validation failed"
        )
        return ApiResponse.error(
            err_msg,
            400,
            code=(
                result.get("code", "VALIDATION_ERROR")
                if isinstance(result, dict)
                else "VALIDATION_ERROR"
            ),
            validation=result.get("validation") if isinstance(result, dict) else None,
        )
    elif status_code == 200:
        return ApiResponse.success(data=result, status_code=200)

    return ApiResponse.success(data=result, status_code=status_code)


@bp.route("/executions", methods=["GET"])
@login_required
@require_scope("streams:read")
async def list_all_executions():
    """List execution history across all streams for the tenant (global view).

    Query params:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 20, max: 100)
        - status: Filter by status
        - stream_id: Optional filter by specific stream

    Returns:
        200: Paginated list of executions across all tenant streams
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    pagination = PaginationParams.from_request()
    status_filter = request.args.get("status")
    stream_filter = request.args.get("stream_id")

    def list_all_execs():
        # Build query scoped to tenant
        query = db.stream_executions.tenant_id == tenant_id

        # Determine which streams this caller can read (public, owned, or shared)
        readable_streams_query = (db.stream_playbooks.tenant_id == tenant_id) & (
            (db.stream_playbooks.is_public == True)
            | (db.stream_playbooks.owner_identity_id == identity_id)
        )
        readable_stream_ids = db(readable_streams_query).select(db.stream_playbooks.id)
        readable_ids_set = {s.id for s in readable_stream_ids}

        # Add shared streams
        if identity_id:
            shared_streams = db(
                db.stream_shares.shared_with_identity_id == identity_id
            ).select(db.stream_shares.playbook_id)
            readable_ids_set.update(s.playbook_id for s in shared_streams)

        # Filter executions to only readable streams
        if readable_ids_set:
            query = query & (db.stream_executions.playbook_id.belongs(readable_ids_set))
        else:
            # No readable streams, return empty
            return [], 0

        # Optional stream filter (must be readable)
        if stream_filter:
            try:
                stream_id = int(stream_filter)
                if stream_id in readable_ids_set:
                    query = query & (db.stream_executions.playbook_id == stream_id)
                else:
                    # Requested stream not readable
                    return [], 0
            except (ValueError, TypeError):
                pass

        # Optional status filter
        if status_filter:
            query = query & (db.stream_executions.status == status_filter)

        # Count total (now access-filtered)
        total = db(query).count()

        # Fetch paginated executions
        executions = db(query).select(
            orderby=~db.stream_executions.started_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        # Pre-fetch all unique streams needed for this page
        stream_ids_in_page = {e.playbook_id for e in executions}
        streams_map = {}
        if stream_ids_in_page:
            streams = db(
                (db.stream_playbooks.id.belongs(stream_ids_in_page))
                & (db.stream_playbooks.tenant_id == tenant_id)
            ).select()
            for stream in streams:
                streams_map[stream.id] = stream

        result = []
        for exec_row in executions:
            stream_row = streams_map.get(exec_row.playbook_id)

            # Check stream access before including execution
            # (execution inherits access from stream)
            if stream_row:
                can_read = _can_read_stream(db, stream_row, tenant_id, identity_id)
                if not can_read:
                    continue

                serialized = _serialize_execution(
                    exec_row,
                    stream_id=stream_row.id,
                    stream_name=stream_row.name,
                )
            else:
                # No stream (orphaned execution) — skip
                continue

            result.append(serialized)

        return result, total

    result, total = await run_in_threadpool(list_all_execs)

    return ApiResponse.success(
        {
            "data": result,
            "total": total,
            "page": pagination.page,
            "per_page": pagination.per_page,
        }
    )


@bp.route("/<int:stream_id>/executions", methods=["GET"])
@login_required
@require_scope("streams:read")
async def list_executions(stream_id):
    """List execution history for a stream.

    Args:
        stream_id: Stream identifier

    Query params:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 20, max: 100)
        - status: Filter by status

    Returns:
        200: Paginated list of executions
        404: Stream not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    pagination = PaginationParams.from_request()
    status_filter = request.args.get("status")

    def list_execs():
        stream = (
            db(
                (db.stream_playbooks.id == stream_id)
                & (db.stream_playbooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not stream:
            return None, None, 0

        # Check access
        if not _can_read_stream(db, stream, tenant_id, identity_id):
            return None, None, 0

        query = (db.stream_executions.playbook_id == stream_id) & (
            db.stream_executions.tenant_id == tenant_id
        )

        if status_filter:
            query = query & (db.stream_executions.status == status_filter)

        total = db(query).count()

        executions = db(query).select(
            orderby=~db.stream_executions.started_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return stream, [_serialize_execution(e) for e in executions], total

    stream, result, total = await run_in_threadpool(list_execs)

    if not stream:
        return ApiResponse.not_found("Stream")

    return ApiResponse.success(
        {
            "data": result,
            "total": total,
            "page": pagination.page,
            "per_page": pagination.per_page,
        }
    )


@bp.route("/<int:stream_id>/executions/<execution_id>", methods=["GET"])
@login_required
@require_scope("streams:read")
async def get_execution(stream_id, execution_id):
    """Get details of a specific execution.

    Args:
        stream_id: Stream identifier
        execution_id: Execution identifier (execution_id field, not DB id)

    Returns:
        200: Execution details with node logs
        404: Stream or execution not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def get():
        stream = (
            db(
                (db.stream_playbooks.id == stream_id)
                & (db.stream_playbooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not stream:
            return None, None, None

        # Check access
        if not _can_read_stream(db, stream, tenant_id, identity_id):
            return None, None, None

        execution = (
            db(
                (db.stream_executions.execution_id == execution_id)
                & (db.stream_executions.playbook_id == stream_id)
                & (db.stream_executions.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not execution:
            return stream, None, None

        # Get node execution logs
        node_logs = db(db.stream_node_executions.execution_id == execution_id).select(
            orderby=db.stream_node_executions.started_at
        )

        node_log_data = [
            {
                "node_id": log.node_id,
                "node_type": log.node_type,
                "status": log.status,
                "input_data": log.input_json,
                "output_data": log.output_json,
                "error_message": log.error_message,
                "started_at": log.started_at.isoformat() if log.started_at else None,
                "completed_at": (
                    log.completed_at.isoformat() if log.completed_at else None
                ),
                "duration_ms": log.duration_ms,
            }
            for log in node_logs
        ]

        return stream, _serialize_execution(execution), node_log_data

    stream, execution_data, node_logs = await run_in_threadpool(get)

    if not stream:
        return ApiResponse.not_found("Stream")

    if not execution_data:
        return ApiResponse.not_found("Execution")

    return ApiResponse.success(
        data={
            "execution": execution_data,
            "node_logs": node_logs or [],
        }
    )
