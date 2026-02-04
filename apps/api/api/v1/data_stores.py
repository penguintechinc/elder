"""Data Stores tracking management API endpoints for Elder using PyDAL with async/await and shared helpers."""

# flake8: noqa: E501


from dataclasses import asdict

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

bp = Blueprint("data_stores", __name__)

# Valid storage types - aligned with frontend DataStores.tsx
VALID_STORAGE_TYPES = [
    "database",
    "file_storage",
    "data_warehouse",
    "data_lake",
    "cache",
    "message_queue",
    "search_index",
    "time_series",
    "blob_storage",
    "other",
    # Legacy/cloud-specific types for backwards compatibility
    "s3",
    "gcs",
    "azure_blob",
    "disk",
    "nas",
    "san",
    "hdfs",
]

# Valid data classifications
VALID_DATA_CLASSIFICATIONS = ["public", "internal", "confidential", "restricted"]


@bp.route("", methods=["GET"])
@login_required
async def list_data_stores():
    """List data stores with optional filtering."""
    db = current_app.db
    pagination = PaginationParams.from_request()

    def get_data_stores():
        query = db.data_stores.id > 0

        if request.args.get("organization_id"):
            query &= db.data_stores.organization_id == request.args.get(
                "organization_id", type=int
            )
        if request.args.get("data_classification"):
            query &= db.data_stores.data_classification == request.args.get(
                "data_classification"
            )
        if request.args.get("storage_type"):
            query &= db.data_stores.storage_type == request.args.get("storage_type")
        if request.args.get("location_region"):
            query &= db.data_stores.location_region == request.args.get(
                "location_region"
            )
        if request.args.get("contains_pii") is not None:
            contains_pii = request.args.get("contains_pii", "").lower() == "true"
            query &= db.data_stores.contains_pii == contains_pii
        if request.args.get("contains_phi") is not None:
            contains_phi = request.args.get("contains_phi", "").lower() == "true"
            query &= db.data_stores.contains_phi == contains_phi
        if request.args.get("contains_pci") is not None:
            contains_pci = request.args.get("contains_pci", "").lower() == "true"
            query &= db.data_stores.contains_pci == contains_pci
        if request.args.get("compliance_framework"):
            # Search for compliance framework in JSON array
            query &= db.data_stores.compliance_frameworks.contains(
                request.args.get("compliance_framework")
            )
        if request.args.get("poc_identity_id"):
            query &= db.data_stores.poc_identity_id == request.args.get(
                "poc_identity_id", type=int
            )

        total = db(query).count()
        rows = db(query).select(
            orderby=~db.data_stores.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )
        return total, rows

    total, rows = await run_in_threadpool(get_data_stores)
    pages = pagination.calculate_pages(total)

    response = PaginatedResponse(
        items=[row.as_dict() for row in rows],
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        pages=pages,
    )

    return jsonify(asdict(response)), 200


@bp.route("", methods=["POST"])
@login_required
async def create_data_store():
    """Create a new data store entry."""
    db = current_app.db

    data = request.get_json()
    if error := validate_json_body(data):
        return error

    if error := validate_required_fields(data, ["name", "organization_id"]):
        return error

    if data.get("data_classification"):
        if error := validate_enum_value(
            data["data_classification"],
            VALID_DATA_CLASSIFICATIONS,
            "data_classification",
        ):
            return error

    if data.get("storage_type"):
        if error := validate_enum_value(
            data["storage_type"], VALID_STORAGE_TYPES, "storage_type"
        ):
            return error

    if data.get("poc_identity_id"):
        identity, error = await validate_resource_exists(
            db.identities, data["poc_identity_id"], "POC identity"
        )
        if error:
            return error

    org, tenant_id, error = await validate_organization_and_get_tenant(
        data["organization_id"]
    )
    if error:
        return error

    def create():
        data_store_id = db.data_stores.insert(
            name=data["name"],
            description=data.get("description"),
            organization_id=data["organization_id"],
            tenant_id=tenant_id,
            storage_type=data.get("storage_type", "other"),
            storage_provider=data.get("storage_provider"),
            location_region=data.get("location_region"),
            location_physical=data.get("location_physical"),
            data_classification=data.get("data_classification", "internal"),
            encryption_at_rest=data.get("encryption_at_rest", False),
            encryption_in_transit=data.get("encryption_in_transit", False),
            encryption_key_id=data.get("encryption_key_id"),
            retention_days=data.get("retention_days"),
            backup_enabled=data.get("backup_enabled", False),
            backup_frequency=data.get("backup_frequency"),
            access_control_type=data.get("access_control_type", "private"),
            poc_identity_id=data.get("poc_identity_id"),
            compliance_frameworks=data.get("compliance_frameworks"),
            contains_pii=data.get("contains_pii", False),
            contains_phi=data.get("contains_phi", False),
            contains_pci=data.get("contains_pci", False),
            size_bytes=data.get("size_bytes"),
            metadata=data.get("metadata"),
            is_active=data.get("is_active", True),
        )
        db.commit()
        return db.data_stores[data_store_id]

    data_store = await run_in_threadpool(create)
    return ApiResponse.created(data_store.as_dict())


@bp.route("/<int:id>", methods=["GET"])
@login_required
async def get_data_store(id: int):
    """Get a single data store entry by ID."""
    db = current_app.db

    data_store, error = await validate_resource_exists(db.data_stores, id, "Data store")
    if error:
        return error

    return ApiResponse.success(data_store.as_dict())


@bp.route("/<int:id>", methods=["PUT"])
@login_required
@resource_role_required("maintainer")
async def update_data_store(id: int):
    """Update a data store entry."""
    db = current_app.db

    data = request.get_json()
    if error := validate_json_body(data):
        return error

    if data.get("data_classification"):
        if error := validate_enum_value(
            data["data_classification"],
            VALID_DATA_CLASSIFICATIONS,
            "data_classification",
        ):
            return error

    if data.get("storage_type"):
        if error := validate_enum_value(
            data["storage_type"], VALID_STORAGE_TYPES, "storage_type"
        ):
            return error

    if data.get("poc_identity_id"):
        identity, error = await validate_resource_exists(
            db.identities, data["poc_identity_id"], "POC identity"
        )
        if error:
            return error

    org_tenant_id = None
    if "organization_id" in data:
        org, org_tenant_id, error = await validate_organization_and_get_tenant(
            data["organization_id"]
        )
        if error:
            return error

    def update():
        data_store = db.data_stores[id]
        if not data_store:
            return None

        update_dict = {}
        updateable_fields = [
            "name",
            "description",
            "storage_type",
            "storage_provider",
            "location_region",
            "location_physical",
            "data_classification",
            "encryption_at_rest",
            "encryption_in_transit",
            "encryption_key_id",
            "retention_days",
            "backup_enabled",
            "backup_frequency",
            "access_control_type",
            "poc_identity_id",
            "compliance_frameworks",
            "contains_pii",
            "contains_phi",
            "contains_pci",
            "size_bytes",
            "metadata",
            "is_active",
        ]

        for field in updateable_fields:
            if field in data:
                update_dict[field] = data[field]

        if "organization_id" in data:
            update_dict["organization_id"] = data["organization_id"]
            update_dict["tenant_id"] = org_tenant_id

        if update_dict:
            db(db.data_stores.id == id).update(**update_dict)
            db.commit()

        return db.data_stores[id]

    data_store = await run_in_threadpool(update)

    if not data_store:
        return ApiResponse.not_found("Data store", id)

    return ApiResponse.success(data_store.as_dict())


@bp.route("/<int:id>", methods=["DELETE"])
@login_required
@resource_role_required("maintainer")
async def delete_data_store(id: int):
    """Delete a data store entry."""
    db = current_app.db

    data_store, error = await validate_resource_exists(db.data_stores, id, "Data store")
    if error:
        return error

    def delete():
        del db.data_stores[id]
        db.commit()

    await run_in_threadpool(delete)

    return ApiResponse.no_content()


@bp.route("/<int:id>/labels", methods=["GET"])
@login_required
async def get_data_store_labels(id: int):
    """Get labels for a data store."""
    db = current_app.db

    data_store, error = await validate_resource_exists(db.data_stores, id, "Data store")
    if error:
        return error

    def get_labels():
        rows = db(db.data_store_labels.data_store_id == id).select()
        labels = []
        for row in rows:
            label = db.issue_labels[row.label_id]
            if label:
                labels.append(label.as_dict())
        return labels

    labels = await run_in_threadpool(get_labels)
    return ApiResponse.success({"labels": labels})


@bp.route("/<int:id>/labels", methods=["POST"])
@login_required
@resource_role_required("viewer")
async def add_data_store_label(id: int):
    """Add a label to a data store."""
    db = current_app.db

    data_store, error = await validate_resource_exists(db.data_stores, id, "Data store")
    if error:
        return error

    data = request.get_json()
    if error := validate_json_body(data):
        return error

    if error := validate_required_fields(data, ["label_id"]):
        return error

    label, error = await validate_resource_exists(
        db.issue_labels, data["label_id"], "Label"
    )
    if error:
        return error

    def add_label():
        # Check if label already exists for this data store
        existing = db(
            (db.data_store_labels.data_store_id == id)
            & (db.data_store_labels.label_id == data["label_id"])
        ).select()

        if existing:
            return None  # Already exists

        label_assignment_id = db.data_store_labels.insert(
            data_store_id=id, label_id=data["label_id"]
        )
        db.commit()
        return db.data_store_labels[label_assignment_id]

    assignment = await run_in_threadpool(add_label)

    if assignment is None:
        return ApiResponse.conflict("Label already assigned to this data store")

    return ApiResponse.created(assignment.as_dict())


@bp.route("/<int:id>/labels/<int:label_id>", methods=["DELETE"])
@login_required
@resource_role_required("maintainer")
async def remove_data_store_label(id: int, label_id: int):
    """Remove a label from a data store."""
    db = current_app.db

    data_store, error = await validate_resource_exists(db.data_stores, id, "Data store")
    if error:
        return error

    label, error = await validate_resource_exists(db.issue_labels, label_id, "Label")
    if error:
        return error

    def remove_label():
        assignment = (
            db(
                (db.data_store_labels.data_store_id == id)
                & (db.data_store_labels.label_id == label_id)
            )
            .select()
            .first()
        )

        if not assignment:
            return None

        del db.data_store_labels[assignment.id]
        db.commit()
        return True

    result = await run_in_threadpool(remove_label)

    if result is None:
        return ApiResponse.not_found("Label assignment")

    return ApiResponse.no_content()
