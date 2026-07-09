"""Helpdesk CRM companies endpoints using penguin-dal."""

# flake8: noqa: E501

import logging
from datetime import datetime, timezone

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams

logger = logging.getLogger(__name__)

bp = Blueprint("helpdesk_companies", __name__)


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


@bp.route("", methods=["GET"])
@login_required
async def list_companies():
    """
    List companies with optional filtering and pagination.

    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 20, max: 100)
        - name: Filter by company name (substring match)
        - industry: Filter by industry (exact match)
        - size: Filter by company size (exact match)

    Returns:
        200: Paginated list of companies
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Extract pagination params
    pagination = PaginationParams.from_request()

    # Build query
    query = db.hd_companies.tenant_id == tenant_id

    # Apply filters
    if request.args.get("name"):
        name_filter = request.args.get("name").strip()
        query &= db.hd_companies.name.ilike(f"%{name_filter}%")

    if request.args.get("industry"):
        industry_filter = request.args.get("industry").strip()
        query &= db.hd_companies.industry == industry_filter

    if request.args.get("size"):
        size_filter = request.args.get("size").strip()
        query &= db.hd_companies.size == size_filter

    def get_companies():
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.hd_companies.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )
        return total, rows

    total, rows = await run_in_threadpool(get_companies)

    companies = [
        {
            "id": r.id,
            "village_id": r.village_id,
            "name": r.name,
            "domain": r.domain,
            "industry": r.industry,
            "size": r.size,
            "website": r.website,
            "notes": r.notes,
            "created_at": r.created_at.isoformat(),
            "updated_at": r.updated_at.isoformat(),
        }
        for r in rows
    ]

    return (
        jsonify(
            {
                "items": companies,
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
async def create_company():
    """
    Create a new company.

    Request body:
        {
            "name": "string (required)",
            "domain": "string (optional)",
            "industry": "string (optional)",
            "size": "startup|smb|mid-market|enterprise (optional)",
            "website": "string (optional)",
            "notes": "string (optional)"
        }

    Returns:
        201: Created company
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

    domain = data.get("domain", "").strip() or None
    industry = data.get("industry", "").strip() or None
    size = data.get("size", "").strip() or None
    website = data.get("website", "").strip() or None
    notes = data.get("notes", "").strip() or None

    # Capture redis_client outside threadpool context
    redis_client = current_app.redis_client

    def create():
        from shared.utils.village_id import generate_village_id

        now = datetime.now(timezone.utc)

        # Generate village_id
        village_id = generate_village_id(tenant_id, redis_client)

        # Insert company
        company_id = db.hd_companies.insert(
            tenant_id=tenant_id,
            village_id=village_id,
            name=name,
            domain=domain,
            industry=industry,
            size=size,
            website=website,
            notes=notes,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Return created company
        return db(db.hd_companies.id == company_id).select().first()

    company_row = await run_in_threadpool(create)

    return (
        jsonify(
            {
                "id": company_row.id,
                "village_id": company_row.village_id,
                "name": company_row.name,
                "domain": company_row.domain,
                "industry": company_row.industry,
                "size": company_row.size,
                "website": company_row.website,
                "notes": company_row.notes,
                "created_at": company_row.created_at.isoformat(),
                "updated_at": company_row.updated_at.isoformat(),
            }
        ),
        201,
    )


@bp.route("/<int:company_id>", methods=["GET"])
@login_required
async def get_company(company_id):
    """
    Get a single company by ID.

    Path parameters:
        company_id: Company ID

    Returns:
        200: Company details
        404: Company not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def fetch():
        return (
            db(
                (db.hd_companies.id == company_id)
                & (db.hd_companies.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

    company_row = await run_in_threadpool(fetch)

    if not company_row:
        return ApiResponse.not_found("Company")

    return jsonify(
        {
            "id": company_row.id,
            "village_id": company_row.village_id,
            "name": company_row.name,
            "domain": company_row.domain,
            "industry": company_row.industry,
            "size": company_row.size,
            "website": company_row.website,
            "notes": company_row.notes,
            "created_at": company_row.created_at.isoformat(),
            "updated_at": company_row.updated_at.isoformat(),
        }
    )


@bp.route("/<int:company_id>", methods=["PATCH"])
@login_required
async def update_company(company_id):
    """
    Update a company.

    Path parameters:
        company_id: Company ID

    Request body:
        {
            "name": "string (optional)",
            "domain": "string (optional)",
            "industry": "string (optional)",
            "size": "startup|smb|mid-market|enterprise (optional)",
            "website": "string (optional)",
            "notes": "string (optional)"
        }

    Returns:
        200: Updated company
        404: Company not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}

    def update():
        company_row = (
            db(
                (db.hd_companies.id == company_id)
                & (db.hd_companies.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not company_row:
            return None

        now = datetime.now(timezone.utc)
        updates = {"updated_at": now}

        # Only update provided fields
        if "name" in data:
            name = data["name"].strip()
            if name:
                updates["name"] = name
        if "domain" in data:
            updates["domain"] = data["domain"].strip() or None
        if "industry" in data:
            updates["industry"] = data["industry"].strip() or None
        if "size" in data:
            updates["size"] = data["size"].strip() or None
        if "website" in data:
            updates["website"] = data["website"].strip() or None
        if "notes" in data:
            updates["notes"] = data["notes"].strip() or None

        db(db.hd_companies.id == company_id).update(**updates)
        db.commit()

        return db(db.hd_companies.id == company_id).select().first()

    company_row = await run_in_threadpool(update)

    if not company_row:
        return ApiResponse.not_found("Company")

    return jsonify(
        {
            "id": company_row.id,
            "village_id": company_row.village_id,
            "name": company_row.name,
            "domain": company_row.domain,
            "industry": company_row.industry,
            "size": company_row.size,
            "website": company_row.website,
            "notes": company_row.notes,
            "created_at": company_row.created_at.isoformat(),
            "updated_at": company_row.updated_at.isoformat(),
        }
    )


@bp.route("/<int:company_id>", methods=["DELETE"])
@login_required
async def delete_company(company_id):
    """
    Delete a company.

    Path parameters:
        company_id: Company ID

    Returns:
        204: Company deleted
        404: Company not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def delete():
        company_row = (
            db(
                (db.hd_companies.id == company_id)
                & (db.hd_companies.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not company_row:
            return False

        db(db.hd_companies.id == company_id).delete()
        db.commit()
        return True

    deleted = await run_in_threadpool(delete)

    if not deleted:
        return ApiResponse.not_found("Company")

    return ApiResponse.no_content()
