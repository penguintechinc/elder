"""Respond Action Node — Formats final structured output from a playbook."""

from __future__ import annotations

import json
from typing import Any

from ...executor.node_registry import register_node
from ..base import BaseNode
from ..template import interpolate_any, interpolate_value


@register_node("action_respond", "actions", "Playbook Response")
class RespondAction(BaseNode):
    """Emit a structured playbook response payload and status code."""

    node_type = "action_respond"
    name = "Playbook Response"
    description = "Format and return the final structured result of the playbook"
    category = "actions"

    @classmethod
    def inputs(cls) -> list[dict[str, Any]]:
        return [
            {
                "name": "in",
                "description": "Data to include in or transform for the response",
                "required": False,
                "data_type": "any",
            }
        ]

    @classmethod
    def outputs(cls) -> list[dict[str, Any]]:
        return [
            {
                "name": "out",
                "description": "Structured response envelope (status_code, summary, body)",
                "data_type": "object",
            }
        ]

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        status_code = int(self.get_config_value("status_code", 200))
        summary_tpl = self.get_config_value("summary", "")
        raw_body = self.get_config_value("response_body")

        summary = interpolate_value(
            summary_tpl, self.context, inputs, preserve_type=False
        )

        if raw_body is None or raw_body == "":
            body = inputs.get("in", inputs.get("default", {}))
        elif isinstance(raw_body, str):
            interpolated = interpolate_value(
                raw_body, self.context, inputs, preserve_type=True
            )
            if isinstance(interpolated, str):
                try:
                    body = json.loads(interpolated)
                except json.JSONDecodeError:
                    body = interpolated
            else:
                body = interpolated
        else:
            body = interpolate_any(raw_body, self.context, inputs, preserve_type=True)

        result = {
            "status_code": status_code,
            "summary": summary,
            "body": body,
        }
        self.log_info(f"Playbook response prepared (status={status_code})")
        return {
            "out": {
                "data": result,
                "metadata": {},
                "source_node_id": self.context.get("node_id", ""),
            }
        }
