"""Documents CRUD and workflow endpoints using penguin-dal."""

# flake8: noqa: E501

import logging
from datetime import UTC, datetime, timezone

from markdown_it import MarkdownIt
from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.common.licensing.enforce import check_limit
from apps.api.logging_config import log_error_and_respond
from apps.api.modules.documents.common import (
    identity_in_tenant,
    sanitize_html,
    slugify,
    visibility_users_in_tenant,
)
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams
from shared.utils.village_id import generate_village_id

logger = logging.getLogger(__name__)

bp = Blueprint("documents", __name__)

# Markdown renderer
_md = MarkdownIt("gfm-like").enable("table")


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


def _render_and_sanitize_body(markdown_text: str) -> tuple[str, str]:
    """Render markdown to HTML and sanitize it.

    Args:
        markdown_text: Markdown source text

    Returns:
        Tuple of (plaintext_extraction, sanitized_html)
    """
    # Render markdown to HTML
    html_content = _md.render(markdown_text)

    # Sanitize the HTML
    sanitized = sanitize_html(html_content)

    # Extract plaintext for search (simple: strip HTML tags)
    plaintext = sanitized
    plaintext = plaintext.replace("<br>", " ")
    plaintext = plaintext.replace("<hr>", " ")
    plaintext = plaintext.replace("<img", " [image]")
    plaintext = plaintext.replace("</", " </")
    plaintext = plaintext.replace(">", "> ")
    # Remove remaining tags
    import re

    plaintext = re.sub(r"<[^>]+>", "", plaintext)
    # Collapse whitespace
    plaintext = " ".join(plaintext.split())

    return plaintext, sanitized


def _can_read_document(
    db, doc_row, tenant_id, identity_id=None, user_roles=None
) -> bool:
    """Check if current user can read this document based on visibility.

    Args:
        db: PyDAL database instance
        doc_row: Document row from database
        tenant_id: Current tenant ID
        identity_id: Current identity ID (optional, from token)

    Returns:
        True if user can read, False otherwise
    """
    # Cross-tenant isolation: document must belong to this tenant
    if doc_row.tenant_id != tenant_id:
        return False

    # Visibility logic
    visibility = doc_row.visibility or "authenticated"

    # Public documents visible to everyone
    if visibility == "public" and doc_row.is_public:
        return True

    # Unauthenticated users can't read anything else
    if identity_id is None:
        return False

    # Admin bypass: check if user has admin role (from OIDC scopes)
    # For now, any authenticated user can read authenticated docs
    if visibility == "authenticated":
        return True

    # Role-based visibility: caller must hold at least one of the document's roles.
    if visibility == "roles":
        allowed_roles = set(doc_row.visibility_roles or [])
        if not allowed_roles:
            return False  # fail closed: 'roles' visibility with no roles configured
        return bool(set(user_roles or []) & allowed_roles)

    # User-based visibility
    if visibility == "users":
        allowed_users = doc_row.visibility_users or []
        return identity_id in allowed_users

    return False


@bp.route("", methods=["GET"])
@login_required
@require_scope("documents:read")
async def list_documents():
    """List documents with optional filtering and pagination.

    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 20, max: 100)
        - status: Filter by status (draft, published, archived)
        - category: Filter by category
        - q: Full-text search query
        - include_drafts: Include draft documents (requires write scope)

    Returns:
        200: Paginated list of documents
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Extract pagination params and filters BEFORE threadpool
    pagination = PaginationParams.from_request()
    status_filter = request.args.get("status")
    category = request.args.get("category")
    q = request.args.get("q", "").strip()

    def list_docs():
        # Build base query scoped to tenant
        query = db.doc_documents.tenant_id == tenant_id

        # Status filter
        if status_filter:
            query &= db.doc_documents.status == status_filter
        else:
            # Default: show published docs only
            query &= db.doc_documents.status == "published"

        # Category filter
        if category:
            query &= db.doc_documents.category == category

        # Full-text search
        if q:
            query &= (db.doc_documents.title.like(f"%{q}%")) | (
                db.doc_documents.body_text.like(f"%{q}%")
            )

        total = db(query).count()
        rows = db(query).select(
            orderby=~db.doc_documents.published_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return total, rows

    total, rows = await run_in_threadpool(list_docs)

    # Get current identity_id from token (for visibility filtering)
    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")  # May be None for public endpoints

    # Post-filter by visibility
    documents = []
    for r in rows:
        if _can_read_document(db, r, tenant_id, identity_id, claims.get("roles") or []):
            documents.append(
                {
                    "id": r.id,
                    "village_id": r.village_id,
                    "title": r.title,
                    "slug": r.slug,
                    "category": r.category,
                    "status": r.status,
                    "is_public": r.is_public,
                    "visibility": r.visibility,
                    "tags": r.tags,
                    "published_at": (
                        r.published_at.isoformat() if r.published_at else None
                    ),
                    "created_at": r.created_at.isoformat(),
                    "updated_at": r.updated_at.isoformat(),
                }
            )

    return (
        jsonify(
            {
                "items": documents,
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
@require_scope("documents:write")
async def create_document():
    """Create a new document.

    Requires: documents:write scope

    Request body:
        {
            "title": "string (required)",
            "body": "markdown source (required)",
            "category": "string (optional)",
            "tags": ["array of tags (optional)"],
            "visibility": "public|authenticated|roles|users (default: authenticated)",
            "visibility_roles": ["array of allowed roles for 'roles' visibility"],
            "visibility_users": [array of allowed identity IDs for 'users' visibility]
        }

    Returns:
        201: Created document
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    blocked = await check_limit("object", tenant_id)
    if blocked is not None:
        return blocked

    data = await request.get_json() or {}

    # Validate required fields
    title = data.get("title", "").strip()
    if not title:
        return ApiResponse.validation_error("title", "is required")

    body = data.get("body", "").strip()
    if not body:
        return ApiResponse.validation_error("body", "is required")

    # Get current identity from token
    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")
    if not identity_id:
        return ApiResponse.error("Identity not found in token", 403)

    # Mint the village_id in request context (generate_village_id needs
    # current_app.redis_client, which is unavailable inside the threadpool).
    village_id = generate_village_id(tenant_id, current_app.redis_client)

    def create():
        # Validate that visibility_users (if provided) belong to this tenant
        visibility_users = data.get("visibility_users", [])
        if visibility_users and not visibility_users_in_tenant(
            db, visibility_users, tenant_id
        ):
            return "visibility_users_not_in_tenant"

        # Generate slug from title
        slug = slugify(title)
        if not slug:
            return "invalid_title_for_slug"

        # Global slug uniqueness is intentional: public documents are served at
        # /public/<slug> with no tenant context, so slugs form a single global
        # namespace and must resolve deterministically. (This means the
        # uniqueness check is a weak cross-tenant existence oracle by design —
        # accepted as inherent to the tenant-less public-URL scheme.)
        existing = db(db.doc_documents.slug == slug).select().first()
        if existing:
            return "slug_already_exists"

        # Render markdown to HTML and sanitize
        body_text, body_html = _render_and_sanitize_body(body)

        now = datetime.now(UTC)

        # Parse visibility settings
        visibility = data.get("visibility", "authenticated")
        if visibility not in ("public", "authenticated", "roles", "users"):
            return "invalid_visibility"

        is_public = visibility == "public"
        visibility_roles = data.get("visibility_roles") or None
        visibility_users_list = data.get("visibility_users") or None

        # Validate visibility constraints
        if visibility == "roles" and not visibility_roles:
            return "visibility_roles_required"
        if visibility == "users" and not visibility_users_list:
            return "visibility_users_required"

        # Insert document
        doc_id = db.doc_documents.insert(
            tenant_id=tenant_id,
            village_id=village_id,
            title=title,
            slug=slug,
            body_html=body_html,
            body_text=body_text,
            body_markdown=body,
            category=data.get("category"),
            tags=data.get("tags", []),
            status="draft",
            is_public=is_public,
            visibility=visibility,
            visibility_roles=visibility_roles,
            visibility_users=visibility_users_list,
            author_identity_id=identity_id,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Rebuild wiki-link references from body text
        from apps.api.common.refs.wikilinks import rebuild_references_from_text

        try:
            rebuild_references_from_text(
                db, tenant_id, "documents", "document", slug, body
            )
            db.commit()
        except Exception as e:
            logger.error(f"Failed to rebuild references for doc {doc_id}: {e}")
            # Non-blocking: continue even if refs fail

        # Fetch the created document
        return db(db.doc_documents.id == doc_id).select().first()

    result = await run_in_threadpool(create)

    if isinstance(result, str):
        if result == "visibility_users_not_in_tenant":
            return ApiResponse.error("visibility_users not found in tenant", 400)
        elif result == "invalid_title_for_slug":
            return ApiResponse.error("Title cannot be converted to a slug", 400)
        elif result == "slug_already_exists":
            return ApiResponse.error("Document with this slug already exists", 409)
        elif result == "invalid_visibility":
            return ApiResponse.error("Invalid visibility value", 400)
        elif result == "visibility_roles_required":
            return ApiResponse.error(
                "visibility_roles required for role-based visibility", 400
            )
        elif result == "visibility_users_required":
            return ApiResponse.error(
                "visibility_users required for user-based visibility", 400
            )
        else:
            return ApiResponse.error(result, 400)

    doc = result

    return (
        jsonify(
            {
                "id": doc.id,
                "village_id": doc.village_id,
                "title": doc.title,
                "slug": doc.slug,
                "category": doc.category,
                "status": doc.status,
                "is_public": doc.is_public,
                "visibility": doc.visibility,
                "tags": doc.tags,
                "published_at": (
                    doc.published_at.isoformat() if doc.published_at else None
                ),
                "created_at": doc.created_at.isoformat(),
                "updated_at": doc.updated_at.isoformat(),
            }
        ),
        201,
    )


@bp.route("/<slug>", methods=["GET"])
@login_required
@require_scope("documents:read")
async def get_document(slug):
    """Get a document by slug.

    Returns:
        200: Document details
        403: Access denied (visibility check)
        404: Document not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")

    def fetch():
        return (
            db(
                (db.doc_documents.slug == slug)
                & (db.doc_documents.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

    doc = await run_in_threadpool(fetch)

    # Return 404 for both "does not exist" and "not visible to caller" so a
    # cross-tenant or role-restricted slug is indistinguishable from a missing one.
    if not doc or not _can_read_document(
        db, doc, tenant_id, identity_id, claims.get("roles") or []
    ):
        return ApiResponse.not_found("Document")

    return jsonify(
        {
            "id": doc.id,
            "village_id": doc.village_id,
            "title": doc.title,
            "slug": doc.slug,
            "body_html": doc.body_html,
            "body_text": doc.body_text,
            "body_markdown": doc.body_markdown,
            "category": doc.category,
            "status": doc.status,
            "is_public": doc.is_public,
            "visibility": doc.visibility,
            "visibility_roles": doc.visibility_roles,
            "visibility_users": doc.visibility_users,
            "tags": doc.tags,
            "published_at": doc.published_at.isoformat() if doc.published_at else None,
            "created_at": doc.created_at.isoformat(),
            "updated_at": doc.updated_at.isoformat(),
        }
    )


@bp.route("/<int:doc_id>", methods=["PATCH"])
@login_required
@require_scope("documents:write")
async def update_document(doc_id):
    """Update a document.

    Requires: documents:write scope

    Returns:
        200: Updated document
        404: Document not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")
    user_roles = claims.get("roles") or []

    def update():
        doc = (
            db(
                (db.doc_documents.id == doc_id)
                & (db.doc_documents.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not doc:
            return None

        # Visibility precondition: caller must be able to READ the document
        # before they may modify it (prevents same-tenant IDOR on role/user
        # restricted docs held by a documents:write caller).
        if not _can_read_document(db, doc, tenant_id, identity_id, user_roles):
            return None

        # Validate visibility_users if provided
        if "visibility_users" in data:
            if data["visibility_users"] and not visibility_users_in_tenant(
                db, data["visibility_users"], tenant_id
            ):
                return "visibility_users_not_in_tenant"

        now = datetime.now(UTC)
        updates = {"updated_at": now}

        # If title changes, regenerate slug and validate uniqueness
        old_slug = doc.slug
        if "title" in data:
            new_title = data["title"].strip()
            if new_title:
                new_slug = slugify(new_title)
                if new_slug and new_slug != old_slug:
                    # Check slug uniqueness
                    existing = db(db.doc_documents.slug == new_slug).select().first()
                    if existing:
                        return "slug_already_exists"
                    updates["title"] = new_title
                    updates["slug"] = new_slug

        # If body changes, re-render and sanitize
        if "body" in data:
            body = data["body"].strip()
            if body:
                body_text, body_html = _render_and_sanitize_body(body)
                updates["body_html"] = body_html
                updates["body_text"] = body_text
                updates["body_markdown"] = body

        # Update other fields
        if "category" in data:
            updates["category"] = data["category"]
        if "tags" in data:
            updates["tags"] = data["tags"]
        if "visibility" in data:
            visibility = data["visibility"]
            if visibility not in ("public", "authenticated", "roles", "users"):
                return "invalid_visibility"
            updates["visibility"] = visibility
            updates["is_public"] = visibility == "public"
            updates["visibility_roles"] = data.get("visibility_roles") or None
            updates["visibility_users"] = data.get("visibility_users") or None

        # Create version snapshot before updating
        version_number = db(db.doc_versions.doc_document_id == doc_id).count() + 1
        db.doc_versions.insert(
            doc_document_id=doc_id,
            version_number=version_number,
            title=doc.title,
            body_html=doc.body_html,
            body_text=doc.body_text,
            body_markdown=doc.body_markdown,
            author_identity_id=doc.author_identity_id,
            created_at=now,
        )

        # Update document
        db(db.doc_documents.id == doc_id).update(**updates)
        db.commit()

        # Rebuild wiki-link references from new body text (if it changed)
        if "body" in data:
            from apps.api.common.refs.wikilinks import rebuild_references_from_text

            new_slug = updates.get("slug", old_slug)
            try:
                rebuild_references_from_text(
                    db, tenant_id, "documents", "document", new_slug, data["body"]
                )
                db.commit()
            except Exception as e:
                logger.error(f"Failed to rebuild references for doc {doc_id}: {e}")

        return db(db.doc_documents.id == doc_id).select().first()

    result = await run_in_threadpool(update)

    if isinstance(result, str):
        if result == "visibility_users_not_in_tenant":
            return ApiResponse.error("visibility_users not found in tenant", 400)
        elif result == "slug_already_exists":
            return ApiResponse.error("Document with this slug already exists", 409)
        elif result == "invalid_visibility":
            return ApiResponse.error("Invalid visibility value", 400)
        else:
            return ApiResponse.error(result, 400)

    if not result:
        return ApiResponse.not_found("Document")

    doc = result

    return jsonify(
        {
            "id": doc.id,
            "village_id": doc.village_id,
            "title": doc.title,
            "slug": doc.slug,
            "category": doc.category,
            "status": doc.status,
            "is_public": doc.is_public,
            "visibility": doc.visibility,
            "tags": doc.tags,
            "published_at": doc.published_at.isoformat() if doc.published_at else None,
            "created_at": doc.created_at.isoformat(),
            "updated_at": doc.updated_at.isoformat(),
        }
    )


@bp.route("/<int:doc_id>", methods=["DELETE"])
@login_required
@require_scope("documents:write")
async def delete_document(doc_id):
    """Delete a document.

    Requires: documents:admin scope

    Returns:
        204: Document deleted
        404: Document not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")
    user_roles = claims.get("roles") or []

    def delete():
        doc = (
            db(
                (db.doc_documents.id == doc_id)
                & (db.doc_documents.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not doc:
            return False

        # Visibility precondition: caller must be able to READ before delete.
        if not _can_read_document(db, doc, tenant_id, identity_id, user_roles):
            return False

        # Delete versions
        db(db.doc_versions.doc_document_id == doc_id).delete()

        # Delete document collection associations
        db(db.doc_document_collections.doc_document_id == doc_id).delete()

        # Delete references
        from apps.api.common.refs.service import delete_references_for_source

        delete_references_for_source(db, "documents", "document", str(doc.slug))

        # Delete document
        db(db.doc_documents.id == doc_id).delete()
        db.commit()
        return True

    deleted = await run_in_threadpool(delete)

    if not deleted:
        return ApiResponse.not_found("Document")

    return "", 204


@bp.route("/<int:doc_id>/publish", methods=["POST"])
@login_required
@require_scope("documents:write")
async def publish_document(doc_id):
    """Publish a document (set status=published, set published_at).

    Requires: documents:write scope

    Returns:
        200: Updated document
        404: Document not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")
    user_roles = claims.get("roles") or []

    def publish():
        doc = (
            db(
                (db.doc_documents.id == doc_id)
                & (db.doc_documents.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not doc:
            return None

        # Visibility precondition: caller must be able to READ before publish.
        if not _can_read_document(db, doc, tenant_id, identity_id, user_roles):
            return None

        now = datetime.now(UTC)
        db(db.doc_documents.id == doc_id).update(
            status="published",
            published_at=now,
            updated_at=now,
        )
        db.commit()

        return db(db.doc_documents.id == doc_id).select().first()

    doc = await run_in_threadpool(publish)

    if not doc:
        return ApiResponse.not_found("Document")

    return jsonify(
        {
            "id": doc.id,
            "title": doc.title,
            "slug": doc.slug,
            "status": doc.status,
            "published_at": doc.published_at.isoformat() if doc.published_at else None,
            "updated_at": doc.updated_at.isoformat(),
        }
    )


# Public endpoints (no auth required)
@bp.route("/public", methods=["GET"])
async def list_public_documents():
    """List public documents — no authentication required.

    Returns:
        200: Paginated list of public documents
    """
    db = current_app.db

    # Extract pagination params and filters BEFORE threadpool
    pagination = PaginationParams.from_request()
    category = request.args.get("category")
    q = request.args.get("q", "").strip()

    def list_public():
        query = (db.doc_documents.is_public == True) & (
            db.doc_documents.status == "published"
        )

        # Apply filters
        if category:
            query &= db.doc_documents.category == category

        if q:
            query &= (db.doc_documents.title.like(f"%{q}%")) | (
                db.doc_documents.body_text.like(f"%{q}%")
            )

        total = db(query).count()
        rows = db(query).select(
            orderby=~db.doc_documents.published_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return total, rows

    total, rows = await run_in_threadpool(list_public)

    documents = [
        {
            "id": r.id,
            "slug": r.slug,
            "title": r.title,
            "category": r.category,
            "tags": r.tags,
            "excerpt": (r.body_text or "")[:200],
            "published_at": r.published_at.isoformat() if r.published_at else None,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]

    return (
        jsonify(
            {
                "items": documents,
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


@bp.route("/public/<slug>", methods=["GET"])
async def get_public_document(slug):
    """Get a public document by slug — no authentication required.

    Returns:
        200: Document details
        404: Document not found
    """
    db = current_app.db

    def fetch():
        return (
            db(
                (db.doc_documents.slug == slug)
                & (db.doc_documents.is_public == True)
                & (db.doc_documents.status == "published")
            )
            .select()
            .first()
        )

    doc = await run_in_threadpool(fetch)

    if not doc:
        return ApiResponse.not_found("Document"), 404

    return jsonify(
        {
            "id": doc.id,
            "slug": doc.slug,
            "title": doc.title,
            "body_html": doc.body_html,
            "body_text": doc.body_text,
            "category": doc.category,
            "tags": doc.tags,
            "published_at": doc.published_at.isoformat() if doc.published_at else None,
            "created_at": doc.created_at.isoformat(),
        }
    )
