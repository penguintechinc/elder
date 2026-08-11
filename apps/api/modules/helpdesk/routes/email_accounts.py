"""Helpdesk email account CRUD and test-connection endpoints using penguin-dal."""

# flake8: noqa: E501

import logging
from datetime import UTC, datetime, timezone

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.logging_config import log_error_and_respond
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams, commit_db

logger = logging.getLogger(__name__)

bp = Blueprint("helpdesk_email_accounts", __name__)


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
@require_scope("helpdesk:admin")
async def list_email_accounts():
    """
    List email accounts with optional filtering and pagination.

    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 20, max: 100)
        - is_active: Filter by active status (true/false)

    Returns:
        200: Paginated list of email accounts (passwords never included)
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Extract pagination params
    pagination = PaginationParams.from_request()

    # Build query
    query = db.hd_email_accounts.tenant_id == tenant_id

    # Apply filters
    if request.args.get("is_active"):
        is_active_str = request.args.get("is_active").lower()
        is_active = is_active_str in ("true", "1", "yes")
        query &= db.hd_email_accounts.is_active == is_active

    def get_accounts():
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.hd_email_accounts.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )
        return total, rows

    total, rows = await run_in_threadpool(get_accounts)

    accounts = [
        {
            "id": r.id,
            "email_address": r.email_address,
            "display_name": r.display_name,
            "provider": r.provider,
            "is_default": r.is_default,
            "is_active": r.is_active,
            "last_polled_at": (
                r.last_polled_at.isoformat() if r.last_polled_at else None
            ),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]

    return (
        jsonify(
            {
                "items": accounts,
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
@require_scope("helpdesk:admin")
async def create_email_account():
    """
    Create a new email account.

    Request body (SMTP/IMAP):
        {
            "email_address": "support@example.com",
            "display_name": "Support",
            "provider": "smtp_imap",
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "smtp_mode": "starttls",
            "smtp_username": "user@gmail.com",
            "smtp_password_ref": "penguin-sal-ref",
            "imap_host": "imap.gmail.com",
            "imap_port": 993,
            "imap_username": "user@gmail.com",
            "imap_password_ref": "penguin-sal-ref",
            "is_default": false
        }

    Request body (Gmail API):
        {
            "email_address": "support@example.com",
            "display_name": "Support",
            "provider": "gmail_api",
            "gmail_credentials_ref": "penguin-sal-ref",
            "gmail_token_ref": "penguin-sal-ref",
            "is_default": false
        }

    Returns:
        201: Created email account
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}

    # Validate required fields
    email_address = data.get("email_address", "").strip().lower()
    provider = data.get("provider", "")

    if not email_address or not provider:
        return ApiResponse.validation_error(
            "email_address and provider", "are required"
        )

    if provider not in ["smtp_imap", "gmail_api"]:
        return ApiResponse.validation_error(
            "provider", "must be 'smtp_imap' or 'gmail_api'"
        )

    display_name = data.get("display_name", "").strip()
    is_default = data.get("is_default", False)

    def create():
        now = datetime.now(UTC)

        # Build update dict with only provided fields (pyDAL gotcha: defaults not applied)
        insert_data = {
            "tenant_id": tenant_id,
            "email_address": email_address,
            "display_name": display_name,
            "provider": provider,
            "is_default": is_default,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }

        if provider == "smtp_imap":
            insert_data["smtp_host"] = data.get("smtp_host")
            insert_data["smtp_port"] = data.get("smtp_port", 587)
            insert_data["smtp_mode"] = data.get("smtp_mode", "starttls")
            insert_data["smtp_username"] = data.get("smtp_username")
            insert_data["smtp_password_ref"] = data.get("smtp_password_ref")
            insert_data["imap_host"] = data.get("imap_host")
            insert_data["imap_port"] = data.get("imap_port", 993)
            insert_data["imap_username"] = data.get("imap_username")
            insert_data["imap_password_ref"] = data.get("imap_password_ref")
        elif provider == "gmail_api":
            insert_data["gmail_credentials_ref"] = data.get("gmail_credentials_ref")
            insert_data["gmail_token_ref"] = data.get("gmail_token_ref")

        # Insert account
        account_id = db.hd_email_accounts.insert(**insert_data)
        db.commit()

        # Fetch the account to return
        return db(db.hd_email_accounts.id == account_id).select().first()

    account_row = await run_in_threadpool(create)

    return (
        jsonify(
            {
                "id": account_row.id,
                "email_address": account_row.email_address,
                "display_name": account_row.display_name,
                "provider": account_row.provider,
                "is_default": account_row.is_default,
                "is_active": account_row.is_active,
                "created_at": (
                    account_row.created_at.isoformat()
                    if account_row.created_at
                    else None
                ),
            }
        ),
        201,
    )


@bp.route("/<int:account_id>", methods=["GET"])
@login_required
@require_scope("helpdesk:admin")
async def get_email_account(account_id):
    """
    Get a single email account by ID.

    Path parameters:
        account_id: Account ID

    Returns:
        200: Email account details (no passwords)
        404: Account not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def fetch():
        return (
            db(
                (db.hd_email_accounts.id == account_id)
                & (db.hd_email_accounts.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

    account_row = await run_in_threadpool(fetch)

    if not account_row:
        return ApiResponse.not_found("Email account")

    return jsonify(
        {
            "id": account_row.id,
            "email_address": account_row.email_address,
            "display_name": account_row.display_name,
            "provider": account_row.provider,
            "smtp_host": account_row.smtp_host,
            "smtp_port": account_row.smtp_port,
            "smtp_mode": account_row.smtp_mode,
            "smtp_username": account_row.smtp_username,
            "smtp_password_ref": account_row.smtp_password_ref,
            "imap_host": account_row.imap_host,
            "imap_port": account_row.imap_port,
            "imap_username": account_row.imap_username,
            "imap_password_ref": account_row.imap_password_ref,
            "gmail_credentials_ref": account_row.gmail_credentials_ref,
            "gmail_token_ref": account_row.gmail_token_ref,
            "is_default": account_row.is_default,
            "is_active": account_row.is_active,
            "last_polled_at": (
                account_row.last_polled_at.isoformat()
                if account_row.last_polled_at
                else None
            ),
            "created_at": (
                account_row.created_at.isoformat() if account_row.created_at else None
            ),
            "updated_at": (
                account_row.updated_at.isoformat() if account_row.updated_at else None
            ),
        }
    )


@bp.route("/<int:account_id>", methods=["PATCH"])
@login_required
@require_scope("helpdesk:admin")
async def update_email_account(account_id):
    """
    Update an email account.

    Path parameters:
        account_id: Account ID

    Request body:
        {
            "display_name": "New Name",
            "is_default": true,
            "is_active": false
        }

    Returns:
        200: Updated email account
        404: Account not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}

    def update():
        account_row = (
            db(
                (db.hd_email_accounts.id == account_id)
                & (db.hd_email_accounts.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not account_row:
            return None

        now = datetime.now(UTC)
        updates = {"updated_at": now}

        # Only update provided fields
        if "display_name" in data:
            updates["display_name"] = data["display_name"].strip()
        if "is_default" in data:
            updates["is_default"] = data["is_default"]
        if "is_active" in data:
            updates["is_active"] = data["is_active"]

        db(db.hd_email_accounts.id == account_id).update(**updates)
        db.commit()

        return db(db.hd_email_accounts.id == account_id).select().first()

    account_row = await run_in_threadpool(update)

    if not account_row:
        return ApiResponse.not_found("Email account")

    return jsonify(
        {
            "id": account_row.id,
            "email_address": account_row.email_address,
            "display_name": account_row.display_name,
            "provider": account_row.provider,
            "is_default": account_row.is_default,
            "is_active": account_row.is_active,
            "created_at": (
                account_row.created_at.isoformat() if account_row.created_at else None
            ),
            "updated_at": (
                account_row.updated_at.isoformat() if account_row.updated_at else None
            ),
        }
    )


@bp.route("/<int:account_id>", methods=["DELETE"])
@login_required
@require_scope("helpdesk:admin")
async def delete_email_account(account_id):
    """
    Delete an email account.

    Path parameters:
        account_id: Account ID

    Returns:
        204: Account deleted
        404: Account not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def delete():
        account_row = (
            db(
                (db.hd_email_accounts.id == account_id)
                & (db.hd_email_accounts.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not account_row:
            return False

        db(db.hd_email_accounts.id == account_id).delete()
        db.commit()
        return True

    deleted = await run_in_threadpool(delete)

    if not deleted:
        return ApiResponse.not_found("Email account")

    return "", 204


@bp.route("/<int:account_id>/test-connection", methods=["POST"])
@login_required
@require_scope("helpdesk:admin")
async def test_email_account(account_id):
    """
    Test email account connectivity (SMTP/IMAP).

    Path parameters:
        account_id: Account ID

    Returns:
        200: Test result response
        404: Account not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def test():
        account_row = (
            db(
                (db.hd_email_accounts.id == account_id)
                & (db.hd_email_accounts.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not account_row:
            return None

        # Return status based on configuration
        # Actual SMTP/IMAP testing would require penguin-sal secret retrieval
        # and network calls; for now, return configuration status
        smtp_configured = bool(account_row.smtp_host and account_row.smtp_username)
        imap_configured = bool(account_row.imap_host and account_row.imap_username)
        gmail_configured = bool(account_row.gmail_credentials_ref)

        return {
            "account_id": account_row.id,
            "smtp_configured": smtp_configured,
            "imap_configured": imap_configured,
            "gmail_configured": gmail_configured,
        }

    result = await run_in_threadpool(test)

    if not result:
        return ApiResponse.not_found("Email account")

    return jsonify(result)
