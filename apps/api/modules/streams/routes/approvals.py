"""Streams approval gate and execution approval endpoints using penguin-dal.

Provides approval workflow for paused stream executions:
- Get pending approvals for current user
- Approve/reject paused executions
- Approval status tracking
- Approval gate CRUD operations
"""

import logging
import uuid
from datetime import datetime, timezone

from quart import Blueprint, current_app, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool

from .streams import (
    _can_edit_stream,
    _can_read_stream,
    _get_identity_id,
    _get_tenant_id,
)

logger = logging.getLogger(__name__)

bp = Blueprint("stream_approvals", __name__)


# ============================================================================
# Pending Approvals
# ============================================================================


@bp.route("/my-approvals", methods=["GET"])
@login_required
@require_scope("streams:read")
async def get_my_approvals():
    """Get pending approvals for current user.

    Returns paused stream executions waiting for the current user's approval.

    Returns:
        200: JSON with pending approvals array
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    if not identity_id:
        return ApiResponse.error("Identity not found", 403)

    def list_pending():
        # Find approval gates where user is an approver
        gates = db(
            (db.stream_approval_gates.tenant_id == tenant_id)
            & (db.stream_approval_gates.is_enabled == True)  # noqa: E712
        ).select()

        gate_ids = []
        for gate in gates:
            approvers = gate.approvers or []
            if identity_id in approvers:
                gate_ids.append(gate.id)

        if not gate_ids:
            return {
                "pending_approvals": [],
                "count": 0,
            }

        # Find paused executions for those gates where user has NOT already
        # submitted a decision
        pending = []

        executions = db(
            (db.stream_executions.status == "paused_for_approval")
            & (db.stream_executions.tenant_id == tenant_id)
        ).select(orderby=~db.stream_executions.created_at)

        for execution in executions:
            # Check if user already decided on this execution
            existing = (
                db(
                    (
                        db.stream_execution_approvals.execution_id
                        == execution.execution_id
                    )
                    & (
                        db.stream_execution_approvals.approver_identity_id
                        == identity_id
                    )
                    & (db.stream_execution_approvals.tenant_id == tenant_id)
                )
                .select()
                .first()
            )

            if existing:
                continue  # Skip if already decided

            # Get playbook details
            playbook = (
                db(
                    (db.stream_playbooks.id == execution.playbook_id)
                    & (db.stream_playbooks.tenant_id == tenant_id)
                )
                .select()
                .first()
            )
            if not playbook:
                continue

            # Check if stream is readable by user
            if not _can_read_stream(db, playbook, tenant_id, identity_id):
                continue

            # Get the requester
            requester = None
            if execution.triggered_by_identity_id:
                requester = (
                    db(db.identities.id == execution.triggered_by_identity_id)
                    .select()
                    .first()
                )

            pending.append(
                {
                    "execution_id": execution.execution_id,
                    "playbook_id": playbook.id,
                    "playbook_name": playbook.name,
                    "requested_by": (
                        requester.username
                        if requester
                        else execution.triggered_by or "Unknown"
                    ),
                    "requested_at": (
                        execution.created_at.isoformat()
                        if execution.created_at
                        else None
                    ),
                    "paused_at": (
                        execution.started_at.isoformat()
                        if execution.started_at
                        else None
                    ),
                }
            )

        return {
            "pending_approvals": pending,
            "count": len(pending),
        }

    result = await run_in_threadpool(list_pending)
    return ApiResponse.success(data=result)


# ============================================================================
# Approval Actions
# ============================================================================


@bp.route("/executions/<execution_id>/approve", methods=["POST"])
@login_required
@require_scope("streams:write")
async def approve_execution(execution_id: str):
    """Approve a paused stream execution.

    Args:
        execution_id: Execution identifier (string UUID)

    Request body:
        {
            "comment": "Optional approval comment"
        }

    Returns:
        200: JSON with success status
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id or not identity_id:
        return ApiResponse.error("Tenant or identity not found", 403)

    data = await request.get_json() or {}

    def approve():
        # Find execution
        execution = (
            db(
                (db.stream_executions.execution_id == execution_id)
                & (db.stream_executions.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not execution:
            return None, 404

        if execution.status != "paused_for_approval":
            return {"error": "Execution is not paused for approval"}, 400

        # Get playbook
        playbook = (
            db(
                (db.stream_playbooks.id == execution.playbook_id)
                & (db.stream_playbooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not playbook:
            return None, 404

        # Check if stream is readable and user can edit
        if not _can_read_stream(db, playbook, tenant_id, identity_id):
            return None, 403

        # Get active gates for this playbook
        gates = db(
            (db.stream_approval_gates.playbook_id == playbook.id)
            & (db.stream_approval_gates.is_enabled == True)  # noqa: E712
            & (db.stream_approval_gates.tenant_id == tenant_id)
        ).select()

        # Verify user is an approver
        user_can_approve = False
        gate_id = None
        for gate in gates:
            approvers = gate.approvers or []
            if identity_id in approvers:
                user_can_approve = True
                gate_id = gate.id
                break

        if not user_can_approve:
            return {"error": "Not authorized to approve this execution"}, 403

        # Check if user already approved
        existing = (
            db(
                (db.stream_execution_approvals.execution_id == execution_id)
                & (db.stream_execution_approvals.approver_identity_id == identity_id)
                & (db.stream_execution_approvals.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if existing:
            return {"error": "Already submitted an approval decision"}, 400

        # Record approval
        approval_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        db.stream_execution_approvals.insert(
            tenant_id=tenant_id,
            approval_id=approval_id,
            execution_id=execution_id,
            gate_id=gate_id,
            approver_identity_id=identity_id,
            decision="approve",
            comment=data.get("comment", ""),
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Advance execution status to running (resume from paused)
        db(db.stream_executions.execution_id == execution_id).update(
            status="running",
            updated_at=now,
        )
        db.commit()

        return {
            "message": "Execution approved and resumed",
            "approval_id": approval_id,
        }, 200

    result, status_code = await run_in_threadpool(approve)

    if status_code == 404:
        return ApiResponse.not_found("Execution")
    elif status_code == 403:
        return ApiResponse.error("Access denied", 403)
    elif status_code == 400:
        return ApiResponse.error(result.get("error"), 400)

    return ApiResponse.success(data=result)


@bp.route("/executions/<execution_id>/reject", methods=["POST"])
@login_required
@require_scope("streams:write")
async def reject_execution(execution_id: str):
    """Reject a paused stream execution.

    Args:
        execution_id: Execution identifier (string UUID)

    Request body:
        {
            "comment": "Required rejection reason"
        }

    Returns:
        200: JSON with success status
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id or not identity_id:
        return ApiResponse.error("Tenant or identity not found", 403)

    data = await request.get_json() or {}

    # Require comment for rejection
    comment = data.get("comment", "").strip()
    if not comment:
        return ApiResponse.error("Comment is required for rejection", 400)

    def reject():
        # Find execution
        execution = (
            db(
                (db.stream_executions.execution_id == execution_id)
                & (db.stream_executions.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not execution:
            return None, 404

        if execution.status != "paused_for_approval":
            return {"error": "Execution is not paused for approval"}, 400

        # Get playbook
        playbook = (
            db(
                (db.stream_playbooks.id == execution.playbook_id)
                & (db.stream_playbooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not playbook:
            return None, 404

        # Check if stream is readable and user can edit
        if not _can_read_stream(db, playbook, tenant_id, identity_id):
            return None, 403

        # Get active gates for this playbook
        gates = db(
            (db.stream_approval_gates.playbook_id == playbook.id)
            & (db.stream_approval_gates.is_enabled == True)  # noqa: E712
            & (db.stream_approval_gates.tenant_id == tenant_id)
        ).select()

        # Verify user is an approver
        user_can_approve = False
        gate_id = None
        for gate in gates:
            approvers = gate.approvers or []
            if identity_id in approvers:
                user_can_approve = True
                gate_id = gate.id
                break

        if not user_can_approve:
            return {"error": "Not authorized to reject this execution"}, 403

        # Check if user already decided
        existing = (
            db(
                (db.stream_execution_approvals.execution_id == execution_id)
                & (db.stream_execution_approvals.approver_identity_id == identity_id)
                & (db.stream_execution_approvals.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if existing:
            return {"error": "Already submitted an approval decision"}, 400

        # Record rejection
        approval_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        db.stream_execution_approvals.insert(
            tenant_id=tenant_id,
            approval_id=approval_id,
            execution_id=execution_id,
            gate_id=gate_id,
            approver_identity_id=identity_id,
            decision="reject",
            comment=comment,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Mark execution as failed due to rejection
        db(db.stream_executions.execution_id == execution_id).update(
            status="failed",
            error_message=f"Rejected during approval gate: {comment}",
            completed_at=now,
            updated_at=now,
        )
        db.commit()

        return {
            "message": "Execution rejected",
            "approval_id": approval_id,
        }, 200

    result, status_code = await run_in_threadpool(reject)

    if status_code == 404:
        return ApiResponse.not_found("Execution")
    elif status_code == 403:
        return ApiResponse.error("Access denied", 403)
    elif status_code == 400:
        return ApiResponse.error(result.get("error"), 400)

    return ApiResponse.success(data=result)


# ============================================================================
# Approval Status
# ============================================================================


@bp.route("/executions/<execution_id>/approval-status", methods=["GET"])
@login_required
@require_scope("streams:read")
async def get_approval_status(execution_id: str):
    """Get approval status for an execution.

    Args:
        execution_id: Execution identifier (string UUID)

    Returns:
        200: JSON with approval status and decisions
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def get_status():
        # Find execution
        execution = (
            db(
                (db.stream_executions.execution_id == execution_id)
                & (db.stream_executions.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not execution:
            return None, 404

        # Get all approvals for this execution
        approvals = db(
            (db.stream_execution_approvals.execution_id == execution_id)
            & (db.stream_execution_approvals.tenant_id == tenant_id)
        ).select(orderby=db.stream_execution_approvals.created_at)

        result = []
        for approval in approvals:
            approver = (
                db(db.identities.id == approval.approver_identity_id).select().first()
            )
            gate = (
                db(db.stream_approval_gates.id == approval.gate_id).select().first()
                if approval.gate_id
                else None
            )
            result.append(
                {
                    "approval_id": approval.approval_id,
                    "approver": {
                        "id": approver.id if approver else None,
                        "name": approver.username if approver else "Unknown",
                    },
                    "gate_name": gate.name if gate else "Unknown Gate",
                    "decision": approval.decision,
                    "comment": approval.comment or "",
                    "created_at": (
                        approval.created_at.isoformat() if approval.created_at else None
                    ),
                }
            )

        return {
            "execution_status": execution.status,
            "approvals": result,
            "count": len(result),
        }, 200

    result, status_code = await run_in_threadpool(get_status)

    if status_code == 404:
        return ApiResponse.not_found("Execution")

    return ApiResponse.success(data=result)


# ============================================================================
# Approval Gate Management
# ============================================================================


@bp.route("/<int:stream_id>/approval-gates", methods=["GET"])
@login_required
@require_scope("streams:read")
async def list_approval_gates(stream_id):
    """List all approval gates for a stream.

    Args:
        stream_id: Stream identifier

    Returns:
        200: JSON with approval gates array
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def list_gates():
        # Find stream
        stream = (
            db(
                (db.stream_playbooks.id == stream_id)
                & (db.stream_playbooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not stream:
            return None, 404

        # Check read access
        if not _can_read_stream(db, stream, tenant_id, identity_id):
            return None, 403

        # Get all gates for this stream
        gates = db(
            (db.stream_approval_gates.playbook_id == stream_id)
            & (db.stream_approval_gates.tenant_id == tenant_id)
        ).select(orderby=db.stream_approval_gates.created_at)

        result = [
            {
                "id": gate.id,
                "gate_id": gate.gate_id,
                "node_id": gate.node_id,
                "name": gate.name,
                "description": gate.description or "",
                "require_approval": gate.require_approval,
                "min_approvers": gate.min_approvers,
                "approvers": gate.approvers or [],
                "approver_groups": gate.approver_groups or [],
                "timeout_minutes": gate.timeout_minutes,
                "is_enabled": gate.is_enabled,
                "created_at": (
                    gate.created_at.isoformat() if gate.created_at else None
                ),
                "updated_at": (
                    gate.updated_at.isoformat() if gate.updated_at else None
                ),
            }
            for gate in gates
        ]

        return result, 200

    result, status_code = await run_in_threadpool(list_gates)

    if status_code == 404:
        return ApiResponse.not_found("Stream")
    elif status_code == 403:
        return ApiResponse.error("Access denied", 403)

    return ApiResponse.success(data={"gates": result, "count": len(result)})


@bp.route("/<int:stream_id>/approval-gates", methods=["POST"])
@login_required
@require_scope("streams:write")
async def create_approval_gate(stream_id):
    """Create a new approval gate for a stream.

    Args:
        stream_id: Stream identifier

    Request body:
        {
            "node_id": "node-123",
            "name": "Production Approval",
            "description": "Requires approval before production deployment",
            "require_approval": true,
            "min_approvers": 2,
            "approvers": [1, 2, 3],
            "approver_groups": [10, 11],
            "timeout_minutes": 60,
            "is_enabled": true
        }

    Returns:
        201: JSON with created gate
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id or not identity_id:
        return ApiResponse.error("Tenant or identity not found", 403)

    data = await request.get_json() or {}

    def create():
        # Find stream
        stream = (
            db(
                (db.stream_playbooks.id == stream_id)
                & (db.stream_playbooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not stream:
            return None, 404

        # Check edit access
        if not _can_edit_stream(db, stream, tenant_id, identity_id):
            return None, 403

        # Validate required fields
        if not data.get("node_id") or not data.get("name"):
            return {"error": "node_id and name are required"}, 400

        # Create gate
        gate_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        db_id = db.stream_approval_gates.insert(
            tenant_id=tenant_id,
            gate_id=gate_id,
            playbook_id=stream_id,
            node_id=data["node_id"],
            name=data["name"],
            description=data.get("description", ""),
            require_approval=data.get("require_approval", True),
            min_approvers=data.get("min_approvers", 1),
            approvers=data.get("approvers", []),
            approver_groups=data.get("approver_groups", []),
            timeout_minutes=data.get("timeout_minutes"),
            is_enabled=data.get("is_enabled", True),
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Return created gate
        gate = db(db.stream_approval_gates.id == db_id).select().first()

        return {
            "id": gate.id,
            "gate_id": gate.gate_id,
            "node_id": gate.node_id,
            "name": gate.name,
            "description": gate.description or "",
            "require_approval": gate.require_approval,
            "min_approvers": gate.min_approvers,
            "approvers": gate.approvers or [],
            "approver_groups": gate.approver_groups or [],
            "timeout_minutes": gate.timeout_minutes,
            "is_enabled": gate.is_enabled,
        }, 201

    result, status_code = await run_in_threadpool(create)

    if status_code == 404:
        return ApiResponse.not_found("Stream")
    elif status_code == 403:
        return ApiResponse.error("Access denied", 403)
    elif status_code == 400:
        return ApiResponse.error(result.get("error"), 400)

    return ApiResponse.success(data=result, status_code=status_code)
