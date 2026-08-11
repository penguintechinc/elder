"""Pages module CRUD and workflow endpoints using penguin-dal."""

# flake8: noqa: E501

import logging
import re
from datetime import UTC, datetime, timezone

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.common.html_sanitize import sanitize_html
from apps.api.common.refs.service import backlinks_for
from apps.api.common.refs.wikilinks import rebuild_references_from_text
from apps.api.logging_config import log_error_and_respond
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams, commit_db
from shared.utils.village_id import generate_village_id

logger = logging.getLogger(__name__)

bp = Blueprint("pages", __name__)


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


def _get_user_id() -> int:
    """Extract user identity_id from g.claims."""
    claims = getattr(g, "claims", {}) or {}
    user_id = claims.get("user_identity_id")
    if user_id:
        try:
            return int(user_id)
        except (ValueError, TypeError):
            pass
    return None


def _get_user_roles() -> list:
    """Extract the caller's roles from g.claims (for role-based visibility)."""
    claims = getattr(g, "claims", {}) or {}
    return claims.get("roles") or []


def _can_read_page(page, user_id, user_roles) -> bool:
    """Visibility ACL for a page row. Tenant scoping is enforced by the query.

    Fails closed: unknown visibility, or 'roles'/'users' with no allow-list, deny.
    """
    if page.is_public or page.visibility == "public":
        return True
    # Everything below requires authentication.
    if user_id is None:
        return False
    if page.visibility == "authenticated":
        return True
    if page.visibility == "roles":
        allowed = set(page.visibility_roles or [])
        return bool(allowed and (set(user_roles or []) & allowed))
    if page.visibility == "users":
        allowed_users = page.visibility_users
        if isinstance(allowed_users, str):
            try:
                import json

                allowed_users = json.loads(allowed_users)
            except Exception:
                return False
        return bool(allowed_users and user_id in allowed_users)
    return False


def _slugify(title: str) -> str:
    """Convert title to URL-safe slug."""
    slug = title.lower().strip()
    slug = re.sub(r"[^a-z0-9\-_/]", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")
    return slug


def _sanitize_html(html: str) -> str:
    """Sanitize page body HTML via the shared bleach allow-list sanitizer."""
    return sanitize_html(html)


@bp.route("", methods=["GET"])
@login_required
@require_scope("pages:read")
async def list_pages():
    """
    List pages with optional filtering, visibility ACL, and pagination.

    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 20, max: 100)
        - status: Filter by status (draft, published, archived)
        - visibility: Filter by visibility (public, authenticated, roles, users)

    Returns:
        200: Paginated list of pages (visibility-filtered)
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    user_id = _get_user_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    pagination = PaginationParams.from_request()

    # Build query
    query = db.pg_pages.tenant_id == tenant_id

    # Apply filters
    if request.args.get("status"):
        status_filter = request.args.get("status")
        query &= db.pg_pages.status == status_filter

    if request.args.get("visibility"):
        visibility_filter = request.args.get("visibility")
        query &= db.pg_pages.visibility == visibility_filter

    def get_pages():
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.pg_pages.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )
        return total, rows

    total, rows = await run_in_threadpool(get_pages)

    # Post-filter visibility ACL
    user_roles = _get_user_roles()
    visible_rows = [r for r in rows if _can_read_page(r, user_id, user_roles)]

    pages = [
        {
            "id": r.id,
            "village_id": r.village_id,
            "title": r.title,
            "slug": r.slug,
            "status": r.status,
            "visibility": r.visibility,
            "is_public": r.is_public,
            "author_id": r.author_identity_id,
            "published_at": r.published_at.isoformat() if r.published_at else None,
            "created_at": r.created_at.isoformat(),
            "updated_at": r.updated_at.isoformat(),
        }
        for r in visible_rows
    ]

    return (
        jsonify(
            {
                "items": pages,
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
@require_scope("pages:write")
async def create_page():
    """
    Create a new page.

    Request body:
        {
            "title": "string (required)",
            "body_html": "string (required, will be sanitized)",
            "status": "draft|published (default: draft)",
            "visibility": "public|authenticated|roles|users (default: authenticated)",
            "visibility_roles": ["role1", "role2"] (optional, for 'roles' visibility),
            "visibility_users": [123, 456] (optional, for 'users' visibility),
            "is_public": boolean (default: false),
        }

    Returns:
        201: Created page
        400: Validation error
        409: Slug conflict (per-tenant)
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    user_id = _get_user_id()

    if not tenant_id or not user_id:
        return ApiResponse.error("Tenant or user not found", 403)

    try:
        data = await request.get_json()
    except Exception:
        return ApiResponse.error("Invalid JSON", 400)

    title = data.get("title", "").strip()
    body_html = data.get("body_html", "").strip()

    if not title or not body_html:
        return ApiResponse.error("title and body_html required", 400)

    status = data.get("status", "draft")
    visibility = data.get("visibility", "authenticated")
    is_public = data.get("is_public", False)
    visibility_roles = data.get("visibility_roles")
    visibility_users = data.get("visibility_users")

    # Sanitize body_html
    body_html = _sanitize_html(body_html)

    # Slugify title
    slug = _slugify(title)

    if not slug:
        return ApiResponse.error("title does not produce a valid slug", 400)

    # Mint the village_id in request context (needs current_app.redis_client).
    village_id = generate_village_id(tenant_id, current_app.redis_client)

    # Check per-tenant slug uniqueness
    def create_and_rebuild():
        existing = (
            db((db.pg_pages.tenant_id == tenant_id) & (db.pg_pages.slug == slug))
            .select()
            .first()
        )

        if existing:
            return None, "slug_conflict"

        # Insert page
        page_id = db.pg_pages.insert(
            tenant_id=tenant_id,
            village_id=village_id,
            title=title,
            slug=slug,
            body_html=body_html,
            status=status,
            visibility=visibility,
            visibility_roles=visibility_roles,
            visibility_users=visibility_users,
            is_public=is_public,
            author_identity_id=user_id,
            published_at=datetime.now(UTC) if status == "published" else None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        # Rebuild references from wiki-links
        rebuild_references_from_text(
            db,
            tenant_id=tenant_id,
            source_module="pages",
            source_type="page",
            source_id=slug,
            text=body_html,
        )

        commit_db(db)

        # Retrieve inserted row
        page = db(db.pg_pages.id == page_id).select().first()
        return page, None

    page, error = await run_in_threadpool(create_and_rebuild)

    if error == "slug_conflict":
        return ApiResponse.error(f"Slug '{slug}' already exists in this tenant", 409)

    if not page:
        return ApiResponse.error("Failed to create page", 500)

    return (
        jsonify(
            {
                "id": page.id,
                "village_id": page.village_id,
                "title": page.title,
                "slug": page.slug,
                "status": page.status,
                "visibility": page.visibility,
                "is_public": page.is_public,
                "author_id": page.author_identity_id,
                "published_at": (
                    page.published_at.isoformat() if page.published_at else None
                ),
                "created_at": page.created_at.isoformat(),
                "updated_at": page.updated_at.isoformat(),
            }
        ),
        201,
    )


@bp.route("/<slug>", methods=["GET"])
@login_required
@require_scope("pages:read")
async def get_page_by_slug(slug: str):
    """
    Get page by slug with visibility ACL.

    Returns:
        200: Page details
        403: Visibility denied
        404: Not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    user_id = _get_user_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def fetch_page():
        return (
            db((db.pg_pages.tenant_id == tenant_id) & (db.pg_pages.slug == slug))
            .select()
            .first()
        )

    page = await run_in_threadpool(fetch_page)

    if not page:
        return ApiResponse.error("Page not found", 404)

    # Visibility ACL (public/authenticated/roles/users), fail-closed.
    if not _can_read_page(page, user_id, _get_user_roles()):
        return ApiResponse.error("Not found", 403)

    return (
        jsonify(
            {
                "id": page.id,
                "village_id": page.village_id,
                "title": page.title,
                "slug": page.slug,
                "body_html": page.body_html,
                "status": page.status,
                "visibility": page.visibility,
                "is_public": page.is_public,
                "author_id": page.author_identity_id,
                "published_at": (
                    page.published_at.isoformat() if page.published_at else None
                ),
                "created_at": page.created_at.isoformat(),
                "updated_at": page.updated_at.isoformat(),
            }
        ),
        200,
    )


@bp.route("/<slug>", methods=["PUT"])
@login_required
@require_scope("pages:write")
async def update_page(slug: str):
    """
    Update page by slug.

    Request body:
        {
            "title": "string (optional)",
            "body_html": "string (optional, will be sanitized)",
            "status": "string (optional)",
            "visibility": "string (optional)",
            "is_public": "boolean (optional)",
        }

    Returns:
        200: Updated page
        404: Not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    user_id = _get_user_id()

    if not tenant_id or not user_id:
        return ApiResponse.error("Tenant or user not found", 403)

    try:
        data = await request.get_json()
    except Exception:
        return ApiResponse.error("Invalid JSON", 400)

    user_roles = _get_user_roles()

    def update_and_rebuild():
        page = (
            db((db.pg_pages.tenant_id == tenant_id) & (db.pg_pages.slug == slug))
            .select()
            .first()
        )

        if not page:
            return None, 404

        # Visibility precondition: caller must be able to READ the page before
        # modifying it (prevents same-tenant IDOR on restricted pages).
        if not _can_read_page(page, user_id, user_roles):
            return None, 404

        # Update fields
        updates = {}
        new_slug = slug

        if "title" in data:
            new_title = data["title"].strip()
            if new_title:
                updates["title"] = new_title
                new_slug = _slugify(new_title)

        if "body_html" in data:
            updates["body_html"] = _sanitize_html(data["body_html"])

        if "status" in data:
            updates["status"] = data["status"]
            if data["status"] == "published" and not page.published_at:
                updates["published_at"] = datetime.now(UTC)

        if "visibility" in data:
            updates["visibility"] = data["visibility"]

        if "is_public" in data:
            updates["is_public"] = data["is_public"]

        if "visibility_roles" in data:
            updates["visibility_roles"] = data["visibility_roles"]

        if "visibility_users" in data:
            updates["visibility_users"] = data["visibility_users"]

        if new_slug != slug:
            # Check per-tenant slug uniqueness
            existing = (
                db(
                    (db.pg_pages.tenant_id == tenant_id)
                    & (db.pg_pages.slug == new_slug)
                    & (db.pg_pages.id != page.id)
                )
                .select()
                .first()
            )

            if existing:
                return None, 409

            updates["slug"] = new_slug

        updates["updated_at"] = datetime.now(UTC)

        # Update page
        db(db.pg_pages.id == page.id).update(**updates)

        # Rebuild references if body_html changed
        if "body_html" in updates:
            rebuild_references_from_text(
                db,
                tenant_id=tenant_id,
                source_module="pages",
                source_type="page",
                source_id=new_slug,
                text=updates["body_html"],
            )

        commit_db(db)

        # Retrieve updated row
        updated = db(db.pg_pages.id == page.id).select().first()
        return updated, 200

    page, status = await run_in_threadpool(update_and_rebuild)

    if status == 404:
        return ApiResponse.error("Page not found", 404)

    if status == 409:
        return ApiResponse.error("Slug conflict", 409)

    if not page:
        return ApiResponse.error("Failed to update page", 500)

    return (
        jsonify(
            {
                "id": page.id,
                "village_id": page.village_id,
                "title": page.title,
                "slug": page.slug,
                "body_html": page.body_html,
                "status": page.status,
                "visibility": page.visibility,
                "is_public": page.is_public,
                "author_id": page.author_identity_id,
                "published_at": (
                    page.published_at.isoformat() if page.published_at else None
                ),
                "created_at": page.created_at.isoformat(),
                "updated_at": page.updated_at.isoformat(),
            }
        ),
        200,
    )


@bp.route("/<slug>", methods=["DELETE"])
@login_required
@require_scope("pages:admin")
async def delete_page(slug: str):
    """
    Delete page by slug (admin only).

    Returns:
        204: No content
        404: Not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    user_id = _get_user_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    user_roles = _get_user_roles()

    def delete_page_impl():
        page = (
            db((db.pg_pages.tenant_id == tenant_id) & (db.pg_pages.slug == slug))
            .select()
            .first()
        )

        if not page:
            return None, 404

        # Visibility precondition: caller must be able to READ before delete.
        if not _can_read_page(page, user_id, user_roles):
            return None, 404

        db(db.pg_pages.id == page.id).delete()
        commit_db(db)
        return page, 204

    page, status = await run_in_threadpool(delete_page_impl)

    if status == 404:
        return ApiResponse.error("Page not found", 404)

    return "", 204


@bp.route("/<slug>/collections/<int:collection_id>", methods=["POST"])
@login_required
@require_scope("pages:write")
async def attach_collection(slug: str, collection_id: int):
    """
    Attach a document collection to a page.

    Returns:
        200: Success
        404: Page or collection not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    user_id = _get_user_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    user_roles = _get_user_roles()

    def attach_impl():
        page = (
            db((db.pg_pages.tenant_id == tenant_id) & (db.pg_pages.slug == slug))
            .select()
            .first()
        )

        if not page:
            return None, 404

        # Visibility precondition: caller must be able to READ before mutating.
        if not _can_read_page(page, user_id, user_roles):
            return None, 404

        collection = (
            db(
                (db.doc_collections.tenant_id == tenant_id)
                & (db.doc_collections.id == collection_id)
            )
            .select()
            .first()
        )

        if not collection:
            return None, 404

        # Check if already attached
        existing = (
            db(
                (db.pg_page_collections.pg_page_id == page.id)
                & (db.pg_page_collections.doc_collection_id == collection.id)
            )
            .select()
            .first()
        )

        if not existing:
            db.pg_page_collections.insert(
                pg_page_id=page.id,
                doc_collection_id=collection.id,
            )
            commit_db(db)

        return page, 200

    page, status = await run_in_threadpool(attach_impl)

    if status == 404:
        return ApiResponse.error("Page or collection not found", 404)

    return jsonify({"message": "Collection attached"}), 200


@bp.route("/<slug>/collections/<int:collection_id>", methods=["DELETE"])
@login_required
@require_scope("pages:write")
async def detach_collection(slug: str, collection_id: int):
    """
    Detach a document collection from a page.

    Returns:
        204: No content
        404: Page or collection not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    user_id = _get_user_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    user_roles = _get_user_roles()

    def detach_impl():
        page = (
            db((db.pg_pages.tenant_id == tenant_id) & (db.pg_pages.slug == slug))
            .select()
            .first()
        )

        if not page:
            return None, 404

        # Visibility precondition: caller must be able to READ before mutating.
        if not _can_read_page(page, user_id, user_roles):
            return None, 404

        db(
            (db.pg_page_collections.pg_page_id == page.id)
            & (db.pg_page_collections.doc_collection_id == collection_id)
        ).delete()

        commit_db(db)
        return page, 204

    page, status = await run_in_threadpool(detach_impl)

    if status == 404:
        return ApiResponse.error("Page not found", 404)

    return "", 204


@bp.route("/<slug>/backlinks", methods=["GET"])
@login_required
@require_scope("pages:read")
async def get_backlinks(slug: str):
    """
    Get all pages that reference this page via wiki-links.

    Returns:
        200: List of backlinks
        404: Page not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    user_id = _get_user_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    user_roles = _get_user_roles()

    def fetch_backlinks():
        page = (
            db((db.pg_pages.tenant_id == tenant_id) & (db.pg_pages.slug == slug))
            .select()
            .first()
        )

        # Return 404 when the page is missing OR not visible to the caller, so
        # backlinks can't be enumerated for a page restricted away from them.
        if not page or not _can_read_page(page, user_id, user_roles):
            return None

        refs = backlinks_for(
            db,
            target_module="pages",
            target_type="page",
            target_id=slug,
            tenant_id=tenant_id,
        )

        return refs

    refs = await run_in_threadpool(fetch_backlinks)

    if refs is None:
        return ApiResponse.error("Page not found", 404)

    backlinks = [
        {
            "source_module": r.source_module,
            "source_type": r.source_type,
            "source_id": r.source_id,
            "context": r.context,
            "created_at": (
                r.created_at.isoformat() if hasattr(r, "created_at") else None
            ),
        }
        for r in refs
    ]

    return jsonify({"backlinks": backlinks}), 200
