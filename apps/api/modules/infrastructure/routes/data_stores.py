"""Data Stores tracking management API endpoints for Elder using PyDAL with async/await and shared helpers."""

# flake8: noqa: E501

from datetime import UTC, datetime, timezone

from quart import Blueprint, current_app, request
from quart_schema import validate_response

from apps.api.auth.decorators import (
    login_required,
    require_scope,
    resource_role_required,
)
from apps.api.models.pydantic.data_store import (
    DataStoreLabelAssignmentResponse,
    DataStoreLabelListResponse,
    DataStoreLabelResponse,
    DataStoreListResponse,
    DataStoreResponse,
)
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams
from apps.api.utils.tenant_scoping import get_current_tenant_id, get_tenant_scoped
from apps.api.utils.validation_helpers import (
    validate_enum_value,
    validate_json_body,
    validate_organization_and_get_tenant,
    validate_required_fields,
    validate_resource_exists,
)

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

PRESEED_DATA_LABELS = (
    ("PHI", "#a855f7", "Protected Health Information (HIPAA)"),
    ("PII", "#3b82f6", "Personally Identifiable Information"),
    ("FedRAMP", "#06b6d4", "Federal Risk and Authorization Management Program"),
    ("NYDFS", "#f59e0b", "NYDFS 23 NYCRR 500 Cybersecurity Regulation"),
    ("SOX", "#10b981", "Sarbanes-Oxley Act compliance data"),
    ("PCI", "#ec4899", "Payment Card Industry Data Security Standard"),
)


def _ensure_preseeded_data_labels(db):
    """Idempotently ensure default compliance/sensitivity data labels exist."""
    existing_rows = db(db.issue_labels.id > 0).select()
    by_name = {
        (row.name or "").strip().lower(): row for row in existing_rows if row.name
    }
    now = datetime.now(UTC)
    created = False
    for name, color, description in PRESEED_DATA_LABELS:
        if name.lower() not in by_name:
            label_id = db.issue_labels.insert(
                name=name,
                color=color,
                description=description,
                created_at=now,
                updated_at=now,
            )
            by_name[name.lower()] = db.issue_labels[label_id]  # tenant-scope-exempt
            created = True
    if created:
        db.commit()
    return by_name


def _sync_data_store_labels(db, data_store_id: int, label_ids: list[int]) -> list:
    """Replace a data store's label assignments with the validated label_ids."""
    unique_ids = list(dict.fromkeys(int(lid) for lid in label_ids))
    valid_labels = []
    if unique_ids:
        rows = db(db.issue_labels.id.belongs(unique_ids)).select()
        valid_by_id = {r.id: r for r in rows}
        valid_labels = [valid_by_id[lid] for lid in unique_ids if lid in valid_by_id]

    db(db.data_store_labels.data_store_id == data_store_id).delete()
    now = datetime.now(UTC)
    for label in valid_labels:
        db.data_store_labels.insert(
            data_store_id=data_store_id,
            label_id=label.id,
            created_at=now,
        )
    return valid_labels


def _hydrate_data_store_labels(db, rows, labels_by_name=None):
    """Load DataStoreLabelResponse lists for a batch of data_store rows."""
    if not rows:
        return {}
    store_ids = [r.id for r in rows]
    links = db(db.data_store_labels.data_store_id.belongs(store_ids)).select()
    label_ids = list({link.label_id for link in links})
    labels_by_id = {}
    if label_ids:
        for lbl in db(db.issue_labels.id.belongs(label_ids)).select():
            labels_by_id[lbl.id] = lbl

    result: dict[int, list[DataStoreLabelResponse]] = {sid: [] for sid in store_ids}
    seen_per_store: dict[int, set[int]] = {sid: set() for sid in store_ids}
    for link in links:
        lbl = labels_by_id.get(link.label_id)
        if lbl and lbl.id not in seen_per_store[link.data_store_id]:
            result[link.data_store_id].append(
                DataStoreLabelResponse.model_validate(lbl)
            )
            seen_per_store[link.data_store_id].add(lbl.id)

    # Convert legacy boolean sensitivity flags (contains_pii/phi/pci) into labels
    if labels_by_name:
        flag_to_label = (
            ("contains_pii", "pii"),
            ("contains_phi", "phi"),
            ("contains_pci", "pci"),
        )
        now = datetime.now(UTC)
        inserted_legacy = False
        for row in rows:
            for attr, lname in flag_to_label:
                if getattr(row, attr, False):
                    lbl = labels_by_name.get(lname)
                    if lbl and lbl.id not in seen_per_store[row.id]:
                        db.data_store_labels.insert(
                            data_store_id=row.id,
                            label_id=lbl.id,
                            created_at=now,
                        )
                        result[row.id].append(
                            DataStoreLabelResponse.model_validate(lbl)
                        )
                        seen_per_store[row.id].add(lbl.id)
                        inserted_legacy = True
        if inserted_legacy:
            db.commit()

    return result


@bp.route("", methods=["GET"])
@login_required
@require_scope("infrastructure:read")
@validate_response(DataStoreListResponse)
async def list_data_stores():
    """List data stores with optional filtering."""
    db = current_app.db
    pagination = PaginationParams.from_request()

    def get_data_stores():
        labels_by_name = _ensure_preseeded_data_labels(db)
        query = db.data_stores.id > 0

        if request.args.get("search"):
            term = f"%{request.args.get('search', '').strip()}%"
            query &= (db.data_stores.name.ilike(term)) | (
                db.data_stores.description.ilike(term)
            )
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
        label_id_param = request.args.get("label_id", type=int)
        label_name_param = (request.args.get("label") or "").strip().lower()
        if not label_id_param and label_name_param:
            matched_label = labels_by_name.get(label_name_param)
            if matched_label:
                label_id_param = matched_label.id
            else:
                return 0, [], {}
        if label_id_param:
            link_rows = db(db.data_store_labels.label_id == label_id_param).select()
            matching_ids = [lr.data_store_id for lr in link_rows]
            if not matching_ids:
                return 0, [], {}
            query &= db.data_stores.id.belongs(matching_ids)

        total = db(query).count()
        rows = db(query).select(
            orderby=~db.data_stores.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )
        labels_map = _hydrate_data_store_labels(db, rows, labels_by_name)
        return total, rows, labels_map

    total, rows, labels_map = await run_in_threadpool(get_data_stores)
    pages = pagination.calculate_pages(total)

    return DataStoreListResponse(
        items=[
            DataStoreResponse.model_validate(row).model_copy(
                update={"labels": labels_map.get(row.id, [])}
            )
            for row in rows
        ],
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        pages=pages,
    ), 200


@bp.route("", methods=["POST"])
@login_required
@require_scope("infrastructure:write")
@validate_response(DataStoreResponse, status_code=201)
async def create_data_store():
    """Create a new data store entry."""
    db = current_app.db

    data = await request.get_json()
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

    org, tenant_id, error = await validate_organization_and_get_tenant(
        data["organization_id"]
    )
    if error:
        return error

    if data.get("poc_identity_id"):
        identity = await run_in_threadpool(
            lambda: get_tenant_scoped(
                db, db.identities, data["poc_identity_id"], tenant_id
            )
        )
        if not identity:
            return ApiResponse.not_found("POC identity", data["poc_identity_id"])

    def create():
        now = datetime.now(UTC)
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
            created_at=now,
            updated_at=now,
        )
        db.commit()
        if isinstance(data.get("label_ids"), list):
            synced_labels = _sync_data_store_labels(
                db, data_store_id, data["label_ids"]
            )
            synced_names = {(l.name or "").strip().lower() for l in synced_labels}
            db(db.data_stores.id == data_store_id).update(
                contains_pii=("pii" in synced_names),
                contains_phi=("phi" in synced_names),
                contains_pci=("pci" in synced_names),
            )
            db.commit()
        else:
            synced_labels = []
        # tenant_id resolved from the validated organization above (gh-237)
        row = get_tenant_scoped(db, db.data_stores, data_store_id, tenant_id)
        return row, [DataStoreLabelResponse.model_validate(l) for l in synced_labels]

    data_store, synced_labels = await run_in_threadpool(create)
    return DataStoreResponse.model_validate(data_store).model_copy(
        update={"labels": synced_labels}
    ), 201


@bp.route("/<int:id>", methods=["GET"])
@login_required
@require_scope("infrastructure:read")
@validate_response(DataStoreResponse)
async def get_data_store(id: int):
    """Get a single data store entry by ID."""
    db = current_app.db
    tenant_id = get_current_tenant_id()

    # gh-237: 404 on cross-tenant id guesses instead of leaking existence
    data_store = await run_in_threadpool(
        lambda: get_tenant_scoped(db, db.data_stores, id, tenant_id)
    )
    if not data_store:
        return ApiResponse.not_found("Data store", id)

    return DataStoreResponse.model_validate(data_store), 200


@bp.route("/<int:id>", methods=["PUT"])
@login_required
@require_scope("infrastructure:write")
@resource_role_required("maintainer")
@validate_response(DataStoreResponse)
async def update_data_store(id: int):
    """Update a data store entry."""
    db = current_app.db
    # Resolve tenant here — g does not propagate into run_in_threadpool (gh-237)
    tenant_id = get_current_tenant_id()

    data = await request.get_json()
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
        identity = await run_in_threadpool(
            lambda: get_tenant_scoped(
                db, db.identities, data["poc_identity_id"], tenant_id
            )
        )
        if not identity:
            return ApiResponse.not_found("POC identity", data["poc_identity_id"])

    org_tenant_id = None
    if "organization_id" in data:
        org, org_tenant_id, error = await validate_organization_and_get_tenant(
            data["organization_id"]
        )
        if error:
            return error

    def update():
        # gh-237: 404s on cross-tenant id guesses instead of leaking existence
        data_store = get_tenant_scoped(db, db.data_stores, id, tenant_id)
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

        if isinstance(data.get("label_ids"), list):
            synced_labels = _sync_data_store_labels(db, id, data["label_ids"])
            synced_names = {(l.name or "").strip().lower() for l in synced_labels}
            update_dict["contains_pii"] = "pii" in synced_names
            update_dict["contains_phi"] = "phi" in synced_names
            update_dict["contains_pci"] = "pci" in synced_names

        if update_dict:
            db(db.data_stores.id == id).update(**update_dict)
            db.commit()

        row = get_tenant_scoped(db, db.data_stores, id, tenant_id)
        if not row:
            return None, []
        labels_map = _hydrate_data_store_labels(db, [row])
        return row, labels_map.get(row.id, [])

    data_store, store_labels = await run_in_threadpool(update)

    if not data_store:
        return ApiResponse.not_found("Data store", id)

    return DataStoreResponse.model_validate(data_store).model_copy(
        update={"labels": store_labels}
    ), 200


@bp.route("/<int:id>", methods=["DELETE"])
@login_required
@require_scope("infrastructure:write")
@resource_role_required("maintainer")
async def delete_data_store(id: int):
    """Delete a data store entry."""
    db = current_app.db
    tenant_id = get_current_tenant_id()

    def delete():
        # gh-237: verify tenant ownership before delete, not just existence
        data_store = get_tenant_scoped(db, db.data_stores, id, tenant_id)
        if not data_store:
            return False
        del db.data_stores[id]  # tenant-scope-exempt: verified above
        db.commit()
        return True

    deleted = await run_in_threadpool(delete)
    if not deleted:
        return ApiResponse.not_found("Data store", id)

    return ApiResponse.no_content()


@bp.route("/<int:id>/labels", methods=["GET"])
@login_required
@require_scope("infrastructure:read")
@validate_response(DataStoreLabelListResponse)
async def get_data_store_labels(id: int):
    """Get labels for a data store."""
    db = current_app.db
    tenant_id = get_current_tenant_id()

    data_store = await run_in_threadpool(
        lambda: get_tenant_scoped(db, db.data_stores, id, tenant_id)
    )
    if not data_store:
        return ApiResponse.not_found("Data store", id)

    def get_labels():
        rows = db(db.data_store_labels.data_store_id == id).select()
        labels = []
        for row in rows:
            label = db.issue_labels[row.label_id]  # tenant-scope-exempt: global
            if label:
                labels.append(DataStoreLabelResponse.model_validate(label))
        return labels

    labels = await run_in_threadpool(get_labels)
    return DataStoreLabelListResponse(labels=labels), 200


@bp.route("/<int:id>/labels", methods=["POST"])
@login_required
@require_scope("infrastructure:write")
@resource_role_required("viewer")
@validate_response(DataStoreLabelAssignmentResponse, status_code=201)
async def add_data_store_label(id: int):
    """Add a label to a data store."""
    db = current_app.db
    tenant_id = get_current_tenant_id()

    data_store = await run_in_threadpool(
        lambda: get_tenant_scoped(db, db.data_stores, id, tenant_id)
    )
    if not data_store:
        return ApiResponse.not_found("Data store", id)

    data = await request.get_json()
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
        return db.data_store_labels[label_assignment_id]  # tenant-scope-exempt

    assignment = await run_in_threadpool(add_label)

    if assignment is None:
        return ApiResponse.conflict("Label already assigned to this data store")

    return DataStoreLabelAssignmentResponse.model_validate(assignment), 201


@bp.route("/<int:id>/labels/<int:label_id>", methods=["DELETE"])
@login_required
@require_scope("infrastructure:write")
@resource_role_required("maintainer")
async def remove_data_store_label(id: int, label_id: int):
    """Remove a label from a data store."""
    db = current_app.db
    tenant_id = get_current_tenant_id()

    data_store = await run_in_threadpool(
        lambda: get_tenant_scoped(db, db.data_stores, id, tenant_id)
    )
    if not data_store:
        return ApiResponse.not_found("Data store", id)

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

        del db.data_store_labels[assignment.id]  # tenant-scope-exempt: see above
        db.commit()
        return True

    result = await run_in_threadpool(remove_label)

    if result is None:
        return ApiResponse.not_found("Label assignment")

    return ApiResponse.no_content()
