"""Set / Map Fields Transform Node — n8n-style field assignment and shaping."""

from __future__ import annotations

import copy
import json
from typing import Any

from ...executor.node_registry import register_node
from ..base import BaseNode
from ..template import interpolate_value


def _cast_value(val: Any, target_type: str) -> Any:
    """Cast an evaluated assignment value to the requested target type."""
    if target_type == "auto" or not target_type:
        return val
    if target_type == "string":
        return "" if val is None else str(val)
    if target_type == "number":
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return val
        try:
            s = str(val).strip()
            return float(s) if "." in s else int(s)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Cannot cast {val!r} to number") from exc
    if target_type == "boolean":
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.strip().lower() in {"1", "true", "yes", "on"}
        return bool(val)
    if target_type == "json":
        if isinstance(val, (dict, list)):
            return val
        if isinstance(val, str):
            return json.loads(val) if val.strip() else None
        return val
    return val


def _assign_path(target: dict[str, Any], path: str, value: Any) -> None:
    """Assign value into a nested dict path using dot notation."""
    parts = [p for p in path.split(".") if p]
    if not parts:
        return
    curr: dict[str, Any] = target
    for part in parts[:-1]:
        if part not in curr or not isinstance(curr[part], dict):
            curr[part] = {}
        curr = curr[part]
    curr[parts[-1]] = value


@register_node("transform_set_fields", "transforms", "Set Fields")
class SetFieldsTransform(BaseNode):
    """Map, compute, or override fields using upstream expressions ($json/$node)."""

    node_type = "transform_set_fields"
    name = "Set Fields"
    description = (
        "Add, rename, or compute fields with {{$json.field}} and {{$node[...]}} values"
    )
    category = "transforms"

    @classmethod
    def inputs(cls) -> list[dict[str, Any]]:
        return [
            {
                "name": "in",
                "description": "Input object or array of objects to enrich/shape",
                "required": False,
                "data_type": "any",
            }
        ]

    @classmethod
    def outputs(cls) -> list[dict[str, Any]]:
        return [
            {
                "name": "out",
                "description": "Shaped payload with assigned fields",
                "data_type": "any",
            }
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        assignments = config.get("assignments", [])
        if assignments is not None and not isinstance(assignments, list):
            errors.append("assignments must be a list")
            return errors
        for idx, item in enumerate(assignments or []):
            if not isinstance(item, dict) or not str(item.get("name", "")).strip():
                errors.append(f"Assignment {idx + 1}: field name is required")
        return errors

    def _apply_to_item(
        self, item: Any, assignments: list[dict[str, Any]]
    ) -> dict[str, Any]:
        keep_only = bool(self.get_config_value("keep_only_set", False))
        base: dict[str, Any]
        if not keep_only and isinstance(item, dict):
            base = copy.deepcopy(item)
        else:
            base = {}

        local_inputs = {"in": item} if item is not None else {}
        for item_cfg in assignments:
            name = str(item_cfg.get("name", "")).strip()
            if not name:
                continue
            raw_val = item_cfg.get("value")
            val_type = str(item_cfg.get("type", "auto"))
            evaluated = interpolate_value(
                raw_val, self.context, local_inputs, preserve_type=True
            )
            casted = _cast_value(evaluated, val_type)
            _assign_path(base, name, casted)

        return base

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        raw_in = inputs.get("in")
        if raw_in is None:
            raw_in = inputs.get("default", {})

        assignments = self.get_config_value("assignments", []) or []

        if isinstance(raw_in, list):
            result: Any = [self._apply_to_item(el, assignments) for el in raw_in]
        else:
            result = self._apply_to_item(raw_in, assignments)

        self.log_info(f"Set {len(assignments)} field(s)")
        return {
            "out": {
                "data": result,
                "metadata": {},
                "source_node_id": self.context.get("node_id", ""),
            }
        }
