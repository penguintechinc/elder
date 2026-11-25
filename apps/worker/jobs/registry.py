"""Worker job handler registry.

Maps worker task group names to async handler functions.
Handlers receive a JobEnvelope and return a result dict.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC
from typing import Any, Optional

import structlog

from shared.jobbus import JobEnvelope

logger = structlog.get_logger(__name__)

# Type alias for handler functions
JobHandler = Callable[
    [JobEnvelope], Any
]  # Returns dict or None on success, raises on error


async def handle_discovery(envelope: JobEnvelope) -> dict[str, Any]:
    """Handle discovery job from job-bus.

    Invokes the existing DiscoveryExecutor via run_pending() to poll and execute
    pending discovery jobs from the database. Runs the sync executor in a thread pool
    to avoid blocking the event loop.

    Args:
        envelope: JobEnvelope with discovery job details

    Returns:
        Result dict with status and discovery stats

    Raises:
        Exception: If discovery execution fails
    """
    import asyncio

    from apps.worker.discovery.executor import DiscoveryExecutor
    from shared.database.manager import DatabaseManager

    logger.info(
        "discovery_job_received",
        job_id=envelope.job_id,
        job_type=envelope.job_type,
        payload=envelope.payload,
    )

    from apps.worker.config.settings import settings

    if not settings.database_url:
        raise ValueError(
            "DATABASE_URL not configured; discovery jobs require database access"
        )

    def _run_discovery_sync() -> int:
        """Run discovery jobs synchronously in thread pool."""
        db_manager = DatabaseManager(
            primary_url=settings.database_url,
            replica_url=settings.database_read_url,
            pool_size=settings.db_pool_size,
        )
        try:
            executor = DiscoveryExecutor(
                db_write=db_manager.write,
                db_read=db_manager.read,
            )
            executed = executor.run_pending()
            return executed
        finally:
            db_manager.close()

    try:
        # Run sync executor in thread pool to avoid blocking event loop
        executed = await asyncio.to_thread(_run_discovery_sync)

        result = {
            "status": "success",
            "jobs_executed": executed,
            "job_id": envelope.job_id,
        }

        logger.info(
            "discovery_job_completed",
            job_id=envelope.job_id,
            jobs_executed=executed,
        )

        return result

    except Exception as e:
        logger.error(
            "discovery_job_failed",
            job_id=envelope.job_id,
            error=str(e),
            exc_info=True,
        )
        raise


async def handle_sbom_scan(envelope: JobEnvelope) -> dict[str, Any]:
    """Handle SBOM scan job from job-bus (STUB).

    SBOM scan worker logic is not yet ported from Wave-1.
    This is a placeholder that logs receipt and returns not-implemented status.

    Args:
        envelope: JobEnvelope with SBOM scan job details

    Returns:
        Result dict with stub status

    Raises:
        NotImplementedError: Indicates SBOM scan is not yet implemented
    """
    logger.warning(
        "sbom_scan_job_stub",
        job_id=envelope.job_id,
        job_type=envelope.job_type,
        payload=envelope.payload,
        message="SBOM scan worker logic not yet ported — stub handler",
    )

    raise NotImplementedError(
        "SBOM scan worker logic not yet ported from Wave-1; "
        "this handler is a stub for Wave-2 infrastructure testing"
    )


async def handle_helpdesk_sla_breach(envelope: JobEnvelope) -> dict[str, Any]:
    """Handle helpdesk SLA breach check job.

    Finds tickets with breached SLAs and flags them.
    Payload: {} (tenant_id from envelope)

    Args:
        envelope: JobEnvelope with SLA breach check details

    Returns:
        Result dict with status, breached_count, newly_flagged_count

    Raises:
        Exception: If SLA check fails
    """
    from apps.api.modules.helpdesk.worker.sla_breach import check_sla_breaches
    from shared.database.manager import DatabaseManager

    logger.info(
        "helpdesk_sla_breach_job_received",
        job_id=envelope.job_id,
        tenant_id=envelope.tenant_id,
    )

    from apps.worker.config.settings import settings

    if not settings.database_url:
        raise ValueError(
            "DATABASE_URL not configured; SLA check requires database access"
        )

    tenant_id = envelope.tenant_id
    if not tenant_id:
        raise ValueError("Missing tenant_id in envelope")

    def _setup_db():
        db_manager = DatabaseManager(
            primary_url=settings.database_url,
            replica_url=settings.database_read_url,
            pool_size=settings.db_pool_size,
        )
        return db_manager

    db_manager = _setup_db()

    try:
        result = await check_sla_breaches(
            db=db_manager.write,
            tenant_id=tenant_id,
        )

        logger.info(
            "helpdesk_sla_breach_completed",
            job_id=envelope.job_id,
            result=result,
        )

        return result

    except Exception as e:
        logger.error(
            "helpdesk_sla_breach_failed",
            job_id=envelope.job_id,
            error=str(e),
            exc_info=True,
        )
        raise

    finally:
        db_manager.close()


async def handle_streams(envelope: JobEnvelope) -> dict[str, Any]:
    """Handle Streams playbook execution job.

    Loads the playbook, nodes, edges, and execution record from the database,
    runs the PlaybookExecutor, and updates the execution status.

    Args:
        envelope: JobEnvelope with execution_id, playbook_id, tenant_id in payload

    Returns:
        Result dict with status and execution results

    Raises:
        Exception: If execution fails (job remains pending for XAUTOCLAIM reclaim)
    """
    import os
    from datetime import datetime

    from apps.api.modules.registry import resolve_enabled
    from apps.worker.config.settings import settings
    from apps.worker.streams.connectors.registry import discover_connectors
    from apps.worker.streams.executor.node_registry import discover_nodes
    from apps.worker.streams.executor.playbook_executor import PlaybookExecutor

    raw_payload = envelope.payload if isinstance(envelope.payload, dict) else {}
    auth_token = raw_payload.get("auth_token")
    safe_payload = {
        k: ("***REDACTED***" if k in ("auth_token", "_elder_auth_token") else v)
        for k, v in raw_payload.items()
    }

    logger.info(
        "streams_job_received",
        job_id=envelope.job_id,
        payload=safe_payload,
    )

    if not settings.database_url:
        raise ValueError(
            "DATABASE_URL not configured; streams execution requires database access"
        )

    payload = envelope.payload
    execution_id = payload.get("execution_id")
    playbook_id = payload.get("playbook_id")
    tenant_id = envelope.tenant_id or payload.get("tenant_id")

    if not execution_id or not playbook_id or not tenant_id:
        raise ValueError("Missing execution_id, playbook_id, or tenant_id in payload")

    def _load_execution_and_playbook():
        """Sync function to load execution and playbook from DB."""
        from penguin_dal import DAL

        # penguin-dal reflects existing tables (schema owned by Alembic), same
        # construction the API uses in shared/database/__init__.py.
        db = DAL(settings.database_url, pool_size=10, migrate=False)
        try:
            # Load queued execution (scope to tenant)
            execution = (
                db(
                    (db.stream_executions.execution_id == execution_id)
                    & (db.stream_executions.tenant_id == tenant_id)
                )
                .select()
                .first()
            )
            if not execution:
                raise ValueError(
                    f"Execution {execution_id} not found in tenant {tenant_id}"
                )

            # Idempotency: if already terminal, no-op
            if execution.status not in ("pending", "queued"):
                logger.info(
                    "streams_execution_already_terminal",
                    execution_id=execution_id,
                    status=execution.status,
                )
                return execution, None, None, None, None

            # Load playbook
            playbook = (
                db(
                    (db.stream_playbooks.id == playbook_id)
                    & (db.stream_playbooks.tenant_id == tenant_id)
                )
                .select()
                .first()
            )
            if not playbook:
                raise ValueError(
                    f"Playbook {playbook_id} not found in tenant {tenant_id}"
                )

            # Load nodes from normalized table
            nodes = db(
                (db.stream_nodes.playbook_id == playbook_id)
                & (db.stream_nodes.tenant_id == tenant_id)
            ).select()
            nodes_list = [
                {
                    "id": n.node_id,
                    "type": n.node_type,
                    "category": n.node_category,
                    "label": n.label,
                    "config": n.config or {},
                    "data": {
                        "label": n.label,
                        "category": n.node_category,
                        "nodeType": n.node_type,
                        "config": n.config or {},
                    },
                }
                for n in nodes
            ]

            # Load edges from normalized table
            edges = db(
                (db.stream_edges.playbook_id == playbook_id)
                & (db.stream_edges.tenant_id == tenant_id)
            ).select()
            edges_list = [
                {
                    "source": e.source_node_id,
                    "target": e.target_node_id,
                    "sourceHandle": e.source_handle,
                    "targetHandle": e.target_handle,
                }
                for e in edges
            ]

            # Fall back to latest stream_versions.canvas_json when stream_nodes is empty
            if not nodes_list:
                latest_version = (
                    db(
                        (db.stream_versions.playbook_id == playbook_id)
                        & (db.stream_versions.tenant_id == tenant_id)
                    )
                    .select(orderby=~db.stream_versions.version_number, limitby=(0, 1))
                    .first()
                )
                if latest_version:
                    canvas = latest_version.canvas_json or {}
                    raw_nodes = canvas.get("nodes") or latest_version.nodes_json or []
                    raw_edges = canvas.get("edges") or latest_version.edges_json or []
                    for rn in raw_nodes:
                        if not isinstance(rn, dict):
                            continue
                        data_obj = rn.get("data") or {}
                        node_type = (
                            rn.get("nodeType")
                            or data_obj.get("nodeType")
                            or rn.get("type")
                            or ""
                        )
                        node_label = rn.get("label") or data_obj.get("label") or ""
                        node_category = (
                            rn.get("category") or data_obj.get("category") or ""
                        )
                        node_config = rn.get("config") or data_obj.get("config") or {}
                        nodes_list.append(
                            {
                                "id": rn.get("id", ""),
                                "type": node_type,
                                "category": node_category,
                                "label": node_label,
                                "config": node_config,
                                "data": {
                                    "label": node_label,
                                    "category": node_category,
                                    "nodeType": node_type,
                                    "config": node_config,
                                },
                            }
                        )
                    if not edges_list and raw_edges:
                        edges_list = [
                            {
                                "source": re.get("source", ""),
                                "target": re.get("target", ""),
                                "sourceHandle": re.get("sourceHandle"),
                                "targetHandle": re.get("targetHandle"),
                            }
                            for re in raw_edges
                            if isinstance(re, dict)
                        ]

            # Resolve enabled modules for this environment & tenant
            resolved_manifests = resolve_enabled(os.environ)
            enabled_modules: dict[str, bool] = {
                m.name: bool(m.default_enabled) for m in resolved_manifests
            }
            if hasattr(db, "tenant_modules"):
                toggle_rows = db(db.tenant_modules.tenant_id == tenant_id).select()
                for row in toggle_rows:
                    if row.module_name in enabled_modules:
                        enabled_modules[row.module_name] = bool(row.enabled)

            return execution, nodes_list, edges_list, enabled_modules, db

        except Exception:
            raise

    try:
        # Discover nodes at startup (idempotent)
        discover_nodes()

        # Discover connector nodes (idempotent)
        discover_connectors()

        # Load execution, playbook, nodes, edges
        (
            execution,
            nodes_list,
            edges_list,
            enabled_modules,
            db,
        ) = _load_execution_and_playbook()

        if execution.status not in ("pending", "queued"):
            # Already terminal, return cached result
            return {
                "status": "skipped",
                "message": "Execution already terminal",
                "execution_id": execution_id,
            }

        # Update execution to "running"
        now = datetime.now(UTC)
        db(db.stream_executions.id == execution.id).update(
            status="running",
            started_at=now,
            updated_at=now,
        )
        db.commit()

        # Prepare playbook data for executor (auth_token stays strictly in-memory)
        trigger_data = execution.input_json or {}
        exec_global_config: dict[str, Any] = {
            "skip_inactive_branches": True,
            "tenant_id": tenant_id,
            "enabled_modules": enabled_modules,
            "trigger_data": trigger_data,
        }
        if auth_token:
            exec_global_config["_elder_auth_token"] = auth_token

        playbook_data = {
            "nodes": nodes_list,
            "edges": edges_list,
            "config": exec_global_config,
            "trigger_data": trigger_data,
            "trigger_output": trigger_data,
            "tenant_id": tenant_id,
            "enabled_modules": enabled_modules,
        }

        node_type_by_id = {
            n["id"]: (n.get("type") or (n.get("data") or {}).get("nodeType") or "")
            for n in nodes_list
        }

        # Execute playbook
        executor = PlaybookExecutor(
            execution_id=execution_id,
            playbook_id=playbook_id,
            node_timeout_seconds=30.0,
        )
        result = await executor.execute(playbook_data)

        # Update execution with result
        db(db.stream_executions.id == execution.id).update(
            status="success" if result.success else "failed",
            output_json=result.to_dict(),
            error_message=result.error,
            completed_at=datetime.now(UTC),
            duration_ms=int(result.execution_time_ms),
            updated_at=datetime.now(UTC),
        )
        db.commit()

        # Insert stream_node_executions for each node that ran
        for node_id, node_result in result.node_results.items():
            resolved_node_type = (
                getattr(node_result, "node_type", "")
                or node_type_by_id.get(node_id)
                or (
                    node_result.node_id.split("_")[0]
                    if "_" in node_result.node_id
                    else node_result.node_id
                )
            )[:50]
            db.stream_node_executions.insert(
                tenant_id=tenant_id,
                execution_id=execution_id,
                node_id=node_id,
                node_type=resolved_node_type,
                playbook_id=playbook_id,
                status=node_result.status.value,
                input_json={},
                output_json=node_result.to_dict().get("outputs", {}),
                error_message=node_result.error,
                started_at=node_result.started_at,
                completed_at=node_result.completed_at,
                duration_ms=int(node_result.execution_time_ms),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        db.commit()

        logger.info(
            "streams_execution_completed",
            execution_id=execution_id,
            playbook_id=playbook_id,
            success=result.success,
            duration_ms=result.execution_time_ms,
        )

        return {
            "status": "success" if result.success else "failed",
            "execution_id": execution_id,
            "success": result.success,
            "duration_ms": result.execution_time_ms,
            "completed_nodes": result.completed_nodes,
            "failed_nodes": result.failed_nodes,
        }

    except Exception as e:
        logger.error(
            "streams_execution_failed",
            execution_id=execution_id,
            playbook_id=playbook_id,
            error=str(e),
            exc_info=True,
        )
        raise


# Handler registry: group name -> async handler
HANDLER_REGISTRY: dict[str, JobHandler] = {
    "discovery": handle_discovery,
    "sbom_scan": handle_sbom_scan,
    "helpdesk_sla_breach": handle_helpdesk_sla_breach,
    "streams": handle_streams,
}


def get_handler(group: str) -> JobHandler | None:
    """Get handler for a job group.

    Args:
        group: Worker task group name

    Returns:
        Handler function if found, None otherwise
    """
    return HANDLER_REGISTRY.get(group)
