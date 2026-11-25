"""Core Trigger Nodes for Streams Playbooks (Manual, Schedule, Webhook)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ...executor.node_registry import register_node
from ..base import BaseNode


def _parse_json_object(raw: Any) -> dict[str, Any]:
    """Parse optional dict or JSON string safely."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


@register_node("trigger_manual", "triggers", "Manual / On-Demand Trigger")
class ManualTriggerNode(BaseNode):
    """Starts a playbook manually or via API with optional test payload."""

    node_type = "trigger_manual"
    name = "Manual Trigger"
    description = "Start playbook manually from editor, UI, or Execute API"
    category = "triggers"

    @classmethod
    def inputs(cls) -> list[dict[str, Any]]:
        return []

    @classmethod
    def outputs(cls) -> list[dict[str, Any]]:
        return [
            {
                "name": "out",
                "description": "Trigger payload passed downstream as $json and $trigger",
                "data_type": "object",
            }
        ]

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        global_cfg = self.context.get("global_config") or {}
        trigger_data = global_cfg.get("trigger_data")
        sample_payload = _parse_json_object(self.get_config_value("sample_payload"))

        if isinstance(trigger_data, dict) and trigger_data:
            payload = trigger_data
        elif sample_payload:
            payload = sample_payload
        elif trigger_data is not None and trigger_data != {}:
            payload = {"data": trigger_data}
        else:
            payload = {}

        result = {
            **payload,
            "_trigger": {
                "type": "manual",
                "triggered_at": datetime.now(UTC).isoformat(),
                "execution_id": self.context.get("execution_id"),
            },
        }
        self.log_info("Manual trigger executed")
        return {
            "out": {
                "data": result,
                "metadata": {},
                "source_node_id": self.context.get("node_id", ""),
            }
        }


@register_node("trigger_schedule", "triggers", "Schedule Trigger")
class ScheduleTriggerNode(BaseNode):
    """Starts a playbook on a cron interval with optional static payload."""

    node_type = "trigger_schedule"
    name = "Schedule Trigger"
    description = "Trigger playbook automatically on a cron schedule"
    category = "triggers"

    @classmethod
    def inputs(cls) -> list[dict[str, Any]]:
        return []

    @classmethod
    def outputs(cls) -> list[dict[str, Any]]:
        return [
            {
                "name": "out",
                "description": "Scheduled run context and configured static payload",
                "data_type": "object",
            }
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        cron = config.get("cron_expression")
        if cron is not None and not isinstance(cron, str):
            return ["cron_expression must be a string"]
        return []

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        global_cfg = self.context.get("global_config") or {}
        trigger_data = global_cfg.get("trigger_data")
        static_payload = _parse_json_object(self.get_config_value("static_payload"))
        base = trigger_data if isinstance(trigger_data, dict) else {}

        result = {
            **static_payload,
            **base,
            "scheduled_at": datetime.now(UTC).isoformat(),
            "cron_expression": self.get_config_value("cron_expression", "0 * * * *"),
            "timezone": self.get_config_value("timezone", "UTC"),
        }
        self.log_info(
            f"Schedule trigger executed ({result['cron_expression']} {result['timezone']})"
        )
        return {
            "out": {
                "data": result,
                "metadata": {},
                "source_node_id": self.context.get("node_id", ""),
            }
        }


@register_node("trigger_webhook", "triggers", "Webhook Trigger")
class WebhookTriggerNode(BaseNode):
    """Starts a playbook when an authenticated inbound webhook arrives."""

    node_type = "trigger_webhook"
    name = "Webhook Trigger"
    description = "Receive external HTTP webhooks via Inbound Webhooks or Stream Hooks"
    category = "triggers"

    @classmethod
    def inputs(cls) -> list[dict[str, Any]]:
        return []

    @classmethod
    def outputs(cls) -> list[dict[str, Any]]:
        return [
            {
                "name": "out",
                "description": "Incoming webhook payload, headers, and metadata",
                "data_type": "object",
            }
        ]

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        global_cfg = self.context.get("global_config") or {}
        trigger_data = global_cfg.get("trigger_data") or {}
        if not isinstance(trigger_data, dict):
            trigger_data = {"payload": trigger_data}

        result = {
            "payload": trigger_data.get("payload", trigger_data),
            "headers": trigger_data.get("headers", {}),
            "source_ip": trigger_data.get("source_ip")
            or (trigger_data.get("__webhook__") or {}).get("remote_addr"),
            "received_at": trigger_data.get("received_at")
            or (trigger_data.get("__webhook__") or {}).get("timestamp")
            or datetime.now(UTC).isoformat(),
            **{
                k: v
                for k, v in trigger_data.items()
                if k not in {"payload", "headers", "source_ip", "received_at"}
            },
        }
        self.log_info("Webhook trigger executed")
        return {
            "out": {
                "data": result,
                "metadata": {},
                "source_node_id": self.context.get("node_id", ""),
            }
        }
