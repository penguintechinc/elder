"""Document collections (hierarchical folders) endpoints using penguin-dal."""

# flake8: noqa: E501

import logging
from datetime import UTC, datetime, timezone

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool

logger = logging.getLogger(__name__)

bp = Blueprint("document_collections", __name__)


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


def _build_collection_tree(db, rows_by_id: dict) -> list[dict]:
    """Build hierarchical tree from flat collection rows.

    Args:
        db: PyDAL database instance
        rows_by_id: Dictionary of {collection_id: row}

    Returns:
        List of top-level collections with nested children
    """

    def build_node(coll_id):
        row = rows_by_id.get(coll_id)
        if not row:
            return None

        node = {
            "id": row.id,
            "village_id": row.village_id,
            "name": row.name,
            "slug": row.slug,
            "description": row.description,
            "parent_id": row.parent_id,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
            "children": [],
        }

        # Find children
        for cid, coll_row in rows_by_id.items():
            if coll_row.parent_id == coll_id:
                child_node = build_node(cid)
                if child_node:
                    node["children"].append(child_node)

        return node

    # Find root collections (parent_id is None)
    roots = []
    for coll_id, row in rows_by_id.items():
        if row.parent_id is None:
            root_node = build_node(coll_id)
            if root_node:
                roots.append(root_node)

    return roots


@bp.route("", methods=["GET"])
@login_required
@require_scope("documents:read")
async def list_collections():
    """List document collections as a hierarchical tree.

    Returns:
        200: Hierarchical collection tree
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def fetch():
        rows = db(db.doc_collections.tenant_id == tenant_id).select(
            orderby=db.doc_collections.name
        )
        rows_dict = {r.id: r for r in rows}
        return rows_dict

    rows_dict = await run_in_threadpool(fetch)

    # Build tree
    tree = _build_collection_tree(db, rows_dict)

    return jsonify({"collections": tree}), 200


@bp.route("", methods=["POST"])
@login_required
@require_scope("documents:write")
async def create_collection():
    """Create a new collection.

    Requires: documents:write scope

    Request body:
        {
            "name": "string (required)",
            "slug": "string (optional, auto-generated if not provided)",
            "description": "string (optional)",
            "parent_id": "int (optional, parent collection ID)"
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

    # Get parent_id if specified
    parent_id = data.get("parent_id")

    def create():
        from apps.api.modules.documents.common import slugify
        from shared.utils.village_id import generate_village_id

        # Validate parent exists and belongs to tenant
        if parent_id:
            parent = (
                db(
                    (db.doc_collections.id == parent_id)
                    & (db.doc_collections.tenant_id == tenant_id)
                )
                .select()
                .first()
            )
            if not parent:
                return "parent_not_found"

        now = datetime.now(UTC)
        redis_client = current_app.redis_client
        village_id = generate_village_id(tenant_id, redis_client)

        # Generate or use provided slug
        slug = data.get("slug") or slugify(name)

        # Insert collection
        coll_id = db.doc_collections.insert(
            tenant_id=tenant_id,
            village_id=village_id,
            name=name,
            slug=slug,
            description=data.get("description"),
            parent_id=parent_id,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        return db(db.doc_collections.id == coll_id).select().first()

    result = await run_in_threadpool(create)

    if isinstance(result, str):
        if result == "parent_not_found":
            return ApiResponse.error("Parent collection not found", 404)
        else:
            return ApiResponse.error(result, 400)

    coll = result

    return (
        jsonify(
            {
                "id": coll.id,
                "village_id": coll.village_id,
                "name": coll.name,
                "slug": coll.slug,
                "description": coll.description,
                "parent_id": coll.parent_id,
                "created_at": coll.created_at.isoformat(),
                "updated_at": coll.updated_at.isoformat(),
            }
        ),
        201,
    )


@bp.route("/<int:coll_id>", methods=["GET"])
@login_required
@require_scope("documents:read")
async def get_collection(coll_id):
    """Get a single collection by ID.

    Returns:
        200: Collection details
        404: Collection not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def fetch():
        return (
            db(
                (db.doc_collections.id == coll_id)
                & (db.doc_collections.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

    coll = await run_in_threadpool(fetch)

    if not coll:
        return ApiResponse.not_found("Collection")

    return jsonify(
        {
            "id": coll.id,
            "village_id": coll.village_id,
            "name": coll.name,
            "slug": coll.slug,
            "description": coll.description,
            "parent_id": coll.parent_id,
            "created_at": coll.created_at.isoformat(),
            "updated_at": coll.updated_at.isoformat(),
        }
    )


@bp.route("/<int:coll_id>", methods=["PATCH"])
@login_required
@require_scope("documents:write")
async def update_collection(coll_id):
    """Update a collection.

    Requires: documents:write scope

    Returns:
        200: Updated collection
        404: Collection not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}

    def update():
        coll = (
            db(
                (db.doc_collections.id == coll_id)
                & (db.doc_collections.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not coll:
            return None

        now = datetime.now(UTC)
        updates = {"updated_at": now}

        if "name" in data:
            updates["name"] = data["name"]

        if "slug" in data:
            updates["slug"] = data["slug"]

        if "description" in data:
            updates["description"] = data["description"]

        if "parent_id" in data:
            new_parent_id = data["parent_id"]
            # Validate parent exists if provided
            if new_parent_id:
                parent = (
                    db(
                        (db.doc_collections.id == new_parent_id)
                        & (db.doc_collections.tenant_id == tenant_id)
                    )
                    .select()
                    .first()
                )
                if not parent:
                    return "parent_not_found"
            updates["parent_id"] = new_parent_id

        db(db.doc_collections.id == coll_id).update(**updates)
        db.commit()

        return db(db.doc_collections.id == coll_id).select().first()

    result = await run_in_threadpool(update)

    if isinstance(result, str):
        if result == "parent_not_found":
            return ApiResponse.error("Parent collection not found", 404)
        else:
            return ApiResponse.error(result, 400)

    if not result:
        return ApiResponse.not_found("Collection")

    coll = result

    return jsonify(
        {
            "id": coll.id,
            "village_id": coll.village_id,
            "name": coll.name,
            "slug": coll.slug,
            "description": coll.description,
            "parent_id": coll.parent_id,
            "created_at": coll.created_at.isoformat(),
            "updated_at": coll.updated_at.isoformat(),
        }
    )


@bp.route("/<int:coll_id>", methods=["DELETE"])
@login_required
@require_scope("documents:write")
async def delete_collection(coll_id):
    """Delete a collection.

    Requires: documents:admin scope

    Returns:
        204: Collection deleted
        400: Collection is not empty
        404: Collection not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def delete():
        coll = (
            db(
                (db.doc_collections.id == coll_id)
                & (db.doc_collections.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not coll:
            return None

        # Check if collection has documents
        doc_count = db(db.doc_document_collections.doc_collection_id == coll_id).count()
        if doc_count > 0:
            return "collection_not_empty"

        # Check if collection has children
        child_count = db(db.doc_collections.parent_id == coll_id).count()
        if child_count > 0:
            return "collection_has_children"

        # Delete collection
        db(db.doc_collections.id == coll_id).delete()
        db.commit()
        return True

    result = await run_in_threadpool(delete)

    if isinstance(result, str):
        if result == "collection_not_empty":
            return ApiResponse.error(
                "Collection contains documents; delete them first", 400
            )
        elif result == "collection_has_children":
            return ApiResponse.error(
                "Collection has child collections; delete them first", 400
            )
        else:
            return ApiResponse.error(result, 400)

    if result is None:
        return ApiResponse.not_found("Collection")

    return "", 204


# Collection document attachment endpoints
@bp.route("/<int:coll_id>/documents/<int:doc_id>", methods=["POST"])
@login_required
@require_scope("documents:write")
async def attach_document_to_collection(coll_id, doc_id):
    """Attach a document to a collection.

    Requires: documents:write scope

    Returns:
        200: Attachment successful
        404: Collection or document not found
        409: Document already in collection
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def attach():
        # Verify collection exists and belongs to tenant
        coll = (
            db(
                (db.doc_collections.id == coll_id)
                & (db.doc_collections.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not coll:
            return ("collection_not_found", None)

        # Verify document exists and belongs to tenant
        doc = (
            db(
                (db.doc_documents.id == doc_id)
                & (db.doc_documents.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not doc:
            return ("document_not_found", None)

        # Check if already attached
        existing = (
            db(
                (db.doc_document_collections.doc_document_id == doc_id)
                & (db.doc_document_collections.doc_collection_id == coll_id)
            )
            .select()
            .first()
        )

        if existing:
            return ("already_attached", None)

        # Attach
        db.doc_document_collections.insert(
            doc_document_id=doc_id,
            doc_collection_id=coll_id,
        )
        db.commit()

        return (None, True)

    error, success = await run_in_threadpool(attach)

    if error == "collection_not_found":
        return ApiResponse.error("Collection not found", 404)
    elif error == "document_not_found":
        return ApiResponse.error("Document not found", 404)
    elif error == "already_attached":
        return ApiResponse.error("Document already attached to collection", 409)

    return jsonify({"attached": True}), 200


@bp.route("/<int:coll_id>/documents/<int:doc_id>", methods=["DELETE"])
@login_required
@require_scope("documents:write")
async def detach_document_from_collection(coll_id, doc_id):
    """Detach a document from a collection.

    Requires: documents:write scope

    Returns:
        204: Detachment successful
        404: Document not attached to collection
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def detach():
        # Verify collection exists and belongs to tenant
        coll = (
            db(
                (db.doc_collections.id == coll_id)
                & (db.doc_collections.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not coll:
            return False

        # Check if attachment exists
        existing = (
            db(
                (db.doc_document_collections.doc_document_id == doc_id)
                & (db.doc_document_collections.doc_collection_id == coll_id)
            )
            .select()
            .first()
        )

        if not existing:
            return False

        # Detach
        db(
            (db.doc_document_collections.doc_document_id == doc_id)
            & (db.doc_document_collections.doc_collection_id == coll_id)
        ).delete()
        db.commit()

        return True

    success = await run_in_threadpool(detach)

    if not success:
        return ApiResponse.not_found("Attachment")

    return "", 204
