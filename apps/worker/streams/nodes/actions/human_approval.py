"""Human Approval Action Node for Streams Workflow.

Pauses playbook execution and creates/updates an approval gate in the Elder API
so authorized human approvers can review and approve or reject the run.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from ...executor.node_registry import register_node
from ..base import BaseNode
from ..template import interpolate_value

DEFAULT_REQUIRED_APPROVALS: int = 1
DEFAULT_TIMEOUT_MINUTES: int = 1440


def _resolve_elder_api_base(context: dict[str, Any]) -> str:
    """Resolve the internal Elder API base URL for worker-side calls."""
    global_cfg = context.get("global_config") or {}
    base = (
        global_cfg.get("elder_api_url")
        or os.environ.get("ELDER_API_URL")
        or "http://hub-api:8080"
    )
    return str(base).rstrip("/")


def _resolve_elder_auth_token(context: dict[str, Any]) -> str | None:
    """Resolve bearer token for internal Elder API calls."""
    global_cfg = context.get("global_config") or {}
    token = (
        global_cfg.get("_elder_auth_token")
        or os.environ.get("ELDER_INTERNAL_TOKEN")
        or os.environ.get("ELDER_API_TOKEN")
    )
    return str(token) if token else None


def _parse_approver_ids(raw: Any) -> list[int]:
    """Parse approver identity IDs from a comma-separated string, list, or int."""
    if raw is None or raw == "":
        return []

    if isinstance(raw, bool):
        raise ValueError("approver_ids must contain integer identity IDs")

    if isinstance(raw, int):
        if raw <= 0:
            raise ValueError("approver_ids must contain positive integer identity IDs")
        return [raw]

    tokens: list[Any]
    if isinstance(raw, (list, tuple, set)):
        tokens = list(raw)
    elif isinstance(raw, str):
        tokens = [part.strip() for part in raw.split(",") if part.strip()]
    else:
        raise ValueError(
            "approver_ids must be a comma-separated string or list of integer IDs"
        )

    result: list[int] = []
    for token in tokens:
        if isinstance(token, bool):
            raise ValueError("approver_ids must contain integer identity IDs")
        try:
            val = int(str(token).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Invalid approver identity ID {token!r}: must be an integer"
            ) from exc
        if val <= 0:
            raise ValueError(
                f"Invalid approver identity ID {val}: must be a positive integer"
            )
        if val not in result:
            result.append(val)
    return result


@register_node("action_human_approval", "actions", "Human Approval")
class HumanApprovalAction(BaseNode):
    """Pause playbook execution and request human approval via the Elder API."""

    node_type = "action_human_approval"
    name = "Human Approval"
    description = (
        "Pause playbook execution and request human approval before continuing"
    )
    category = "actions"

    @classmethod
    def inputs(cls) -> list[dict[str, Any]]:
        """Define input ports for the Human Approval node."""
        return [
            {
                "name": "in",
                "description": (
                    "Optional upstream payload used for {{expression}} interpolation"
                ),
                "required": False,
                "data_type": "any",
            }
        ]

    @classmethod
    def outputs(cls) -> list[dict[str, Any]]:
        """Define output ports for the Human Approval node."""
        return [
            {
                "name": "approved",
                "description": "Output port followed when the request is approved",
                "data_type": "any",
            },
            {
                "name": "rejected",
                "description": "Output port followed when the request is rejected",
                "data_type": "any",
            },
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """Validate static Human Approval node configuration."""
        errors: list[str] = []

        title = str(config.get("title") or "").strip()
        if not title:
            errors.append("title is required")

        raw_approver_ids = config.get("approver_ids")
        if raw_approver_ids not in (None, ""):
            if not (isinstance(raw_approver_ids, str) and "{{" in raw_approver_ids):
                try:
                    _parse_approver_ids(raw_approver_ids)
                except ValueError as exc:
                    errors.append(str(exc))

        raw_required = config.get("required_approvals", DEFAULT_REQUIRED_APPROVALS)
        if raw_required is not None and raw_required != "":
            try:
                req_int = int(raw_required)
                if req_int < 1:
                    errors.append("required_approvals must be at least 1")
            except (TypeError, ValueError):
                errors.append("required_approvals must be a positive integer")

        raw_timeout = config.get("timeout_minutes", DEFAULT_TIMEOUT_MINUTES)
        if raw_timeout is not None and raw_timeout != "":
            try:
                timeout_int = int(raw_timeout)
                if timeout_int < 1:
                    errors.append("timeout_minutes must be at least 1")
            except (TypeError, ValueError):
                errors.append("timeout_minutes must be a positive integer")

        return errors

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Request human approval from the Elder API and pause execution."""
        raw_title = self.get_config_value("title", "")
        title = str(
            interpolate_value(raw_title, self.context, inputs, preserve_type=False)
            or ""
        ).strip()
        if not title:
            raise ValueError("title is required for Human Approval node")

        raw_instructions = self.get_config_value("instructions", "")
        instructions = str(
            interpolate_value(
                raw_instructions, self.context, inputs, preserve_type=False
            )
            or ""
        ).strip()

        raw_approver_ids = self.get_config_value("approver_ids", "")
        if isinstance(raw_approver_ids, str):
            resolved_approvers = interpolate_value(
                raw_approver_ids, self.context, inputs, preserve_type=True
            )
        else:
            resolved_approvers = raw_approver_ids
        approver_ids = _parse_approver_ids(resolved_approvers)

        raw_required = self.get_config_value(
            "required_approvals", DEFAULT_REQUIRED_APPROVALS
        )
        try:
            required_approvals = int(
                DEFAULT_REQUIRED_APPROVALS
                if raw_required in (None, "")
                else raw_required
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("required_approvals must be a positive integer") from exc
        if required_approvals < 1:
            raise ValueError("required_approvals must be at least 1")

        raw_timeout = self.get_config_value("timeout_minutes", DEFAULT_TIMEOUT_MINUTES)
        try:
            timeout_minutes = int(
                DEFAULT_TIMEOUT_MINUTES if raw_timeout in (None, "") else raw_timeout
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("timeout_minutes must be a positive integer") from exc
        if timeout_minutes < 1:
            raise ValueError("timeout_minutes must be at least 1")

        playbook_id = self.context.get("playbook_id")
        execution_id = self.context.get("execution_id")
        node_id = str(self.context.get("node_id") or "")

        if not playbook_id or not execution_id:
            raise ValueError(
                "playbook_id and execution_id are required in execution context"
            )

        base_url = _resolve_elder_api_base(self.context)
        url = (
            f"{base_url}/api/v1/streams/{playbook_id}"
            f"/executions/{execution_id}/request-approval"
        )
        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        token = _resolve_elder_auth_token(self.context)
        if token:
            headers["Authorization"] = f"Bearer {token}"

        payload = {
            "node_id": node_id,
            "title": title,
            "instructions": instructions,
            "approver_ids": approver_ids,
            "required_approvals": required_approvals,
            "timeout_minutes": timeout_minutes,
        }

        self.log_info(
            f"Requesting human approval '{title}' for playbook={playbook_id} "
            f"execution={execution_id} (required_approvals={required_approvals})"
        )

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json=payload, headers=headers)

        if response.status_code >= 400:
            self.log_error(
                f"Human approval request failed: HTTP {response.status_code}"
            )
            raise RuntimeError(
                f"Failed to request human approval (HTTP {response.status_code}): "
                f"{response.text}"
            )

        return {
            "approved": {
                "data": None,
                "metadata": {
                    "paused_for_approval": True,
                    "approval_title": title,
                    "instructions": instructions,
                    "approver_ids": approver_ids,
                    "required_approvals": required_approvals,
                    "timeout_minutes": timeout_minutes,
                },
                "source_node_id": node_id,
            },
            "rejected": {
                "data": None,
                "metadata": {
                    "paused_for_approval": True,
                    "approval_title": title,
                },
                "source_node_id": node_id,
            },
        }
