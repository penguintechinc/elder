"""Shared n8n-style template and expression resolver for Streams workflow nodes.

Supports resolving data propagated from upstream nodes, root triggers, and
execution metadata:
- ``{{$json.field.nested}}`` / ``{{in.field}}`` / ``{{input.field}}`` / ``{{field}}``
- ``{{$node["Node Label"].field}}`` / ``{{$node.node_id.field}}``
- ``{{$trigger.field}}``
- ``{{$execution.id}}`` / ``{{$execution.playbook_id}}`` / ``{{$execution.tenant_id}}``
"""

from __future__ import annotations

import json
import re
from typing import Any

# Matches both {{...}} anywhere in a string and exact single-{{...}} expressions.
_TEMPLATE_TOKEN_RE = re.compile(r"\{\{\s*(.+?)\s*\}\}")
_EXACT_TOKEN_RE = re.compile(r"^\{\{\s*(.+?)\s*\}\}$")
_NODE_BRACKET_RE = re.compile(r'^\$node\[(?:"([^"]+)"|\'([^\']+)\')\](?:\.(.*))?$')
_BRACKET_INDEX_RE = re.compile(r"\[(\d+)\]")

_MISSING = object()


def _normalize_unwrapped(value: Any) -> Any:
    """Unwrap wrapped node port dicts if one slipped through."""
    if isinstance(value, dict) and set(value.keys()) >= {"data", "source_node_id"}:
        return value.get("data")
    return value


def resolve_path(obj: Any, path: str | None) -> Any:
    """Resolve a dot/bracket path inside a nested dict/list structure.

    Returns ``_MISSING`` if any segment cannot be traversed.
    """
    if obj is _MISSING or obj is None:
        return _MISSING
    if not path:
        return obj

    normalized = _BRACKET_INDEX_RE.sub(r".\1", path.strip())
    parts = [p for p in normalized.split(".") if p != ""]
    current = obj

    for part in parts:
        current = _normalize_unwrapped(current)
        if isinstance(current, dict):
            if part in current:
                current = current[part]
            else:
                return _MISSING
        elif isinstance(current, (list, tuple)) and part.isdigit():
            idx = int(part)
            if 0 <= idx < len(current):
                current = current[idx]
            else:
                return _MISSING
        else:
            return _MISSING

    return current


def _primary_json_payload(inputs: dict[str, Any]) -> Any:
    """Determine the active `$json` payload from a node's incoming inputs."""
    if not isinstance(inputs, dict):
        return inputs
    if "in" in inputs and inputs["in"] is not None:
        return _normalize_unwrapped(inputs["in"])
    if "default" in inputs and inputs["default"] is not None:
        return _normalize_unwrapped(inputs["default"])
    if "payload" in inputs and inputs["payload"] is not None:
        return _normalize_unwrapped(inputs["payload"])
    # Fall back to first non-None input port value if single-port custom name
    for val in inputs.values():
        if val is not None:
            return _normalize_unwrapped(val)
    return {}


def resolve_expression(
    expr: str,
    context: dict[str, Any] | None = None,
    inputs: dict[str, Any] | None = None,
) -> Any:
    """Evaluate one template expression inside ``{{ ... }}``.

    Returns ``_MISSING`` when the target field cannot be found.
    """
    ctx = context or {}
    inps = inputs or {}
    global_cfg = ctx.get("global_config") or {}
    node_cfg = ctx.get("config") or {}
    node_outputs = ctx.get("node_outputs") or {}
    trigger_data = global_cfg.get("trigger_data") or {}
    expr_clean = expr.strip()

    # Never expose internal transient auth tokens to template expressions
    if expr_clean == "_elder_auth_token" or expr_clean.startswith("_elder_auth_token."):
        return _MISSING

    # 1. Explicit $json / $input
    if expr_clean == "$json":
        return _primary_json_payload(inps)
    if expr_clean.startswith("$json."):
        return resolve_path(_primary_json_payload(inps), expr_clean[6:])
    if expr_clean == "$input":
        return _primary_json_payload(inps)
    if expr_clean.startswith("$input."):
        return resolve_path(_primary_json_payload(inps), expr_clean[7:])

    # 2. Named ancestor output: $node["Label"].field or $node.id.field
    bracket_match = _NODE_BRACKET_RE.match(expr_clean)
    if bracket_match:
        node_key = bracket_match.group(1) or bracket_match.group(2)
        remainder = bracket_match.group(3)
        if node_key not in node_outputs:
            return _MISSING
        return resolve_path(node_outputs[node_key], remainder)

    if expr_clean.startswith("$node."):
        rest = expr_clean[6:]
        head, _, tail = rest.partition(".")
        if head not in node_outputs:
            return _MISSING
        return resolve_path(node_outputs[head], tail or None)

    # 3. Root trigger data: $trigger or $trigger.field
    if expr_clean == "$trigger":
        return trigger_data
    if expr_clean.startswith("$trigger."):
        return resolve_path(trigger_data, expr_clean[9:])

    # 4. Execution metadata
    if expr_clean.startswith("$execution."):
        exec_info = {
            "id": ctx.get("execution_id"),
            "execution_id": ctx.get("execution_id"),
            "playbook_id": ctx.get("playbook_id"),
            "tenant_id": global_cfg.get("tenant_id"),
        }
        return resolve_path(exec_info, expr_clean[11:])

    # 5. Prefix-based input/config/trigger resolution (backward-compatible)
    if expr_clean == "in" or expr_clean == "input":
        return _primary_json_payload(inps)
    if expr_clean.startswith("in."):
        return resolve_path(_primary_json_payload(inps), expr_clean[3:])
    if expr_clean.startswith("input."):
        res = resolve_path(inps, expr_clean[6:])
        if res is not _MISSING:
            return res
        return resolve_path(_primary_json_payload(inps), expr_clean[6:])
    if expr_clean.startswith("config."):
        return resolve_path(node_cfg, expr_clean[7:])

    # 6. Direct lookup order: exact port name -> primary json -> variables -> node config -> trigger
    head, _, _ = expr_clean.partition(".")
    if head in inps:
        res = resolve_path(inps, expr_clean)
        if res is not _MISSING:
            return res

    primary = _primary_json_payload(inps)
    res = resolve_path(primary, expr_clean)
    if res is not _MISSING:
        return res

    res = resolve_path(global_cfg, expr_clean)
    if res is not _MISSING:
        return res

    res = resolve_path(node_cfg, expr_clean)
    if res is not _MISSING:
        return res

    res = resolve_path(trigger_data, expr_clean)
    if res is not _MISSING:
        return res

    return _MISSING


def _stringify(val: Any) -> str:
    """Convert resolved value into string suitable for embedded interpolation."""
    if val is None:
        return ""
    if isinstance(val, (dict, list)):
        return json.dumps(val)
    return str(val)


def interpolate_value(
    value: Any,
    context: dict[str, Any] | None = None,
    inputs: dict[str, Any] | None = None,
    *,
    preserve_type: bool = False,
) -> Any:
    """Interpolate ``{{...}}`` expressions within a scalar value.

    When ``preserve_type=True`` and ``value`` is a single ``"{{expr}}"``
    placeholder, returns the raw Python object/number/bool instead of forcing
    string conversion (unresolved expressions keep the literal string).
    """
    if not isinstance(value, str):
        return value

    if preserve_type:
        exact = _EXACT_TOKEN_RE.match(value.strip())
        if exact:
            resolved = resolve_expression(exact.group(1), context, inputs)
            if resolved is not _MISSING:
                return resolved
            return value

    def _replace(match: re.Match[str]) -> str:
        resolved = resolve_expression(match.group(1), context, inputs)
        if resolved is _MISSING:
            return match.group(0)
        return _stringify(resolved)

    return _TEMPLATE_TOKEN_RE.sub(_replace, value)


def interpolate_any(
    value: Any,
    context: dict[str, Any] | None = None,
    inputs: dict[str, Any] | None = None,
    *,
    preserve_type: bool = True,
) -> Any:
    """Recursively interpolate dicts, lists, and strings."""
    if isinstance(value, str):
        return interpolate_value(value, context, inputs, preserve_type=preserve_type)
    if isinstance(value, dict):
        return {
            k: interpolate_any(v, context, inputs, preserve_type=preserve_type)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [
            interpolate_any(item, context, inputs, preserve_type=preserve_type)
            for item in value
        ]
    return value
