"""Certificates management API endpoints for Elder using PyDAL with async/await and shared helpers."""

# flake8: noqa: E501


from dataclasses import asdict
from datetime import date, datetime

from flask import Blueprint, current_app, jsonify, request

from apps.api.auth.decorators import login_required, resource_role_required
from apps.api.models.dataclasses import PaginatedResponse
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.pydal_helpers import PaginationParams
from apps.api.utils.validation_helpers import (
    validate_enum_value,
    validate_json_body,
    validate_organization_and_get_tenant,
    validate_required_fields,
    validate_resource_exists,
)
from apps.api.utils.async_utils import run_in_threadpool

bp = Blueprint("certificates", __name__)

# Valid certificate creators
VALID_CREATORS = [
    "digicert",
    "letsencrypt",
    "self_signed",
    "sectigo",
    "globalsign",
    "godaddy",
    "entrust",
    "certbot",
    "acme",
    "comodo",
    "thawte",
    "geotrust",
    "rapidssl",
    "internal_ca",
    "other",
]

# Valid certificate types
VALID_CERT_TYPES = [
    "ca_root",
    "ca_intermediate",
    "server_cert",
    "client_cert",
    "code_signing",
    "wildcard",
    "san",
    "ecc",
    "rsa",
    "email",
    "other",
]

# Valid certificate statuses
VALID_STATUSES = [
    "active",
    "expiring_soon",
    "expired",
    "revoked",
    "pending",
    "archived",
]

# Valid validation types
VALID_VALIDATION_TYPES = ["DV", "OV", "EV"]

# Valid renewal methods
VALID_RENEWAL_METHODS = ["acme_http", "acme_dns", "manual", "api"]

# Valid ACME challenge types
VALID_ACME_CHALLENGES = ["http-01", "dns-01", "tls-alpn-01"]

# Valid CT log statuses
VALID_CT_LOG_STATUSES = ["logged", "pending", "not_required"]


def calculate_certificate_status(expiration_date, renewal_days_before, is_revoked):
    """
    Calculate certificate status based on expiration date and revocation status.

    Args:
        expiration_date: Certificate expiration date
        renewal_days_before: Days before expiration to mark as "expiring_soon"
        is_revoked: Whether the certificate is revoked

    Returns:
        Status string: "expired", "expiring_soon", "active", or "revoked"
    """
    if is_revoked:
        return "revoked"

    today = date.today()

    if expiration_date < today:
        return "expired"

    # Calculate days until expiration
    days_until_expiration = (expiration_date - today).days

    if days_until_expiration <= renewal_days_before:
        return "expiring_soon"

    return "active"


@bp.route("", methods=["GET"])
@login_required
async def list_certificates():
    """List certificates with optional filtering."""
    db = current_app.db
    pagination = PaginationParams.from_request()

    def get_certificates():
        query = db.certificates.id > 0

        if request.args.get("organization_id"):
            query &= db.certificates.organization_id == request.args.get(
                "organization_id", type=int
            )
        if request.args.get("creator"):
            query &= db.certificates.creator == request.args.get("creator")
        if request.args.get("cert_type"):
            query &= db.certificates.cert_type == request.args.get("cert_type")
        if request.args.get("status"):
            query &= db.certificates.status == request.args.get("status")
        if request.args.get("is_active") is not None:
            is_active = request.args.get("is_active", "").lower() == "true"
            query &= db.certificates.is_active == is_active
        if request.args.get("search"):
            search = f"%{request.args.get('search')}%"
            query &= (
                (db.certificates.name.ilike(search))
                | (db.certificates.description.ilike(search))
                | (db.certificates.common_name.ilike(search))
            )

        total = db(query).count()
        rows = db(query).select(
            orderby=~db.certificates.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )
        return total, rows

    total, rows = await run_in_threadpool(get_certificates)
    pages = pagination.calculate_pages(total)

    # Convert rows to dicts and calculate dynamic status if expiring_soon filter is used
    items = []
    for row in rows:
        cert_dict = row.as_dict()
        # Recalculate status based on current date
        if row.expiration_date:
            cert_dict["status"] = calculate_certificate_status(
                row.expiration_date,
                row.renewal_days_before or 30,
                row.is_revoked or False,
            )
        items.append(cert_dict)

    # Handle expiring_soon filter
    if request.args.get("expiring_soon") == "true":
        items = [item for item in items if item["status"] == "expiring_soon"]
        total = len(items)
        pages = pagination.calculate_pages(total)

    response = PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        pages=pages,
    )

    return jsonify(asdict(response)), 200


@bp.route("", methods=["POST"])
@login_required
async def create_certificate():
    """Create a new certificate entry."""
    db = current_app.db

    data = request.get_json()
    if error := validate_json_body(data):
        return error

    if error := validate_required_fields(
        data,
        [
            "name",
            "organization_id",
            "cert_type",
            "creator",
            "issue_date",
            "expiration_date",
        ],
    ):
        return error

    if error := validate_enum_value(data.get("creator"), VALID_CREATORS, "creator"):
        return error

    if error := validate_enum_value(
        data.get("cert_type"), VALID_CERT_TYPES, "cert_type"
    ):
        return error

    if data.get("validation_type"):
        if error := validate_enum_value(
            data["validation_type"], VALID_VALIDATION_TYPES, "validation_type"
        ):
            return error

    if data.get("renewal_method"):
        if error := validate_enum_value(
            data["renewal_method"], VALID_RENEWAL_METHODS, "renewal_method"
        ):
            return error

    if data.get("acme_challenge_type"):
        if error := validate_enum_value(
            data["acme_challenge_type"], VALID_ACME_CHALLENGES, "acme_challenge_type"
        ):
            return error

    if data.get("ct_log_status"):
        if error := validate_enum_value(
            data["ct_log_status"], VALID_CT_LOG_STATUSES, "ct_log_status"
        ):
            return error

    org, tenant_id, error = await validate_organization_and_get_tenant(
        data["organization_id"]
    )
    if error:
        return error

    # Validate private_key_secret_id if provided
    if data.get("private_key_secret_id"):
        secret, error = await validate_resource_exists(
            db.builtin_secrets, data["private_key_secret_id"], "Built-in Secret"
        )
        if error:
            return error

    def create():
        # Parse dates
        issue_date = data.get("issue_date")
        expiration_date = data.get("expiration_date")

        if isinstance(issue_date, str):
            issue_date = datetime.fromisoformat(issue_date).date()
        if isinstance(expiration_date, str):
            expiration_date = datetime.fromisoformat(expiration_date).date()

        # Calculate initial status
        renewal_days_before = data.get("renewal_days_before", 30)
        is_revoked = data.get("is_revoked", False)
        calculated_status = calculate_certificate_status(
            expiration_date, renewal_days_before, is_revoked
        )

        cert_id = db.certificates.insert(
            tenant_id=tenant_id,
            name=data["name"],
            description=data.get("description"),
            organization_id=data["organization_id"],
            creator=data["creator"],
            cert_type=data["cert_type"],
            common_name=data.get("common_name"),
            subject_alternative_names=data.get("subject_alternative_names"),
            organization_unit=data.get("organization_unit"),
            locality=data.get("locality"),
            state_province=data.get("state_province"),
            country=data.get("country"),
            issuer_common_name=data.get("issuer_common_name"),
            issuer_organization=data.get("issuer_organization"),
            key_algorithm=data.get("key_algorithm"),
            key_size=data.get("key_size"),
            signature_algorithm=data.get("signature_algorithm"),
            issue_date=issue_date,
            expiration_date=expiration_date,
            not_before=data.get("not_before"),
            not_after=data.get("not_after"),
            certificate_pem=data.get("certificate_pem"),
            certificate_fingerprint_sha1=data.get("certificate_fingerprint_sha1"),
            certificate_fingerprint_sha256=data.get("certificate_fingerprint_sha256"),
            serial_number=data.get("serial_number"),
            private_key_secret_id=data.get("private_key_secret_id"),
            entities_using=data.get("entities_using"),
            services_using=data.get("services_using"),
            file_path=data.get("file_path"),
            vault_path=data.get("vault_path"),
            auto_renew=data.get("auto_renew", False),
            renewal_days_before=renewal_days_before,
            last_renewed_at=data.get("last_renewed_at"),
            renewal_method=data.get("renewal_method"),
            acme_account_url=data.get("acme_account_url"),
            acme_order_url=data.get("acme_order_url"),
            acme_challenge_type=data.get("acme_challenge_type"),
            is_revoked=is_revoked,
            revoked_at=data.get("revoked_at"),
            revocation_reason=data.get("revocation_reason"),
            validation_type=data.get("validation_type"),
            ct_log_status=data.get("ct_log_status", "not_required"),
            ocsp_must_staple=data.get("ocsp_must_staple", False),
            cost_annual=data.get("cost_annual"),
            purchase_date=data.get("purchase_date"),
            vendor=data.get("vendor"),
            notes=data.get("notes"),
            tags=data.get("tags"),
            custom_metadata=data.get("custom_metadata"),
            status=calculated_status,
            is_active=data.get("is_active", True),
            created_by_id=data.get("created_by_id"),
        )
        db.commit()
        return db.certificates[cert_id]

    certificate = await run_in_threadpool(create)
    return ApiResponse.created(certificate.as_dict())


@bp.route("/<int:id>", methods=["GET"])
@login_required
async def get_certificate(id: int):
    """Get a single certificate entry by ID."""
    db = current_app.db

    certificate, error = await validate_resource_exists(
        db.certificates, id, "Certificate"
    )
    if error:
        return error

    cert_dict = certificate.as_dict()
    # Recalculate status based on current date
    if certificate.expiration_date:
        cert_dict["status"] = calculate_certificate_status(
            certificate.expiration_date,
            certificate.renewal_days_before or 30,
            certificate.is_revoked or False,
        )

    return ApiResponse.success(cert_dict)


@bp.route("/<int:id>", methods=["PUT"])
@login_required
@resource_role_required("maintainer")
async def update_certificate(id: int):
    """Update a certificate entry."""
    db = current_app.db

    data = request.get_json()
    if error := validate_json_body(data):
        return error

    if data.get("creator"):
        if error := validate_enum_value(data["creator"], VALID_CREATORS, "creator"):
            return error

    if data.get("cert_type"):
        if error := validate_enum_value(
            data["cert_type"], VALID_CERT_TYPES, "cert_type"
        ):
            return error

    if data.get("validation_type"):
        if error := validate_enum_value(
            data["validation_type"], VALID_VALIDATION_TYPES, "validation_type"
        ):
            return error

    if data.get("renewal_method"):
        if error := validate_enum_value(
            data["renewal_method"], VALID_RENEWAL_METHODS, "renewal_method"
        ):
            return error

    if data.get("acme_challenge_type"):
        if error := validate_enum_value(
            data["acme_challenge_type"], VALID_ACME_CHALLENGES, "acme_challenge_type"
        ):
            return error

    if data.get("ct_log_status"):
        if error := validate_enum_value(
            data["ct_log_status"], VALID_CT_LOG_STATUSES, "ct_log_status"
        ):
            return error

    org_tenant_id = None
    if "organization_id" in data:
        org, org_tenant_id, error = await validate_organization_and_get_tenant(
            data["organization_id"]
        )
        if error:
            return error

    if data.get("private_key_secret_id"):
        secret, error = await validate_resource_exists(
            db.builtin_secrets, data["private_key_secret_id"], "Built-in Secret"
        )
        if error:
            return error

    def update():
        certificate = db.certificates[id]
        if not certificate:
            return None

        update_dict = {}
        updateable_fields = [
            "name",
            "description",
            "creator",
            "cert_type",
            "common_name",
            "subject_alternative_names",
            "organization_unit",
            "locality",
            "state_province",
            "country",
            "issuer_common_name",
            "issuer_organization",
            "key_algorithm",
            "key_size",
            "signature_algorithm",
            "certificate_pem",
            "certificate_fingerprint_sha1",
            "certificate_fingerprint_sha256",
            "serial_number",
            "private_key_secret_id",
            "entities_using",
            "services_using",
            "file_path",
            "vault_path",
            "auto_renew",
            "renewal_days_before",
            "last_renewed_at",
            "renewal_method",
            "acme_account_url",
            "acme_order_url",
            "acme_challenge_type",
            "is_revoked",
            "revoked_at",
            "revocation_reason",
            "validation_type",
            "ct_log_status",
            "ocsp_must_staple",
            "cost_annual",
            "purchase_date",
            "vendor",
            "notes",
            "tags",
            "custom_metadata",
            "is_active",
        ]

        for field in updateable_fields:
            if field in data:
                update_dict[field] = data[field]

        if "organization_id" in data:
            update_dict["organization_id"] = data["organization_id"]
            update_dict["tenant_id"] = org_tenant_id

        # Handle date fields
        if "issue_date" in data:
            issue_date = data["issue_date"]
            if isinstance(issue_date, str):
                issue_date = datetime.fromisoformat(issue_date).date()
            update_dict["issue_date"] = issue_date

        if "expiration_date" in data:
            expiration_date = data["expiration_date"]
            if isinstance(expiration_date, str):
                expiration_date = datetime.fromisoformat(expiration_date).date()
            update_dict["expiration_date"] = expiration_date

        if "not_before" in data:
            not_before = data["not_before"]
            if isinstance(not_before, str):
                not_before = datetime.fromisoformat(not_before)
            update_dict["not_before"] = not_before

        if "not_after" in data:
            not_after = data["not_after"]
            if isinstance(not_after, str):
                not_after = datetime.fromisoformat(not_after)
            update_dict["not_after"] = not_after

        # Recalculate status if relevant fields changed
        if any(
            field in update_dict
            for field in ["expiration_date", "is_revoked", "renewal_days_before"]
        ):
            expiration_date = update_dict.get(
                "expiration_date", certificate.expiration_date
            )
            renewal_days_before = update_dict.get(
                "renewal_days_before", certificate.renewal_days_before or 30
            )
            is_revoked = update_dict.get("is_revoked", certificate.is_revoked or False)
            update_dict["status"] = calculate_certificate_status(
                expiration_date, renewal_days_before, is_revoked
            )

        if update_dict:
            db(db.certificates.id == id).update(**update_dict)
            db.commit()

        return db.certificates[id]

    certificate = await run_in_threadpool(update)

    if not certificate:
        return ApiResponse.not_found("Certificate", id)

    cert_dict = certificate.as_dict()
    # Recalculate status based on current date
    if certificate.expiration_date:
        cert_dict["status"] = calculate_certificate_status(
            certificate.expiration_date,
            certificate.renewal_days_before or 30,
            certificate.is_revoked or False,
        )

    return ApiResponse.success(cert_dict)


@bp.route("/<int:id>", methods=["DELETE"])
@login_required
@resource_role_required("maintainer")
async def delete_certificate(id: int):
    """Delete a certificate entry."""
    db = current_app.db

    certificate, error = await validate_resource_exists(
        db.certificates, id, "Certificate"
    )
    if error:
        return error

    def delete():
        del db.certificates[id]
        db.commit()

    await run_in_threadpool(delete)

    return ApiResponse.no_content()
