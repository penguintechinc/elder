"""Diagram collections endpoints using penguin-dal."""

# flake8: noqa: E501

import logging
import secrets
from datetime import datetime, timezone

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams
from shared.utils.village_id import generate_village_id

from .diagrams import _can_read_diagram, _get_tenant_id

logger = logging.getLogger(__name__)

bp = Blueprint("diagram_collections", __name__)


def _can_read_collection(db, collection_row, tenant_id, identity_id=None) -> bool:
    """Check if current user can read this collection based on visibility.

    Args:
        db: PyDAL database instance
        collection_row: Collection row from database
        tenant_id: Current tenant ID
        identity_id: Current identity ID (optional, from token)

    Returns:
        True if user can read, False otherwise
    """
    # Cross-tenant isolation: collection must belong to this tenant
    if collection_row.tenant_id != tenant_id:
        return False

    # Public collections visible to everyone
    if collection_row.is_public:
        return True

    # Unauthenticated users can't read anything else
    if identity_id is None:
        return False

    # Owner can always read their own collection
    if collection_row.owner_identity_id == identity_id:
        return True

    # Check shared access: query dg_collection_shares table
    share_row = (
        db(
            (db.dg_collection_shares.collection_id == collection_row.id)
            & (db.dg_collection_shares.shared_with_identity_id == identity_id)
        )
        .select()
        .first()
    )
    if share_row:
        return True

    return False


@bp.route("", methods=["GET"])
@login_required
@require_scope("diagrams:read")
async def list_collections():
    """List diagram collections with optional filtering and pagination.

    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 20, max: 100)

    Returns:
        200: Paginated list of collections
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    pagination = PaginationParams.from_request()

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")

    def fetch():
        # Build base query scoped to tenant
        query = db.dg_collections.tenant_id == tenant_id

        total = db(query).count()
        rows = db(query).select(
            orderby=~db.dg_collections.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return total, rows

    total, rows = await run_in_threadpool(fetch)

    # Post-filter by visibility
    collections = []
    for r in rows:
        if _can_read_collection(db, r, tenant_id, identity_id):
            collections.append(
                {
                    "id": r.id,
                    "village_id": r.village_id,
                    "name": r.name,
                    "thumbnail_url": r.thumbnail_url,
                    "is_public": r.is_public,
                    "share_mode": r.share_mode,
                    "owner_identity_id": r.owner_identity_id,
                    "created_at": r.created_at.isoformat(),
                    "updated_at": r.updated_at.isoformat(),
                }
            )

    return (
        jsonify(
            {
                "items": collections,
                "pagination": {
                    "page": pagination.page,
                    "per_page": pagination.per_page,
                    "total": total,
                    "pages": (total + pagination.per_page - 1) // pagination.per_page,
                },
            }
        ),
        200,
    )


@bp.route("", methods=["POST"])
@login_required
@require_scope("diagrams:write")
async def create_collection():
    """Create a new collection.

    Requires: diagrams:write scope

    Request body:
        {
            "name": "string (required)",
            "thumbnail_url": "optional url",
            "is_public": false (default),
            "share_mode": "private|restricted|public (default: private)"
        }

    Returns:
        201: Created collection
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}

    # Validate required fields
    name = data.get("name", "").strip()
    if not name:
        return ApiResponse.validation_error("name", "is required")

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")
    if not identity_id:
        return ApiResponse.error("Identity not found in token", 403)

    # Mint village_id in request context (must be done before threadpool)
    redis_client = current_app.redis_client
    village_id = generate_village_id(tenant_id, redis_client)

    def create():
        now = datetime.now(timezone.utc)

        collection_id = db.dg_collections.insert(
            tenant_id=tenant_id,
            village_id=village_id,
            name=name,
            owner_identity_id=identity_id,
            thumbnail_url=data.get("thumbnail_url"),
            is_public=data.get("is_public", False),
            share_mode=data.get("share_mode", "private"),
            share_token=None,  # Generated on-demand if made public
            created_at=now,
            updated_at=now,
        )
        db.commit()

        return db(db.dg_collections.id == collection_id).select().first()

    result = await run_in_threadpool(create)

    if not result:
        return ApiResponse.error("Failed to create collection", 500)

    coll = result

    return (
        jsonify(
            {
                "id": coll.id,
                "village_id": coll.village_id,
                "name": coll.name,
                "thumbnail_url": coll.thumbnail_url,
                "is_public": coll.is_public,
                "share_mode": coll.share_mode,
                "owner_identity_id": coll.owner_identity_id,
                "created_at": coll.created_at.isoformat(),
                "updated_at": coll.updated_at.isoformat(),
            }
        ),
        201,
    )


@bp.route("/<int:collection_id>", methods=["GET"])
@login_required
@require_scope("diagrams:read")
async def get_collection(collection_id):
    """Get a collection by ID with its items.

    Returns:
        200: Collection details with items
        404: Collection not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")

    def fetch():
        coll = (
            db(
                (db.dg_collections.id == collection_id)
                & (db.dg_collections.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not coll:
            return (None, [])

        # Fetch items in order
        items = db(db.dg_collection_items.collection_id == collection_id).select(
            orderby=db.dg_collection_items.order_index
        )

        return (coll, items)

    coll, items = await run_in_threadpool(fetch)

    if coll is None:
        return ApiResponse.not_found("Collection")

    if not _can_read_collection(db, coll, tenant_id, identity_id):
        return ApiResponse.not_found("Collection")

    # Enrich items with diagram data
    items_data = []
    for item in items:
        diagram = db(db.dg_diagrams.id == item.diagram_id).select().first()
        if diagram:
            items_data.append(
                {
                    "id": item.id,
                    "diagram_id": diagram.id,
                    "diagram_title": diagram.title,
                    "diagram_thumbnail_url": diagram.thumbnail_url,
                    "order_index": item.order_index,
                }
            )

    return jsonify(
        {
            "id": coll.id,
            "village_id": coll.village_id,
            "name": coll.name,
            "thumbnail_url": coll.thumbnail_url,
            "is_public": coll.is_public,
            "share_mode": coll.share_mode,
            "owner_identity_id": coll.owner_identity_id,
            "items": items_data,
            "created_at": coll.created_at.isoformat(),
            "updated_at": coll.updated_at.isoformat(),
        }
    )


@bp.route("/<int:collection_id>", methods=["PATCH"])
@login_required
@require_scope("diagrams:write")
async def update_collection(collection_id):
    """Update a collection.

    Requires: diagrams:write scope (owner-only)

    Request body can include:
        {
            "name": "new name",
            "thumbnail_url": "url",
            "is_public": true/false,
            "share_mode": "private|restricted|public"
        }

    Returns:
        200: Updated collection
        404: Collection not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")

    def update():
        coll = (
            db(
                (db.dg_collections.id == collection_id)
                & (db.dg_collections.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not coll:
            return None

        # Owner-only precondition
        if coll.owner_identity_id != identity_id:
            return None

        now = datetime.now(timezone.utc)
        updates = {"updated_at": now}

        if "name" in data:
            updates["name"] = data["name"]
        if "thumbnail_url" in data:
            updates["thumbnail_url"] = data["thumbnail_url"]
        if "is_public" in data:
            updates["is_public"] = data["is_public"]
        if "share_mode" in data:
            updates["share_mode"] = data["share_mode"]

        db(db.dg_collections.id == collection_id).update(**updates)
        db.commit()

        return db(db.dg_collections.id == collection_id).select().first()

    coll = await run_in_threadpool(update)

    if coll is None:
        return ApiResponse.not_found("Collection")

    return jsonify(
        {
            "id": coll.id,
            "village_id": coll.village_id,
            "name": coll.name,
            "thumbnail_url": coll.thumbnail_url,
            "is_public": coll.is_public,
            "share_mode": coll.share_mode,
            "owner_identity_id": coll.owner_identity_id,
            "updated_at": coll.updated_at.isoformat(),
        }
    )


@bp.route("/<int:collection_id>", methods=["DELETE"])
@login_required
@require_scope("diagrams:write")
async def delete_collection(collection_id):
    """Delete a collection and its items.

    Requires: diagrams:write scope (owner-only)

    Returns:
        204: Collection deleted
        404: Collection not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")

    def delete():
        coll = (
            db(
                (db.dg_collections.id == collection_id)
                & (db.dg_collections.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not coll:
            return False

        # Owner-only precondition
        if coll.owner_identity_id != identity_id:
            return False

        # Cascade delete items
        db(db.dg_collection_items.collection_id == collection_id).delete()

        # Delete collection
        db(db.dg_collections.id == collection_id).delete()
        db.commit()
        return True

    success = await run_in_threadpool(delete)

    if not success:
        return ApiResponse.not_found("Collection")

    return "", 204


@bp.route("/<int:collection_id>/items", methods=["POST"])
@login_required
@require_scope("diagrams:write")
async def add_item(collection_id):
    """Add a diagram to a collection.

    Requires: diagrams:write scope

    Request body:
        {
            "diagram_id": "int (required)"
        }

    Returns:
        201: Item added
        404: Collection or diagram not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}
    diagram_id = data.get("diagram_id")

    if diagram_id is None:
        return ApiResponse.validation_error("diagram_id", "is required")

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")

    def add():
        # Verify collection exists and caller is owner
        coll = (
            db(
                (db.dg_collections.id == collection_id)
                & (db.dg_collections.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not coll:
            return ("collection_not_found", None)

        if coll.owner_identity_id != identity_id:
            return ("not_owner", None)

        # Verify diagram exists and belongs to tenant and is readable
        diagram = (
            db(
                (db.dg_diagrams.id == diagram_id)
                & (db.dg_diagrams.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not diagram:
            return ("diagram_not_found", None)

        if not _can_read_diagram(db, diagram, tenant_id, identity_id):
            return ("diagram_not_readable", None)

        # Check if already in collection
        existing = (
            db(
                (db.dg_collection_items.collection_id == collection_id)
                & (db.dg_collection_items.diagram_id == diagram_id)
            )
            .select()
            .first()
        )

        if existing:
            return ("already_in_collection", None)

        # Get max order_index
        max_item = (
            db(db.dg_collection_items.collection_id == collection_id)
            .select(orderby=~db.dg_collection_items.order_index, limitby=(0, 1))
            .first()
        )
        next_order = (max_item.order_index + 1) if max_item else 0

        now = datetime.now(timezone.utc)

        item_id = db.dg_collection_items.insert(
            tenant_id=tenant_id,
            collection_id=collection_id,
            diagram_id=diagram_id,
            added_by_identity_id=identity_id,
            order_index=next_order,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        return (None, db(db.dg_collection_items.id == item_id).select().first())

    error, item = await run_in_threadpool(add)

    if error == "collection_not_found":
        return ApiResponse.not_found("Collection")
    elif error == "diagram_not_found":
        return ApiResponse.not_found("Diagram")
    elif error == "not_owner":
        return ApiResponse.not_found("Collection")
    elif error == "diagram_not_readable":
        return ApiResponse.not_found("Diagram")
    elif error == "already_in_collection":
        return ApiResponse.error("Diagram already in collection", 409)

    return (
        jsonify(
            {
                "id": item.id,
                "collection_id": item.collection_id,
                "diagram_id": item.diagram_id,
                "order_index": item.order_index,
            }
        ),
        201,
    )


@bp.route("/<int:collection_id>/items/<int:diagram_id>", methods=["DELETE"])
@login_required
@require_scope("diagrams:write")
async def remove_item(collection_id, diagram_id):
    """Remove a diagram from a collection.

    Requires: diagrams:write scope (owner-only)

    Returns:
        204: Item removed
        404: Item not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")

    def remove():
        # Verify collection and caller is owner
        coll = (
            db(
                (db.dg_collections.id == collection_id)
                & (db.dg_collections.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not coll:
            return False

        if coll.owner_identity_id != identity_id:
            return False

        # Check if item exists
        item = (
            db(
                (db.dg_collection_items.collection_id == collection_id)
                & (db.dg_collection_items.diagram_id == diagram_id)
            )
            .select()
            .first()
        )

        if not item:
            return False

        # Remove item
        db(
            (db.dg_collection_items.collection_id == collection_id)
            & (db.dg_collection_items.diagram_id == diagram_id)
        ).delete()
        db.commit()
        return True

    success = await run_in_threadpool(remove)

    if not success:
        return ApiResponse.not_found("Item")

    return "", 204
