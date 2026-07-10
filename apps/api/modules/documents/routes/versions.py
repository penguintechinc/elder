"""Document versions (history and restore) endpoints using penguin-dal."""

# flake8: noqa: E501

import logging
from datetime import datetime, timezone

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.modules.documents.routes.documents import _can_read_document
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams

logger = logging.getLogger(__name__)

bp = Blueprint("document_versions", __name__)


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


@bp.route("/<int:doc_id>/versions", methods=["GET"])
@login_required
@require_scope("documents:read")
async def list_versions(doc_id):
    """List all versions of a document.

    Requires: documents:read scope

    Returns:
        200: Paginated list of versions
        404: Document not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Extract pagination params
    pagination = PaginationParams.from_request()

    def fetch():
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
            return (None, 0, [])

        # Fetch versions
        total = db(db.doc_versions.doc_document_id == doc_id).count()
        rows = db(db.doc_versions.doc_document_id == doc_id).select(
            orderby=~db.doc_versions.version_number,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return (doc, total, rows)

    doc, total, rows = await run_in_threadpool(fetch)

    if doc is None:
        return ApiResponse.not_found("Document")

    claims = getattr(g, "claims", {}) or {}
    if not _can_read_document(
        db, doc, tenant_id, claims.get("identity_id"), claims.get("roles") or []
    ):
        return ApiResponse.not_found("Document")

    versions = [
        {
            "id": r.id,
            "version_number": r.version_number,
            "title": r.title,
            "author_identity_id": r.author_identity_id,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]

    return (
        jsonify(
            {
                "doc_id": doc_id,
                "versions": versions,
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


@bp.route("/<int:doc_id>/versions/<int:version_number>", methods=["GET"])
@login_required
@require_scope("documents:read")
async def get_version(doc_id, version_number):
    """Get a specific version of a document.

    Requires: documents:read scope

    Returns:
        200: Version details
        404: Document or version not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def fetch():
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
            return (None, None)

        # Fetch version
        version = (
            db(
                (db.doc_versions.doc_document_id == doc_id)
                & (db.doc_versions.version_number == version_number)
            )
            .select()
            .first()
        )

        return (doc, version)

    doc, version = await run_in_threadpool(fetch)

    if doc is None:
        return ApiResponse.not_found("Document")

    claims = getattr(g, "claims", {}) or {}
    if not _can_read_document(
        db, doc, tenant_id, claims.get("identity_id"), claims.get("roles") or []
    ):
        return ApiResponse.not_found("Document")

    if version is None:
        return ApiResponse.not_found("Version")

    return jsonify(
        {
            "id": version.id,
            "doc_id": doc_id,
            "version_number": version.version_number,
            "title": version.title,
            "body_html": version.body_html,
            "body_text": version.body_text,
            "body_markdown": version.body_markdown,
            "author_identity_id": version.author_identity_id,
            "created_at": version.created_at.isoformat(),
        }
    )


@bp.route("/<int:doc_id>/versions/<int:version_number>/restore", methods=["POST"])
@login_required
@require_scope("documents:write")
async def restore_version(doc_id, version_number):
    """Restore a document from a specific version.

    Creates a new version from the snapshot and updates the live document.
    The old version snapshot is never modified (immutable history).

    Requires: documents:write scope

    Returns:
        200: Restored document (new version)
        404: Document or version not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Get current identity from token
    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")
    if not identity_id:
        return ApiResponse.error("Identity not found in token", 403)

    # Authorization: caller must be able to read the parent document before mutating it.
    pre_doc = await run_in_threadpool(
        lambda: db(
            (db.doc_documents.id == doc_id) & (db.doc_documents.tenant_id == tenant_id)
        )
        .select()
        .first()
    )
    if pre_doc is None:
        return ApiResponse.not_found("Document")
    if not _can_read_document(
        db, pre_doc, tenant_id, identity_id, claims.get("roles") or []
    ):
        return ApiResponse.not_found("Document")

    def restore():
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
            return (None, None, None)

        # Fetch source version
        source_version = (
            db(
                (db.doc_versions.doc_document_id == doc_id)
                & (db.doc_versions.version_number == version_number)
            )
            .select()
            .first()
        )
        if not source_version:
            return (None, None, None)

        now = datetime.now(timezone.utc)

        # Create a new version from current state (before overwriting)
        new_version_number = db(db.doc_versions.doc_document_id == doc_id).count() + 1
        db.doc_versions.insert(
            doc_document_id=doc_id,
            version_number=new_version_number,
            title=doc.title,
            body_html=doc.body_html,
            body_text=doc.body_text,
            body_markdown=doc.body_markdown,
            author_identity_id=identity_id,
            created_at=now,
        )

        # Update the live document with content from source version
        db(db.doc_documents.id == doc_id).update(
            title=source_version.title,
            body_html=source_version.body_html,
            body_text=source_version.body_text,
            body_markdown=source_version.body_markdown,
            updated_at=now,
        )
        db.commit()

        # Fetch the updated document
        updated_doc = db(db.doc_documents.id == doc_id).select().first()

        return (updated_doc, new_version_number, source_version)

    doc, new_version_num, source_version = await run_in_threadpool(restore)

    if doc is None:
        return ApiResponse.not_found("Document")

    if source_version is None:
        return ApiResponse.not_found("Version")

    return jsonify(
        {
            "id": doc.id,
            "title": doc.title,
            "slug": doc.slug,
            "status": doc.status,
            "restored_from_version": version_number,
            "new_version_number": new_version_num,
            "updated_at": doc.updated_at.isoformat(),
        }
    )
