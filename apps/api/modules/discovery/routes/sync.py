"""Sync configuration and management API endpoints.

Provides API endpoints for managing two-way synchronization with external
project management platforms (GitHub, GitLab, Jira, Trello, OpenProject).
"""

# flake8: noqa: E501

import inspect
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from quart import Blueprint, current_app, g, jsonify, request
from quart_cors import route_cors
from quart_schema import validate_response

from apps.api.auth.decorators import admin_required, login_required, require_scope
from apps.api.models.pydantic.sync import (
    SyncConfigEnvelopeResponse,
    SyncConfigListResponse,
    SyncConfigResponse,
    SyncConflictEnvelopeResponse,
    SyncConflictResponse,
)
from apps.api.utils.async_utils import run_in_threadpool
from apps.worker.sync.base import (
    BaseSyncClient,
    ResourceType,
    SyncResult,
    SyncStatus,
)
from apps.worker.sync.conflict_resolver import ConflictResolver
from apps.worker.sync.platforms.github_client import (
    GitHubSyncClient,
    load_github_app_private_key,
    parse_installation_id,
    resolve_github_auth_mode,
)
from apps.worker.sync.platforms.gitlab_client import GitLabSyncClient
from apps.worker.sync.platforms.jira_client import JiraSyncClient
from apps.worker.sync.platforms.openproject_client import OpenProjectSyncClient
from apps.worker.sync.platforms.trello_client import TrelloSyncClient
from apps.worker.sync.webhook_handler import WebhookHandler
from shared.redaction import CREDENTIAL_KEYS, REDACTED_PLACEHOLDER

logger = logging.getLogger(__name__)

bp = Blueprint("sync", __name__, url_prefix="/api/v1/sync")

SUPPORTED_PLATFORMS = frozenset({"github", "jira", "gitlab", "trello", "openproject"})
SUPPORTED_RESOURCE_TYPES = ("project", "milestone", "label", "issue")
VALID_SYNC_DIRECTIONS = frozenset({"pull", "push", "bidirectional"})


def _is_blank_or_redacted(value: Any) -> bool:
    """Return True if a field value is empty, None, or the redaction sentinel."""
    if value is None:
        return True
    if isinstance(value, str):
        stripped = value.strip()
        return not stripped or stripped == REDACTED_PLACEHOLDER
    return False


def _merge_config_json(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """Merge incoming config_json while preserving stored credentials."""
    merged = dict(incoming)
    existing_dict = existing if isinstance(existing, dict) else {}

    for key, prev_val in existing_dict.items():
        if key.lower() in CREDENTIAL_KEYS and _is_blank_or_redacted(merged.get(key)):
            merged[key] = prev_val

    return merged


def _validate_platform_config(
    platform: str,
    config_json: dict[str, Any],
) -> str | None:
    """Validate platform-specific fields inside ``config_json``."""
    if not isinstance(config_json, dict):
        return "config_json must be a JSON object"

    resource_types = config_json.get("resource_types")
    if resource_types is not None:
        if not isinstance(resource_types, list) or not all(
            isinstance(item, str) and item in SUPPORTED_RESOURCE_TYPES
            for item in resource_types
        ):
            return "config_json.resource_types must contain only: " + ", ".join(
                SUPPORTED_RESOURCE_TYPES
            )

    direction = config_json.get("direction")
    if direction is not None and (
        not isinstance(direction, str) or direction.lower() not in VALID_SYNC_DIRECTIONS
    ):
        return "config_json.direction must be one of: pull, push, bidirectional"

    if platform == "github":
        owner = str(
            config_json.get("owner") or config_json.get("org_name") or ""
        ).strip()
        repo_name = str(config_json.get("repo_name") or "").strip()
        if not owner or not repo_name:
            return "GitHub config_json requires owner (or org_name) and repo_name"

        try:
            auth_mode = resolve_github_auth_mode(config_json)
        except ValueError as exc:
            return str(exc)

        if auth_mode == "github_app":
            raw_app_id = config_json.get("app_id")
            if (
                raw_app_id is None
                or isinstance(raw_app_id, bool)
                or (isinstance(raw_app_id, int) and raw_app_id <= 0)
                or not str(raw_app_id).strip()
            ):
                return "GitHub App config_json requires a non-empty app_id"

            if parse_installation_id(config_json.get("installation_id")) is None:
                return (
                    "GitHub App config_json requires a positive integer installation_id"
                )

            raw_private_key = config_json.get("private_key")
            if _is_blank_or_redacted(raw_private_key):
                return "GitHub App config_json requires a valid PEM private_key"
            try:
                load_github_app_private_key(raw_private_key)
            except ValueError as exc:
                return str(exc)
        else:
            api_token = config_json.get("api_token")
            if _is_blank_or_redacted(api_token) or not str(api_token).strip():
                return (
                    "GitHub PAT config_json requires api_token, owner (or org_name), "
                    "and repo_name"
                )

    elif platform == "jira":
        api_token = str(config_json.get("api_token") or "").strip()
        email = str(config_json.get("email") or "").strip()
        jira_url = str(config_json.get("jira_url") or "").strip()
        project_key = str(config_json.get("project_key") or "").strip()
        if not api_token or not email or not jira_url or not project_key:
            return (
                "Jira config_json requires api_token, email, jira_url, and project_key"
            )
        label_mode = config_json.get("label_mode")
        if label_mode is not None and (
            not isinstance(label_mode, str)
            or label_mode.lower() not in {"labels", "components"}
        ):
            return "Jira config_json.label_mode must be 'labels' or 'components'"

    return None


def _build_sync_client(
    platform: str,
    config_json: dict[str, Any] | None,
    db: Any,
    sync_config_id: int,
) -> BaseSyncClient:
    """Instantiate the sync client matching ``platform``."""
    cfg = dict(config_json or {})
    clients: dict[str, type[BaseSyncClient]] = {
        "github": GitHubSyncClient,
        "jira": JiraSyncClient,
        "gitlab": GitLabSyncClient,
        "trello": TrelloSyncClient,
        "openproject": OpenProjectSyncClient,
    }
    client_cls = clients.get(platform.lower())
    if not client_cls:
        raise ValueError(f"Unsupported sync platform: {platform}")
    return client_cls(cfg, db, sync_config_id, logger)


def _serialize_sync_result(result: SyncResult) -> dict[str, Any]:
    """Convert a ``SyncResult`` into a JSON-serializable summary."""
    return {
        "status": result.status.value,
        "items_synced": result.items_synced,
        "items_failed": result.items_failed,
        "errors": list(result.errors),
        "conflicts": list(result.conflicts),
        "metadata": dict(result.metadata),
    }


@bp.route("/configs", methods=["GET"])
@route_cors()
@login_required
@require_scope("discovery:read")
@validate_response(SyncConfigListResponse)
def list_sync_configs():
    """List all sync configurations."""
    db = current_app.db

    rows = db(db.sync_configs.id > 0).select(orderby=db.sync_configs.id)

    return SyncConfigListResponse(
        configs=[SyncConfigResponse.from_row(row) for row in rows]
    ), 200


@bp.route("/configs", methods=["POST"])
@route_cors()
@login_required
@require_scope("discovery:admin")
@admin_required
@validate_response(SyncConfigEnvelopeResponse, status_code=201)
async def create_sync_config():
    """Create a new sync configuration."""
    db = current_app.db
    data = await request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON request body"}), 400

    name = str(data.get("name") or "").strip()
    platform = str(data.get("platform") or "").strip().lower()
    if not name or not platform:
        return jsonify({"error": "Missing required fields: name, platform"}), 400

    if platform not in SUPPORTED_PLATFORMS:
        return (
            jsonify(
                {
                    "error": f"Unsupported platform '{platform}'. Supported: "
                    + ", ".join(sorted(SUPPORTED_PLATFORMS))
                }
            ),
            400,
        )

    config_json = data.get("config_json")
    if config_json is None:
        config_json = {}
    config_error = _validate_platform_config(platform, config_json)
    if config_error:
        return jsonify({"error": config_error}), 400

    webhook_secret = data.get("webhook_secret")
    if isinstance(webhook_secret, str):
        webhook_secret = webhook_secret.strip() or None

    def inner():
        existing = db(db.sync_configs.name == name).select(limitby=(0, 1)).first()
        if existing:
            return None, f"Sync configuration '{name}' already exists", 409

        now = datetime.now(UTC)
        config_id = db.sync_configs.insert(
            name=name,
            platform=platform,
            enabled=data.get("enabled", True),
            sync_interval=data.get("sync_interval", 300),
            batch_fallback_enabled=data.get("batch_fallback_enabled", True),
            batch_size=data.get("batch_size", 100),
            two_way_create=data.get("two_way_create", False),
            webhook_enabled=data.get("webhook_enabled", True),
            webhook_secret=webhook_secret,
            config_json=config_json,
            created_at=now,
            updated_at=now,
        )
        db.commit()
        # sync_configs is a global, admin-only platform-integration config
        # table (no tenant_id/organization_id column) — gated by
        # @admin_required, not per-tenant data.
        config = db.sync_configs[config_id]  # tenant-scope-exempt: see above
        return config, None, None

    config_row, error, status = await run_in_threadpool(inner)
    if error:
        return jsonify({"error": error}), status
    return SyncConfigEnvelopeResponse(
        config=SyncConfigResponse.from_row(config_row)
    ), 201


@bp.route("/configs/<int:config_id>", methods=["GET"])
@route_cors()
@login_required
@require_scope("discovery:read")
@validate_response(SyncConfigEnvelopeResponse)
def get_sync_config(config_id):
    """Get sync configuration details."""
    db = current_app.db

    config = db.sync_configs[config_id]  # tenant-scope-exempt: global admin config

    if not config:
        return jsonify({"error": "Config not found"}), 404

    return SyncConfigEnvelopeResponse(config=SyncConfigResponse.from_row(config)), 200


@bp.route("/configs/<int:config_id>", methods=["PATCH"])
@route_cors()
@login_required
@require_scope("discovery:admin")
@admin_required
@validate_response(SyncConfigEnvelopeResponse)
async def update_sync_config(config_id):
    """Update sync configuration while preserving redacted/omitted secrets."""
    db = current_app.db
    data = await request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON request body"}), 400

    def inner():
        config = db.sync_configs[config_id]  # tenant-scope-exempt: global admin config
        if not config:
            return None, "Config not found", 404

        allowed_fields = {
            "name",
            "enabled",
            "sync_interval",
            "batch_fallback_enabled",
            "batch_size",
            "two_way_create",
            "webhook_enabled",
            "webhook_secret",
            "config_json",
        }

        update_data: dict[str, Any] = {}
        for key, value in data.items():
            if key not in allowed_fields:
                continue
            if key == "name":
                trimmed_name = str(value or "").strip()
                if not trimmed_name:
                    return None, "Config name cannot be empty", 400
                dup = (
                    db(
                        (db.sync_configs.name == trimmed_name)
                        & (db.sync_configs.id != config_id)
                    )
                    .select(limitby=(0, 1))
                    .first()
                )
                if dup:
                    return (
                        None,
                        f"Sync configuration '{trimmed_name}' already exists",
                        409,
                    )
                update_data["name"] = trimmed_name
            elif key == "webhook_secret":
                if not _is_blank_or_redacted(value):
                    update_data["webhook_secret"] = str(value).strip()
            elif key == "config_json":
                if not isinstance(value, dict):
                    return None, "config_json must be a JSON object", 400
                merged_cfg = _merge_config_json(config.config_json, value)
                cfg_error = _validate_platform_config(config.platform, merged_cfg)
                if cfg_error:
                    return None, cfg_error, 400
                update_data["config_json"] = merged_cfg
            else:
                update_data[key] = value

        if data.get("clear_webhook_secret") is True:
            update_data["webhook_secret"] = None

        update_data["updated_at"] = datetime.now(UTC)
        db(db.sync_configs.id == config_id).update(**update_data)
        db.commit()

        updated_config = db.sync_configs[config_id]  # tenant-scope-exempt
        return updated_config, None, None

    config_row, error, status = await run_in_threadpool(inner)
    if error:
        return jsonify({"error": error}), status
    return SyncConfigEnvelopeResponse(
        config=SyncConfigResponse.from_row(config_row)
    ), 200


@bp.route("/configs/<int:config_id>", methods=["DELETE"])
@route_cors()
@login_required
@require_scope("discovery:admin")
@admin_required
async def delete_sync_config(config_id):
    """Delete sync configuration and dependent history/mappings/conflicts."""
    db = current_app.db

    def inner():
        config = db.sync_configs[config_id]  # tenant-scope-exempt: global admin config
        if not config:
            return None, "Config not found", 404

        mapping_rows = db(db.sync_mappings.sync_config_id == config_id).select(
            db.sync_mappings.id
        )
        mapping_ids = [row.id for row in mapping_rows]
        if mapping_ids:
            db(db.sync_conflicts.mapping_id.belongs(mapping_ids)).delete()
        db(db.sync_mappings.sync_config_id == config_id).delete()
        db(db.sync_history.sync_config_id == config_id).delete()
        db(db.sync_configs.id == config_id).delete()
        db.commit()
        return {"message": "Config deleted"}, None, None

    result, error, status = await run_in_threadpool(inner)
    if error:
        return jsonify({"error": error}), status
    return jsonify(result), 200


@bp.route("/configs/<int:config_id>/test", methods=["POST"])
@route_cors()
@login_required
@require_scope("discovery:admin")
@admin_required
async def test_sync_config(config_id):
    """Test connectivity and authentication for a sync configuration."""
    db = current_app.db

    def inner():
        config = db.sync_configs[config_id]  # tenant-scope-exempt: global admin config
        if not config:
            return None, "Config not found", 404

        try:
            client = _build_sync_client(
                config.platform, config.config_json, db, config_id
            )
        except ValueError as exc:
            return None, str(exc), 400

        try:
            detail = client.test_connection_detail()
            return detail, None, 200
        finally:
            client.close()

    detail, error, status = await run_in_threadpool(inner)
    if error:
        return jsonify({"error": error}), status
    return jsonify(detail), 200


@bp.route("/configs/<int:config_id>/trigger", methods=["POST"])
@route_cors()
@login_required
@require_scope("discovery:admin")
@admin_required
async def trigger_sync_config(config_id):
    """Manually trigger batch synchronization for a configuration."""
    db = current_app.db
    data = await request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON request body"}), 400

    req_resource_types = data.get("resource_types")
    if req_resource_types is not None:
        if not isinstance(req_resource_types, list) or not all(
            isinstance(rt, str) and rt in SUPPORTED_RESOURCE_TYPES
            for rt in req_resource_types
        ):
            return (
                jsonify(
                    {
                        "error": "resource_types must contain only: "
                        + ", ".join(SUPPORTED_RESOURCE_TYPES)
                    }
                ),
                400,
            )

    req_direction = data.get("direction")
    if req_direction is not None and (
        not isinstance(req_direction, str)
        or req_direction.lower() not in VALID_SYNC_DIRECTIONS
    ):
        return (
            jsonify({"error": "direction must be one of: pull, push, bidirectional"}),
            400,
        )

    since_dt: datetime | None = None
    since_raw = data.get("since")
    if since_raw:
        try:
            since_dt = datetime.fromisoformat(str(since_raw).replace("Z", "+00:00"))
        except ValueError:
            return jsonify({"error": "Invalid ISO datetime for 'since'"}), 400

    def inner():
        config = db.sync_configs[config_id]  # tenant-scope-exempt: global admin config
        if not config:
            return None, "Config not found", 404

        cfg = dict(config.config_json or {})
        resource_types = list(
            req_resource_types
            or cfg.get("resource_types")
            or list(SUPPORTED_RESOURCE_TYPES)
        )
        direction = (
            req_direction
            or cfg.get("direction")
            or ("bidirectional" if config.two_way_create else "pull")
        ).lower()

        try:
            client = _build_sync_client(config.platform, cfg, db, config_id)
        except ValueError as exc:
            return None, str(exc), 400

        started_at = datetime.now(UTC)
        correlation_id = str(uuid4())
        results_by_type: dict[str, Any] = {}
        errors: list[str] = []
        total_synced = 0
        total_failed = 0

        try:
            if not client.validate_config():
                return (
                    None,
                    f"Sync configuration '{config.name}' is missing required settings",
                    400,
                )

            supports_direction = (
                "direction" in inspect.signature(client.batch_sync).parameters
            )
            for rt_name in resource_types:
                rt_enum = ResourceType(rt_name)
                if supports_direction:
                    result = client.batch_sync(
                        rt_enum, since=since_dt, direction=direction
                    )
                else:
                    result = client.batch_sync(rt_enum, since=since_dt)

                results_by_type[rt_name] = _serialize_sync_result(result)
                total_synced += result.items_synced
                total_failed += result.items_failed
                errors.extend(result.errors)
        finally:
            client.close()

        completed_at = datetime.now(UTC)
        overall_success = total_failed == 0 and not errors

        db.sync_history.insert(
            sync_config_id=config_id,
            correlation_id=correlation_id,
            sync_type="manual",
            items_synced=total_synced,
            items_failed=total_failed,
            started_at=started_at,
            completed_at=completed_at,
            success=overall_success,
            error_message="; ".join(errors) if errors else None,
            sync_metadata={
                "direction": direction,
                "resource_types": resource_types,
                "results": results_by_type,
            },
        )
        db(db.sync_configs.id == config_id).update(
            last_sync_at=completed_at,
            last_batch_sync_at=completed_at,
            updated_at=completed_at,
        )
        db.commit()

        return (
            {
                "sync_config_id": config_id,
                "platform": config.platform,
                "correlation_id": correlation_id,
                "direction": direction,
                "resource_types": resource_types,
                "success": overall_success,
                "items_synced": total_synced,
                "items_failed": total_failed,
                "errors": errors,
                "results": results_by_type,
            },
            None,
            200,
        )

    payload, error, status = await run_in_threadpool(inner)
    if error:
        return jsonify({"error": error}), status
    return jsonify(payload), status


@bp.route("/webhooks/<platform>/<int:config_id>", methods=["POST"])
@route_cors()
async def receive_sync_webhook(platform, config_id):
    """Public platform webhook receiver protected by HMAC/token signature."""
    db = current_app.db
    platform_norm = str(platform or "").strip().lower()
    if platform_norm not in SUPPORTED_PLATFORMS:
        return jsonify({"error": f"Unsupported platform: {platform_norm}"}), 400

    raw_payload = await request.get_data()
    headers = {k: v for k, v in request.headers.items()}

    def inner():
        config = db.sync_configs[config_id]  # tenant-scope-exempt: global admin config
        if not config:
            return None, "Config not found", 404

        if str(config.platform or "").strip().lower() != platform_norm:
            return None, "Platform mismatch for sync configuration", 400

        if (
            not config.enabled
            or not config.webhook_enabled
            or not config.webhook_secret
        ):
            return (
                None,
                "Webhook receiver is disabled or missing webhook secret",
                401,
            )

        client = _build_sync_client(platform_norm, config.config_json, db, config_id)
        started_at = datetime.now(UTC)
        correlation_id = str(uuid4())

        try:
            handler = WebhookHandler(
                platform=platform_norm,
                secret=config.webhook_secret,
                sync_client=client,
                conflict_resolver=ConflictResolver(logger),
                logger=logger,
                require_secret=True,
            )
            result = handler.handle_webhook(headers, raw_payload)
        finally:
            client.close()

        if result.status == SyncStatus.FAILED and any(
            "Invalid webhook signature" in err for err in result.errors
        ):
            return None, "Invalid webhook signature", 401

        if result.status == SyncStatus.FAILED and any(
            "Invalid JSON payload" in err for err in result.errors
        ):
            return None, result.errors[0], 400

        completed_at = datetime.now(UTC)
        db.sync_history.insert(
            sync_config_id=config_id,
            correlation_id=correlation_id,
            sync_type="webhook",
            items_synced=result.items_synced,
            items_failed=result.items_failed,
            started_at=started_at,
            completed_at=completed_at,
            success=result.is_success,
            error_message="; ".join(result.errors) if result.errors else None,
            sync_metadata=_serialize_sync_result(result),
        )
        db(db.sync_configs.id == config_id).update(
            last_sync_at=completed_at,
            updated_at=completed_at,
        )
        db.commit()

        status_code = 200 if result.is_success else 500
        return (
            {
                "correlation_id": correlation_id,
                **_serialize_sync_result(result),
            },
            None,
            status_code,
        )

    payload, error, status = await run_in_threadpool(inner)
    if error:
        return jsonify({"error": error}), status
    return jsonify(payload), status


@bp.route("/history", methods=["GET"])
@route_cors()
@login_required
@require_scope("discovery:read")
def list_sync_history():
    """List sync history with pagination."""
    db = current_app.db

    page = max(1, request.args.get("page", 1, type=int) or 1)
    per_page = max(1, min(request.args.get("per_page", 50, type=int) or 50, 200))
    offset = (page - 1) * per_page

    config_id = request.args.get("config_id", type=int)
    sync_type = request.args.get("sync_type")

    query = db.sync_history.id > 0
    if config_id:
        query &= db.sync_history.sync_config_id == config_id
    if sync_type:
        query &= db.sync_history.sync_type == sync_type

    total = db(query).count()
    history = (
        db(query)
        .select(
            orderby=~db.sync_history.started_at,
            limitby=(offset, offset + per_page),
        )
        .as_list()
    )

    return (
        jsonify(
            {
                "history": history,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": total,
                    "pages": (total + per_page - 1) // per_page,
                },
            }
        ),
        200,
    )


@bp.route("/conflicts", methods=["GET"])
@route_cors()
@login_required
@require_scope("discovery:read")
def list_sync_conflicts():
    """List sync conflicts."""
    db = current_app.db

    resolved = request.args.get("resolved", "false").lower() == "true"
    config_id = request.args.get("config_id", type=int)

    query = db.sync_conflicts.resolved == resolved
    if config_id:
        mapping_rows = db(db.sync_mappings.sync_config_id == config_id).select(
            db.sync_mappings.id
        )
        mapping_ids = [row.id for row in mapping_rows]
        if not mapping_ids:
            return jsonify({"conflicts": []}), 200
        query &= db.sync_conflicts.mapping_id.belongs(mapping_ids)

    conflicts = db(query).select(orderby=~db.sync_conflicts.created_at).as_list()

    return jsonify({"conflicts": conflicts}), 200


@bp.route("/conflicts/<int:conflict_id>/resolve", methods=["POST"])
@route_cors()
@login_required
@require_scope("discovery:admin")
@admin_required
@validate_response(SyncConflictEnvelopeResponse)
async def resolve_conflict(conflict_id):
    """Resolve a sync conflict manually."""
    db = current_app.db
    data = await request.get_json(silent=True) or {}
    user_id = g.current_user.id

    def inner():
        # sync_conflicts has no tenant_id/organization_id column -- it's
        # tied to sync_mappings/sync_configs, both global admin-only
        # platform-integration config (see create_sync_config)
        conflict = db.sync_conflicts[conflict_id]  # tenant-scope-exempt: see above
        if not conflict:
            return None, "Conflict not found", 404

        strategy = (
            data.get("resolution_strategy", "manual")
            if isinstance(data, dict)
            else "manual"
        )
        db(db.sync_conflicts.id == conflict_id).update(
            resolved=True,
            resolved_at=datetime.now(UTC),
            resolved_by_id=user_id,
            resolution_strategy=strategy,
        )
        db.commit()

        updated_conflict = db.sync_conflicts[conflict_id]  # tenant-scope-exempt
        return updated_conflict, None, None

    conflict_row, error, status = await run_in_threadpool(inner)
    if error:
        return jsonify({"error": error}), status
    conflict_dict = (
        conflict_row.as_dict() if hasattr(conflict_row, "as_dict") else conflict_row
    )
    return SyncConflictEnvelopeResponse(
        conflict=SyncConflictResponse.model_validate(conflict_dict)
    ), 200


@bp.route("/mappings", methods=["GET"])
@route_cors()
@login_required
@require_scope("discovery:read")
def list_sync_mappings():
    """List sync mappings."""
    db = current_app.db

    config_id = request.args.get("config_id", type=int)
    elder_type = request.args.get("elder_type")

    query = db.sync_mappings.id > 0
    if config_id:
        query &= db.sync_mappings.sync_config_id == config_id
    if elder_type:
        query &= db.sync_mappings.elder_type == elder_type

    mappings = db(query).select(orderby=~db.sync_mappings.id).as_list()

    return jsonify({"mappings": mappings}), 200


@bp.route("/status", methods=["GET"])
@route_cors()
@login_required
@require_scope("discovery:read")
def sync_status():
    """Get overall sync status summary."""
    db = current_app.db

    total_configs = db(db.sync_configs.id > 0).count()
    enabled_configs = db(db.sync_configs.enabled == True).count()  # noqa: E712

    cutoff = datetime.now(UTC) - timedelta(hours=24)

    recent_syncs = db(db.sync_history.started_at > cutoff).count()
    recent_failures = db(
        (db.sync_history.started_at > cutoff) & (db.sync_history.success == False)  # noqa: E712
    ).count()

    unresolved_conflicts = db(
        db.sync_conflicts.resolved == False  # noqa: E712
    ).count()
    total_mappings = db(db.sync_mappings.id > 0).count()

    return (
        jsonify(
            {
                "configs": {
                    "total": total_configs,
                    "enabled": enabled_configs,
                },
                "recent_activity": {
                    "syncs_24h": recent_syncs,
                    "failures_24h": recent_failures,
                },
                "conflicts": {
                    "unresolved": unresolved_conflicts,
                },
                "mappings": {
                    "total": total_mappings,
                },
            }
        ),
        200,
    )
