"""If-Then Conditional Node for Streams Workflow.

Evaluates conditions and routes execution through "true" or "false" outputs.
Supports multiple conditions with AND/OR logic.
Ported from icestreams-worker.
"""

from __future__ import annotations

import logging
import operator
import os
import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

from ...executor.node_registry import register_node
from ..base import BaseNode
from ..regex_guard import UnsafeRegexError, safe_regex_search
from ..template import interpolate_value

logger = logging.getLogger(__name__)

# Map resource categories (matching FlowPropertyModals / NodeInspector) to
# Elder API detail endpoints for live state & relationship conditionals.
RESOURCE_DETAIL_ENDPOINTS: dict[str, str] = {
    "identity": "/api/v1/identities/{id}",
    "user": "/api/v1/identities/{id}",
    "entity": "/api/v1/entities/{id}",
    "network": "/api/v1/networking/networks/{id}",
    "data_store": "/api/v1/data-stores/{id}",
    "service": "/api/v1/services/{id}",
    "software": "/api/v1/software/{id}",
    "ai_resource": "/api/v1/ai/resources/{id}",
    "organization": "/api/v1/organizations/{id}",
    "project": "/api/v1/projects/{id}",
    "issue": "/api/v1/issues/{id}",
}

# Operator mapping for conditional evaluation. "regex" is special-cased in
# _evaluate_condition below -- it must be awaited through the safe_regex_search
# ReDoS guard (thread offload + timeout), so it is never invoked through this
# dict directly; the entry is kept only so "regex" still shows up as a known
# operator name wherever OPERATORS.keys() is inspected.
OPERATORS: dict[str, Callable] = {
    "eq": operator.eq,
    "ne": operator.ne,
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
    "contains": lambda a, b: b in a if hasattr(a, "__contains__") else False,
    "not_contains": lambda a, b: b not in a if hasattr(a, "__contains__") else True,
    "starts_with": lambda a, b: str(a).startswith(str(b)),
    "ends_with": lambda a, b: str(a).endswith(str(b)),
    "regex": lambda a, b: bool(re.search(b, str(a))),
    "is_null": lambda a, b: a is None,
    "is_not_null": lambda a, b: a is not None,
    "in": lambda a, b: a in b if isinstance(b, (list, tuple, set)) else False,
    "not_in": lambda a, b: a not in b if isinstance(b, (list, tuple, set)) else True,
}


def get_field_value(data: Any, field: str) -> Any:
    """Get nested field value using dot notation."""
    if not isinstance(data, dict):
        return None

    parts = field.split(".")
    value = data
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit():
            idx = int(part)
            value = value[idx] if idx < len(value) else None
        else:
            return None
    return value


def _resolve_elder_api_base(context: dict[str, Any]) -> str:
    """Resolve the internal Elder API base URL for worker-side lookups."""
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


def _coerce_comparable(actual: Any, expected: Any) -> tuple[Any, Any]:
    """Normalize stringified booleans/numbers when comparing against typed values."""
    if isinstance(actual, bool) and isinstance(expected, str):
        low = expected.strip().lower()
        if low == "true":
            return actual, True
        if low == "false":
            return actual, False
    if (
        isinstance(actual, (int, float))
        and not isinstance(actual, bool)
        and isinstance(expected, str)
    ):
        try:
            num = float(expected)
            return actual, int(num) if num.is_integer() else num
        except ValueError:
            pass
    return actual, expected


async def fetch_associated_resource(
    resource_type: str,
    resource_id: Any,
    context: dict[str, Any],
    include_relationships: bool = False,
    log_warning: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    """Fetch live resource state (and optionally dependencies/relationships) from Elder API."""
    norm_type = str(resource_type or "").strip().lower()
    endpoint_tpl = RESOURCE_DETAIL_ENDPOINTS.get(norm_type)
    if not endpoint_tpl or resource_id is None or str(resource_id).strip() == "":
        return None

    # Reuse per-execution cache when available
    global_cfg = context.get("global_config")
    cache: dict[str, dict[str, Any]] | None = None
    if isinstance(global_cfg, dict):
        cache = global_cfg.setdefault("_resource_lookup_cache", {})

    cache_key = f"{norm_type}:{resource_id}:rel={int(include_relationships)}"
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    base_url = _resolve_elder_api_base(context)
    safe_id = quote(str(resource_id).strip(), safe="")
    detail_url = f"{base_url}{endpoint_tpl.format(id=safe_id)}"
    headers: dict[str, str] = {"Accept": "application/json"}
    token = _resolve_elder_auth_token(context)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(detail_url, headers=headers)
            if resp.status_code != 200:
                if log_warning:
                    log_warning(
                        f"Resource state lookup failed for {norm_type}/{resource_id}: HTTP {resp.status_code}"
                    )
                return None
            payload = resp.json()
            resource_data: dict[str, Any] = (
                dict(payload) if isinstance(payload, dict) else {"value": payload}
            )

            # Expose convenient normalized state helpers for identities/users
            if norm_type in ("identity", "user") and "is_locked" not in resource_data:
                status_val = str(resource_data.get("status") or "").lower()
                is_active = resource_data.get("is_active")
                resource_data["is_locked"] = status_val in (
                    "locked",
                    "suspended",
                    "disabled",
                ) or (is_active is False)

            if include_relationships:
                dep_type = "identity" if norm_type == "user" else norm_type
                out_resp = await client.get(
                    f"{base_url}/api/v1/dependencies",
                    params={
                        "source_type": dep_type,
                        "source_id": str(resource_id).strip(),
                        "per_page": 200,
                    },
                    headers=headers,
                )
                in_resp = await client.get(
                    f"{base_url}/api/v1/dependencies",
                    params={
                        "target_type": dep_type,
                        "target_id": str(resource_id).strip(),
                        "per_page": 200,
                    },
                    headers=headers,
                )
                outgoing = (
                    (out_resp.json().get("items") or [])
                    if out_resp.status_code == 200 and isinstance(out_resp.json(), dict)
                    else []
                )
                incoming = (
                    (in_resp.json().get("items") or [])
                    if in_resp.status_code == 200 and isinstance(in_resp.json(), dict)
                    else []
                )
                all_items = [*outgoing, *incoming]
                related_ids: list[Any] = []
                related_types: list[str] = []
                dep_types: list[str] = []
                for dep in outgoing:
                    if isinstance(dep, dict):
                        if dep.get("target_id") is not None:
                            related_ids.append(dep["target_id"])
                        if dep.get("target_type"):
                            related_types.append(str(dep["target_type"]))
                        if dep.get("dependency_type"):
                            dep_types.append(str(dep["dependency_type"]))
                for dep in incoming:
                    if isinstance(dep, dict):
                        if dep.get("source_id") is not None:
                            related_ids.append(dep["source_id"])
                        if dep.get("source_type"):
                            related_types.append(str(dep["source_type"]))
                        if dep.get("dependency_type"):
                            dep_types.append(str(dep["dependency_type"]))

                rel_summary = {
                    "count": len(all_items),
                    "outgoing_count": len(outgoing),
                    "incoming_count": len(incoming),
                    "related_ids": related_ids,
                    "related_types": related_types,
                    "dependency_types": dep_types,
                    "items": all_items,
                }
                resource_data["relationships"] = rel_summary
                resource_data["dependencies"] = rel_summary

            if cache is not None:
                cache[cache_key] = resource_data
            return resource_data
    except Exception as e:
        if log_warning:
            log_warning(
                f"Resource state lookup error for {norm_type}/{resource_id}: {e}"
            )
        return None


def _collect_leaf_values(obj: Any, _depth: int = 0, _limit: int = 500) -> list[Any]:
    """Recursively collect scalar leaf values from nested dicts/lists for any-field search."""
    if _depth > 10:
        return []
    out: list[Any] = []

    def _walk(val: Any, depth: int) -> None:
        if len(out) >= _limit or depth > 10:
            return
        if isinstance(val, dict):
            for v in val.values():
                _walk(v, depth + 1)
        elif isinstance(val, (list, tuple, set)):
            for item in val:
                _walk(item, depth + 1)
        else:
            out.append(val)

    _walk(obj, _depth)
    return out


async def _eval_single_op(
    actual: Any,
    op_name: str,
    expected: Any,
    log_warning: Callable[[str], None] | None = None,
) -> bool:
    """Evaluate one operator against a single actual and expected value."""
    if op_name == "regex":
        try:
            return await safe_regex_search(str(expected), str(actual))
        except UnsafeRegexError as e:
            if log_warning:
                log_warning(f"Regex condition rejected: {e}")
            return False
        except TimeoutError:
            if log_warning:
                log_warning(
                    f"Regex condition timed out (possible catastrophic backtracking): "
                    f"{expected!r}"
                )
            return False
        except Exception:
            return False

    op_func = OPERATORS.get(op_name, operator.eq)
    actual_cmp, expected_cmp = _coerce_comparable(actual, expected)

    if op_name in ("contains", "not_contains") and not hasattr(
        actual_cmp, "__contains__"
    ):
        actual_cmp = "" if actual_cmp is None else str(actual_cmp)
    if op_name in ("contains", "not_contains") and isinstance(actual_cmp, str):
        expected_cmp = "" if expected_cmp is None else str(expected_cmp)

    try:
        return bool(op_func(actual_cmp, expected_cmp))
    except Exception:
        return False


async def evaluate_condition(
    data: Any,
    condition: dict[str, Any],
    context: dict[str, Any],
    inputs: dict[str, Any] | None = None,
    log_warning: Callable[[str], None] | None = None,
) -> bool:
    """Evaluate a single condition against upstream data, any field, or an associated resource.

    Supports:
    - ``source == "input"`` (default): left-hand ``field`` as dot-path into
      ``data``, a ``{{expression}}`` (including prior ``{{$node.*}}`` or
      ``{{$trigger.*}}`` fields), ``*`` (any field), or empty (``data`` itself).
    - ``source == "any_field"`` (or ``field == "*"``): bare-string / value search
      across all leaf values of the incoming payload and prior node outputs.
    - ``source == "resource"``: fetches live state (and relationships when
      ``field`` starts with ``relationships.`` or ``dependencies.``) of the
      condition's ``resource_type``/``resource_id`` (falling back to the node's
      ``associated_resource``).
    - Right-hand ``value`` as either a freeform literal string/number/list or a
      template expression (e.g. ``{{$json.expected_status}}``).
    - ``regex`` routed through ``safe_regex_search`` (thread offload + ReDoS
      guard).
    """
    source = str(condition.get("source") or "input").strip().lower()
    field = condition.get("field", "")
    op_name = condition.get("operator", "eq")
    raw_expected = condition.get("value")
    input_map = inputs if inputs is not None else {"in": data}
    expected = interpolate_value(raw_expected, context, input_map, preserve_type=True)

    # Bare-string / any-field search across incoming payload + prior node outputs
    if source == "any_field" or (source == "input" and str(field).strip() == "*"):
        leaves = _collect_leaf_values(data)
        prior_outputs = context.get("node_outputs")
        if isinstance(prior_outputs, dict) and prior_outputs:
            leaves.extend(_collect_leaf_values(prior_outputs))
        if not leaves:
            return op_name in ("is_null", "not_contains", "ne", "not_in")
        negative_op = op_name in ("not_contains", "ne", "not_in")
        results = [
            await _eval_single_op(leaf, op_name, expected, log_warning=log_warning)
            for leaf in leaves
        ]
        return all(results) if negative_op else any(results)

    eval_data = data
    if source == "resource":
        assoc = context.get("associated_resource") or {}
        res_type = condition.get("resource_type") or assoc.get("resource_type")
        raw_res_id = condition.get("resource_id")
        if raw_res_id is None or raw_res_id == "":
            raw_res_id = assoc.get("resource_id")
        res_id = interpolate_value(raw_res_id, context, input_map, preserve_type=True)
        needs_rel = isinstance(field, str) and (
            field.startswith("relationships") or field.startswith("dependencies")
        )
        eval_data = await fetch_associated_resource(
            str(res_type or ""),
            res_id,
            context,
            include_relationships=needs_rel,
            log_warning=log_warning,
        )
        if eval_data is None:
            return op_name == "is_null"

    if not field:
        actual = eval_data
    elif isinstance(field, str) and "{{" in field:
        actual = interpolate_value(field, context, input_map, preserve_type=True)
    elif isinstance(eval_data, dict):
        actual = get_field_value(eval_data, field)
    else:
        actual = None

    return await _eval_single_op(actual, op_name, expected, log_warning=log_warning)


@register_node("conditional_if_then", "conditionals", "If-Then")
class IfThenConditional(BaseNode):
    """Conditional branching node that routes to true or false outputs."""

    node_type = "conditional_if_then"
    name = "If-Then"
    description = "Evaluate conditions and route execution to true or false outputs"
    category = "conditionals"

    @classmethod
    def inputs(cls) -> list[dict[str, Any]]:
        """Define input ports for the conditional node."""
        return [
            {
                "name": "in",
                "description": "Input data to evaluate against conditions",
                "required": True,
                "data_type": "any",
            },
        ]

    @classmethod
    def outputs(cls) -> list[dict[str, Any]]:
        """Define output ports for the conditional node."""
        return [
            {
                "name": "true",
                "description": "Output when condition evaluates to true",
                "data_type": "any",
            },
            {
                "name": "false",
                "description": "Output when condition evaluates to false",
                "data_type": "any",
            },
        ]

    def _get_field_value(self, data: Any, field: str) -> Any:
        """Get nested field value using dot notation."""
        return get_field_value(data, field)

    async def _evaluate_condition(self, data: Any, condition: dict) -> bool:
        """Evaluate a single condition against data."""
        return await evaluate_condition(
            data,
            condition,
            self.context,
            {"in": data},
            log_warning=self.log_warning,
        )

    async def _matches(self, data: Any, conditions: list[dict], logic: str) -> bool:
        """Check if data matches all or any conditions based on logic."""
        if not conditions:
            return True

        results = [await self._evaluate_condition(data, c) for c in conditions]

        if logic == "and":
            return all(results)
        else:  # or
            return any(results)

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute the if-then conditional."""
        start_time = time.perf_counter()

        input_data = inputs.get("in")
        conditions = self.get_config_value("conditions", [])
        logic = self.get_config_value("logic", "and")

        # Evaluate conditions
        result = await self._matches(input_data, conditions, logic)

        # Route to appropriate output
        if result:
            outputs = {
                "true": {
                    "data": input_data,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "false": {
                    "data": None,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
            }
            self.log_info("Condition evaluated to true")
        else:
            outputs = {
                "true": {
                    "data": None,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "false": {
                    "data": input_data,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
            }
            self.log_info("Condition evaluated to false")

        return outputs
