"""Flows (CI/CD pipelines) CRUD, duplicate, and export endpoints using penguin-dal."""

# flake8: noqa: E501

import logging
import secrets
import uuid
from datetime import UTC, datetime, timezone

from quart import Blueprint, current_app, jsonify, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from shared.utils.village_id import generate_village_id

logger = logging.getLogger(__name__)

bp = Blueprint("flows", __name__)


def _get_tenant_id() -> int:
    """Extract tenant_id from g.claims (populated by before_request)."""
    from quart import g

    claims = getattr(g, "claims", {}) or {}
    tenant_str = claims.get("tenant", "")
    if not tenant_str:
        return None
    try:
        return int(tenant_str)
    except (ValueError, TypeError):
        return None


def _get_identity_id() -> int:
    """Extract identity_id from g.claims."""
    from quart import g

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")
    if identity_id:
        try:
            return int(identity_id)
        except (ValueError, TypeError):
            pass
    return None


def _serialize_pipeline(db, pipeline, include_stages=False):
    """Serialize pipeline database row to JSON-friendly dict."""
    # Get credential info if exists
    credential_info = None
    if pipeline.credential_id:
        cred = db(db.iceflows_credentials.id == pipeline.credential_id).select().first()
        if cred:
            credential_info = {
                "credential_id": cred.credential_id,
                "name": cred.name,
                "provider": cred.provider,
            }

    result = {
        "id": pipeline.id,
        "flow_id": pipeline.flow_id,
        "village_id": pipeline.village_id,
        "name": pipeline.name,
        "description": pipeline.description or "",
        "repository_url": pipeline.repository_url,
        "repository_provider": pipeline.repository_provider,
        "repository_name": pipeline.repository_name or "",
        "default_branch": pipeline.default_branch or "main",
        "credential": credential_info,
        "gitops_enabled": pipeline.gitops_enabled or False,
        "gitops_repo_url": pipeline.gitops_repo_url or "",
        "gitops_branch": pipeline.gitops_branch or "main",
        "gitops_path": pipeline.gitops_path or "",
        "status": pipeline.status or "draft",
        "is_enabled": pipeline.is_enabled or True,
        "created_by_identity_id": pipeline.created_by_identity_id,
        "tags": pipeline.tags or [],
        "created_at": pipeline.created_at.isoformat() if pipeline.created_at else None,
        "updated_at": pipeline.updated_at.isoformat() if pipeline.updated_at else None,
    }

    if include_stages:
        stages = db(db.iceflows_stages.flow_id == pipeline.id).select(
            orderby=db.iceflows_stages.stage_order
        )
        result["stages"] = [_serialize_stage(db, stage) for stage in stages]
        result["stage_count"] = len(stages)

    return result


def _serialize_stage(db, stage):
    """Serialize stage database row to JSON-friendly dict."""
    return {
        "id": stage.id,
        "stage_id": stage.stage_id,
        "flow_id": stage.flow_id,
        "stage_order": stage.stage_order,
        "branch_name": stage.branch_name,
        "display_name": stage.display_name or stage.branch_name,
        "description": stage.description or "",
        "is_production": stage.is_production or False,
        "auto_promote": stage.auto_promote or False,
        "require_approval": stage.require_approval or True,
        "min_approvers": stage.min_approvers or 1,
        "override_min_approvers": stage.override_min_approvers or 2,
        "day_restrictions": stage.day_restrictions or {},
        "time_restrictions": stage.time_restrictions or {},
        "notification_config": stage.notification_config or {},
        "is_enabled": stage.is_enabled or True,
        "created_at": stage.created_at.isoformat() if stage.created_at else None,
        "updated_at": stage.updated_at.isoformat() if stage.updated_at else None,
    }


@bp.route("", methods=["GET"])
@login_required
@require_scope("flows:read")
async def list_pipelines():
    """List all pipelines for current tenant with pagination and filtering."""
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Capture db in request context BEFORE threadpool
    db = current_app.db

    # Extract pagination and filters BEFORE threadpool
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(100, int(request.args.get("per_page", 20)))
    offset = (page - 1) * per_page
    status = request.args.get("status")
    provider = request.args.get("repository_provider")
    is_enabled = request.args.get("is_enabled")
    tags = request.args.getlist("tags")
    search = request.args.get("search")
    sort_by = request.args.get("sort_by", "updated_at")

    def list_flows():
        # Build query (tenant-scoped)
        query = db.iceflows.tenant_id == tenant_id

        if status:
            query &= db.iceflows.status == status
        if provider:
            query &= db.iceflows.repository_provider == provider
        if is_enabled is not None:
            query &= db.iceflows.is_enabled == (is_enabled.lower() == "true")
        if tags:
            for tag in tags:
                query &= db.iceflows.tags.contains(tag)
        if search:
            query &= db.iceflows.name.contains(search)

        # Determine sort order
        if sort_by == "name":
            orderby = db.iceflows.name
        elif sort_by == "created_at":
            orderby = ~db.iceflows.created_at
        else:  # updated_at (default)
            orderby = ~db.iceflows.updated_at

        # Count total
        total = db(query).count()

        # Execute query with pagination
        flows = db(query).select(orderby=orderby, limitby=(offset, offset + per_page))

        result = [_serialize_pipeline(db, flow) for flow in flows]

        return {
            "data": result,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page,
        }

    data = await run_in_threadpool(list_flows)
    return ApiResponse.success(data)


@bp.route("", methods=["POST"])
@login_required
@require_scope("flows:write")
async def create_pipeline():
    """Create a new pipeline."""
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    if not identity_id:
        return ApiResponse.error("Identity not found", 403)

    data = await request.get_json()
    if not data:
        data = {}

    # Validation
    if not data.get("name"):
        return ApiResponse.error("name is required", 400)
    if not data.get("repository_url"):
        return ApiResponse.error("repository_url is required", 400)

    # Auto-detect repository provider from URL
    repo_url = data["repository_url"].lower()
    provider = data.get("repository_provider")
    if not provider:
        if "github.com" in repo_url:
            provider = "github"
        elif "gitlab.com" in repo_url or "gitlab" in repo_url:
            provider = "gitlab"
        else:
            provider = "github"

    # Mint village_id in request context BEFORE threadpool (synchronous)
    village_id = generate_village_id(tenant_id, current_app.redis_client)

    # Capture db in request context BEFORE threadpool
    db = current_app.db

    def create():
        # Validate credential_id if provided
        credential_db_id = None
        if data.get("credential_id"):
            credential = (
                db(
                    (db.iceflows_credentials.credential_id == data["credential_id"])
                    & (db.iceflows_credentials.tenant_id == tenant_id)
                )
                .select()
                .first()
            )
            if not credential:
                return {"error": "Invalid credential_id"}, 400
            credential_db_id = credential.id

        # Generate flow_id and webhook_secret
        flow_id = str(uuid.uuid4())
        webhook_secret = secrets.token_hex(32)

        now = datetime.now(UTC)

        # Create pipeline record
        db_id = db.iceflows.insert(
            tenant_id=tenant_id,
            village_id=village_id,
            flow_id=flow_id,
            name=data["name"],
            description=data.get("description", ""),
            repository_url=data["repository_url"],
            repository_provider=provider,
            repository_name=data.get("repository_url")
            .split("/")[-1]
            .replace(".git", ""),
            default_branch=data.get("default_branch", "main"),
            credential_id=credential_db_id,
            gitops_enabled=data.get("gitops_enabled", False),
            gitops_repo_url=data.get("gitops_repo_url", ""),
            gitops_branch=data.get("gitops_branch", "main"),
            gitops_path=data.get("gitops_path", ""),
            webhook_secret=webhook_secret,
            status="draft",
            is_enabled=True,
            created_by_identity_id=identity_id,
            tags=data.get("tags", []),
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Fetch and return created pipeline
        pipeline = db(db.iceflows.id == db_id).select().first()
        return _serialize_pipeline(db, pipeline), 201

    result = await run_in_threadpool(create)
    if isinstance(result[0], dict) and "error" in result[0]:
        return ApiResponse.error(result[0]["error"], result[1])
    return result


@bp.route("/<pipeline_id>", methods=["GET"])
@login_required
@require_scope("flows:read")
async def get_pipeline(pipeline_id: str):
    """Get pipeline details with stages and metadata."""
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Capture db in request context BEFORE threadpool
    db = current_app.db

    def get():
        pipeline = (
            db(
                (db.iceflows.id == int(pipeline_id) if pipeline_id.isdigit() else False)
                & (db.iceflows.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not pipeline:
            return None, 404

        return _serialize_pipeline(db, pipeline, include_stages=True), 200

    result = await run_in_threadpool(get)

    if result[1] == 404:
        return ApiResponse.error("Not found", 404)

    return result


@bp.route("/<pipeline_id>", methods=["PUT"])
@login_required
@require_scope("flows:write")
async def update_pipeline(pipeline_id: str):
    """Update pipeline configuration."""
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json()
    if not data:
        data = {}

    # Capture db in request context BEFORE threadpool
    db = current_app.db

    def update():
        pipeline = (
            db(
                (db.iceflows.id == int(pipeline_id) if pipeline_id.isdigit() else False)
                & (db.iceflows.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not pipeline:
            return None, 404

        # Build update dict
        update_data = {"updated_at": datetime.now(UTC)}

        if "name" in data:
            update_data["name"] = data["name"]
        if "description" in data:
            update_data["description"] = data["description"]
        if "repository_url" in data:
            update_data["repository_url"] = data["repository_url"]
        if "repository_provider" in data:
            update_data["repository_provider"] = data["repository_provider"]
        if "default_branch" in data:
            update_data["default_branch"] = data["default_branch"]
        if "credential_id" in data:
            if data["credential_id"]:
                credential = (
                    db(
                        (db.iceflows_credentials.credential_id == data["credential_id"])
                        & (db.iceflows_credentials.tenant_id == tenant_id)
                    )
                    .select()
                    .first()
                )
                if not credential:
                    return {"error": "Invalid credential_id"}, 400
                update_data["credential_id"] = credential.id
            else:
                update_data["credential_id"] = None
        if "gitops_enabled" in data:
            update_data["gitops_enabled"] = data["gitops_enabled"]
        if "gitops_repo_url" in data:
            update_data["gitops_repo_url"] = data["gitops_repo_url"]
        if "gitops_branch" in data:
            update_data["gitops_branch"] = data["gitops_branch"]
        if "gitops_path" in data:
            update_data["gitops_path"] = data["gitops_path"]
        if "status" in data:
            update_data["status"] = data["status"]
        if "tags" in data:
            update_data["tags"] = data["tags"]

        db(db.iceflows.id == pipeline.id).update(**update_data)
        db.commit()

        # Fetch updated pipeline
        updated_pipeline = db(db.iceflows.id == pipeline.id).select().first()

        return _serialize_pipeline(db, updated_pipeline), 200

    result = await run_in_threadpool(update)

    if result[1] == 404:
        return ApiResponse.error("Not found", 404)
    if isinstance(result[0], dict) and "error" in result[0]:
        return ApiResponse.error(result[0]["error"], result[1])

    return result


@bp.route("/<pipeline_id>", methods=["DELETE"])
@login_required
@require_scope("flows:write")
async def delete_pipeline(pipeline_id: str):
    """Delete pipeline and all related resources (cascade)."""
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Capture db in request context BEFORE threadpool
    db = current_app.db

    def delete():
        pipeline = (
            db(
                (db.iceflows.id == int(pipeline_id) if pipeline_id.isdigit() else False)
                & (db.iceflows.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not pipeline:
            return None, 404

        # Delete pipeline (cascade handles stages and their children)
        db(db.iceflows.id == pipeline.id).delete()
        db.commit()

        return {"message": "Pipeline deleted successfully"}, 204

    result = await run_in_threadpool(delete)

    if result[1] == 404:
        return ApiResponse.error("Not found", 404)

    return result[0], result[1]


@bp.route("/<pipeline_id>/enable", methods=["PUT"])
@login_required
@require_scope("flows:write")
async def enable_pipeline(pipeline_id: str):
    """Enable a pipeline."""
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Capture db in request context BEFORE threadpool
    db = current_app.db

    def enable():
        pipeline = (
            db(
                (db.iceflows.id == int(pipeline_id) if pipeline_id.isdigit() else False)
                & (db.iceflows.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not pipeline:
            return None, 404

        db(db.iceflows.id == pipeline.id).update(
            is_enabled=True, updated_at=datetime.now(UTC)
        )
        db.commit()

        updated_pipeline = db(db.iceflows.id == pipeline.id).select().first()

        return _serialize_pipeline(db, updated_pipeline), 200

    result = await run_in_threadpool(enable)

    if result[1] == 404:
        return ApiResponse.error("Not found", 404)

    return result


@bp.route("/<pipeline_id>/disable", methods=["PUT"])
@login_required
@require_scope("flows:write")
async def disable_pipeline(pipeline_id: str):
    """Disable a pipeline."""
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Capture db in request context BEFORE threadpool
    db = current_app.db

    def disable():
        pipeline = (
            db(
                (db.iceflows.id == int(pipeline_id) if pipeline_id.isdigit() else False)
                & (db.iceflows.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not pipeline:
            return None, 404

        db(db.iceflows.id == pipeline.id).update(
            is_enabled=False, updated_at=datetime.now(UTC)
        )
        db.commit()

        updated_pipeline = db(db.iceflows.id == pipeline.id).select().first()

        return _serialize_pipeline(db, updated_pipeline), 200

    result = await run_in_threadpool(disable)

    if result[1] == 404:
        return ApiResponse.error("Not found", 404)

    return result


@bp.route("/<pipeline_id>/duplicate", methods=["POST"])
@login_required
@require_scope("flows:write")
async def duplicate_pipeline(pipeline_id: str):
    """Duplicate a pipeline including all stages."""
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    if not identity_id:
        return ApiResponse.error("Identity not found", 403)

    # Mint village_id for new pipeline BEFORE threadpool (synchronous)
    village_id = generate_village_id(tenant_id, current_app.redis_client)

    # Capture db in request context BEFORE threadpool
    db = current_app.db

    def duplicate():
        original = (
            db(
                (db.iceflows.id == int(pipeline_id) if pipeline_id.isdigit() else False)
                & (db.iceflows.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not original:
            return None, 404

        # Create new pipeline
        new_flow_id = str(uuid.uuid4())
        webhook_secret = secrets.token_hex(32)

        now = datetime.now(UTC)

        db_id = db.iceflows.insert(
            tenant_id=tenant_id,
            village_id=village_id,
            flow_id=new_flow_id,
            name=f"{original.name} (Copy)",
            description=original.description,
            repository_url=original.repository_url,
            repository_provider=original.repository_provider,
            repository_name=original.repository_name,
            default_branch=original.default_branch,
            gitops_enabled=original.gitops_enabled,
            gitops_repo_url=original.gitops_repo_url,
            gitops_branch=original.gitops_branch,
            gitops_path=original.gitops_path,
            webhook_secret=webhook_secret,
            status="draft",
            is_enabled=False,
            created_by_identity_id=identity_id,
            tags=original.tags or [],
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Copy all stages
        original_stages = db(db.iceflows_stages.flow_id == original.id).select(
            orderby=db.iceflows_stages.stage_order
        )

        for stage in original_stages:
            new_stage_id = str(uuid.uuid4())
            stage_db_id = db.iceflows_stages.insert(
                tenant_id=tenant_id,
                stage_id=new_stage_id,
                flow_id=db_id,
                stage_order=stage.stage_order,
                branch_name=stage.branch_name,
                display_name=stage.display_name,
                description=stage.description,
                is_production=stage.is_production,
                auto_promote=stage.auto_promote,
                require_approval=stage.require_approval,
                min_approvers=stage.min_approvers,
                override_min_approvers=stage.override_min_approvers,
                day_restrictions=stage.day_restrictions,
                time_restrictions=stage.time_restrictions,
                notification_config=stage.notification_config,
                is_enabled=stage.is_enabled,
                created_at=now,
                updated_at=now,
            )
            db.commit()

            # Copy approvers
            approvers = db(db.iceflows_stage_approvers.stage_id == stage.id).select()
            for approver in approvers:
                db.iceflows_stage_approvers.insert(
                    tenant_id=tenant_id,
                    approver_id=str(uuid.uuid4()),
                    stage_id=stage_db_id,
                    identity_id=approver.identity_id,
                    group_id=approver.group_id,
                    role=approver.role,
                    can_override=approver.can_override,
                    created_at=now,
                    updated_at=now,
                )
            db.commit()

            # Copy tests
            tests = db(db.iceflows_stage_tests.stage_id == stage.id).select()
            for test in tests:
                db.iceflows_stage_tests.insert(
                    tenant_id=tenant_id,
                    test_id=str(uuid.uuid4()),
                    stage_id=stage_db_id,
                    name=test.name,
                    test_type=test.test_type,
                    path_mode=test.path_mode,
                    centralized_path=test.centralized_path,
                    repo_relative_path=test.repo_relative_path,
                    command=test.command,
                    timeout_seconds=test.timeout_seconds,
                    is_blocking=test.is_blocking,
                    is_required=test.is_required,
                    execution_order=test.execution_order,
                    env_vars=test.env_vars,
                    created_at=now,
                    updated_at=now,
                )
            db.commit()

            # Copy calls
            calls = db(db.iceflows_stage_calls.stage_id == stage.id).select()
            for call in calls:
                db.iceflows_stage_calls.insert(
                    tenant_id=tenant_id,
                    call_id=str(uuid.uuid4()),
                    stage_id=stage_db_id,
                    name=call.name,
                    call_type=call.call_type,
                    target_id=call.target_id,
                    trigger_on=call.trigger_on,
                    input_template=call.input_template,
                    timeout_seconds=call.timeout_seconds,
                    is_blocking=call.is_blocking,
                    retry_count=call.retry_count,
                    execution_order=call.execution_order,
                    created_at=now,
                    updated_at=now,
                )
            db.commit()

        new_pipeline = db(db.iceflows.id == db_id).select().first()

        return _serialize_pipeline(db, new_pipeline, include_stages=True), 201

    result = await run_in_threadpool(duplicate)

    if result[1] == 404:
        return ApiResponse.error("Not found", 404)

    return result
