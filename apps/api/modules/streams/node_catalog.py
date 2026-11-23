"""Unified Playbook Node Catalog & Live Validator.

Combines all built-in generic workflow nodes (triggers, transforms, conditionals,
actions) with Elder platform nodes loaded from the Elder connector manifest, and
evaluates per-tenant availability live against:
- Deployed modules (`app.extensions["elder_modules"]` / `resolve_enabled`)
- License checks (`module_licensed` and node `required_license_feature`)
- Per-tenant module toggles (`is_module_enabled`)
- PostHog feature flags (`flag_enabled`)
- Live mounted API routes (`app.url_map`, excluding `web.spa_catch_all`)
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from apps.api.common.flags.posthog_client import flag_enabled
from apps.api.common.modules.licensing import is_bypass_domain, module_licensed
from apps.api.common.modules.tenant_toggle import is_module_enabled
from apps.api.modules import MODULES
from apps.api.modules.registry import resolve_enabled
from apps.worker.streams.connectors.base import ConnectorManifest
from apps.worker.streams.connectors.registry import discover_connectors
from apps.worker.streams.executor.graph_utils import (
    CycleDetectedError,
    TopologicalSorter,
)
from apps.worker.streams.executor.node_registry import NodeRegistry, discover_nodes

_MODULES_BY_NAME = {m.name: m for m in MODULES}

_FILTER_OPERATOR_OPTIONS = [
    {"value": "eq", "label": "Equals (=)"},
    {"value": "ne", "label": "Not equals (!=)"},
    {"value": "gt", "label": "Greater than (>)"},
    {"value": "gte", "label": "Greater or equal (>=)"},
    {"value": "lt", "label": "Less than (<)"},
    {"value": "lte", "label": "Less or equal (<=)"},
    {"value": "contains", "label": "Contains"},
    {"value": "not_contains", "label": "Does not contain"},
    {"value": "starts_with", "label": "Starts with"},
    {"value": "ends_with", "label": "Ends with"},
    {"value": "regex", "label": "Matches regex"},
    {"value": "is_null", "label": "Is null / empty"},
    {"value": "is_not_null", "label": "Is not null"},
    {"value": "in", "label": "In list"},
    {"value": "not_in", "label": "Not in list"},
]

_AUTH_TYPE_OPTIONS = [
    {"value": "none", "label": "None"},
    {"value": "bearer", "label": "Bearer Token"},
    {"value": "basic", "label": "Basic Auth"},
    {"value": "api_key", "label": "API Key Header"},
]

_ELDER_MODULE_COLORS: dict[str, str] = {
    "core": "#0ea5e9",
    "infrastructure": "#6366f1",
    "ipam": "#06b6d4",
    "sbom": "#10b981",
    "services_oncall": "#ef4444",
    "issues": "#f97316",
    "discovery": "#3b82f6",
    "secrets": "#eab308",
    "webhooks_alerting": "#ec4899",
    "access_reviews": "#14b8a6",
    "documents": "#64748b",
    "pages": "#84cc16",
    "diagrams": "#a855f7",
    "streams": "#8b5cf6",
    "flows": "#0ea5e9",
    "helpdesk": "#f43f5e",
    "ai": "#c026d3",
}


def _cf(
    field: str,
    label: str,
    field_type: str = "string",
    *,
    required: bool = False,
    default: Any = None,
    placeholder: str = "",
    description: str = "",
    options: list[str] | list[dict[str, str]] | None = None,
    supports_expressions: bool = False,
    show_when: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a normalized config field descriptor."""
    normalized_options: list[dict[str, str]] | None = None
    if options is not None:
        normalized_options = [
            opt if isinstance(opt, dict) else {"value": str(opt), "label": str(opt)}
            for opt in options
        ]
    return {
        "field": field,
        "label": label,
        "type": field_type,
        "required": required,
        "default": default,
        "placeholder": placeholder,
        "description": description,
        "options": normalized_options,
        "supports_expressions": supports_expressions,
        "show_when": show_when,
    }


BUILTIN_UI_META: dict[str, dict[str, Any]] = {
    "trigger_manual": {
        "stage": "triggers",
        "subcategory": "Core Triggers",
        "icon": "Play",
        "color": "#10b981",
        "config_schema": [
            _cf(
                "sample_payload",
                "Sample / Default Input JSON",
                "json",
                default={},
                placeholder='{"entity_id": 42, "severity": "high"}',
                description=(
                    "Default payload emitted on manual runs; top-level keys become "
                    "clickable $json and $trigger field pills in downstream nodes."
                ),
            ),
        ],
        "output_schema": [
            {
                "name": "trigger",
                "type": "string",
                "description": "Trigger identifier ('manual')",
            },
            {
                "name": "timestamp",
                "type": "string",
                "description": "Execution UTC ISO timestamp",
            },
        ],
    },
    "trigger_webhook": {
        "stage": "triggers",
        "subcategory": "Core Triggers",
        "icon": "Webhook",
        "color": "#10b981",
        "config_schema": [
            _cf(
                "sample_payload",
                "Expected Payload Schema / Sample JSON",
                "json",
                default={},
                placeholder='{"event": "alert.fired", "service": "api"}',
                description=(
                    "Optional sample JSON describing expected webhook keys for "
                    "downstream field pills."
                ),
            ),
        ],
        "output_schema": [
            {
                "name": "payload",
                "type": "object",
                "description": "Decoded inbound webhook JSON body",
            },
            {
                "name": "headers",
                "type": "object",
                "description": "Sanitized inbound HTTP headers",
            },
            {
                "name": "source_ip",
                "type": "string",
                "description": "Caller remote IP address",
            },
            {
                "name": "received_at",
                "type": "string",
                "description": "Timestamp when webhook arrived",
            },
        ],
    },
    "trigger_schedule": {
        "stage": "triggers",
        "subcategory": "Core Triggers",
        "icon": "Clock",
        "color": "#10b981",
        "config_schema": [
            _cf(
                "cron_expression",
                "Cron Expression",
                "string",
                required=True,
                default="0 * * * *",
                placeholder="0 * * * *",
                description="5-field cron schedule (minute hour day month weekday).",
            ),
            _cf(
                "timezone",
                "Timezone",
                "string",
                default="UTC",
                placeholder="UTC",
                description="IANA timezone identifier.",
            ),
            _cf(
                "static_payload",
                "Static Payload JSON",
                "json",
                default={},
                description="Optional static JSON keys injected on each run.",
            ),
        ],
        "output_schema": [
            {
                "name": "scheduled_at",
                "type": "string",
                "description": "Execution UTC ISO timestamp",
            },
            {
                "name": "cron_expression",
                "type": "string",
                "description": "Configured cron expression",
            },
            {
                "name": "timezone",
                "type": "string",
                "description": "Configured timezone",
            },
        ],
    },
    "transform_set_fields": {
        "stage": "process",
        "subcategory": "Data Transform",
        "icon": "SlidersHorizontal",
        "color": "#f59e0b",
        "config_schema": [
            _cf(
                "assignments",
                "Field Assignments",
                "set_fields",
                default=[],
                supports_expressions=True,
                description="Assign output fields using literals or {{$json.*}} expressions.",
            ),
            _cf(
                "keep_only_set",
                "Keep Only Assigned Fields",
                "boolean",
                default=False,
                description="If enabled, drop incoming fields not explicitly set above.",
            ),
        ],
        "output_schema": [],
    },
    "transform_filter": {
        "stage": "process",
        "subcategory": "Data Transform",
        "icon": "Filter",
        "color": "#f59e0b",
        "config_schema": [
            _cf(
                "logic",
                "Combine Conditions",
                "select",
                default="and",
                options=[
                    {"value": "and", "label": "All conditions match (AND)"},
                    {"value": "or", "label": "Any condition matches (OR)"},
                ],
            ),
            _cf(
                "conditions",
                "Filter Conditions",
                "conditions",
                default=[],
                supports_expressions=True,
                description="Matching items route to 'out'; non-matching route to 'rejected'.",
            ),
        ],
        "output_schema": [],
    },
    "transform_expression": {
        "stage": "process",
        "subcategory": "Data Transform",
        "icon": "Code",
        "color": "#f59e0b",
        "config_schema": [
            _cf(
                "expression",
                "Expression",
                "textarea",
                required=True,
                default="data",
                placeholder="data.get('severity') == 'critical'",
                supports_expressions=True,
                description="Safe Python-like expression evaluated against incoming 'data'.",
            ),
        ],
        "output_schema": [
            {
                "name": "result",
                "type": "any",
                "description": "Evaluated expression result",
            }
        ],
    },
    "transform_json": {
        "stage": "process",
        "subcategory": "Data Transform",
        "icon": "Braces",
        "color": "#f59e0b",
        "config_schema": [
            _cf(
                "operation",
                "Operation",
                "select",
                required=True,
                default="extract",
                options=[
                    "extract",
                    "set",
                    "delete",
                    "rename",
                    "merge",
                    "flatten",
                    "unflatten",
                ],
            ),
            _cf(
                "jsonPath",
                "JSON / Dot Path",
                "string",
                required=True,
                placeholder="items[0].name",
                supports_expressions=True,
                show_when={"field": "operation", "in": ["extract", "set", "delete"]},
            ),
            _cf(
                "value",
                "Value (JSON or string)",
                "json",
                required=True,
                supports_expressions=True,
                show_when={"field": "operation", "equals": "set"},
            ),
            _cf(
                "fromPath",
                "Source Path",
                "string",
                required=True,
                placeholder="old_key",
                show_when={"field": "operation", "equals": "rename"},
            ),
            _cf(
                "toPath",
                "Destination Path",
                "string",
                required=True,
                placeholder="new_key",
                show_when={"field": "operation", "equals": "rename"},
            ),
            _cf(
                "mergeData",
                "Merge JSON Object",
                "json",
                default={},
                show_when={"field": "operation", "equals": "merge"},
            ),
            _cf(
                "separator",
                "Key Separator",
                "string",
                default=".",
                show_when={"field": "operation", "in": ["flatten", "unflatten"]},
            ),
        ],
        "output_schema": [],
    },
    "transform_merge": {
        "stage": "process",
        "subcategory": "Flow Control",
        "icon": "GitMerge",
        "color": "#f59e0b",
        "config_schema": [
            _cf(
                "mode",
                "Merge Mode",
                "select",
                default="object",
                options=["object", "array", "concat", "deep"],
            ),
            _cf(
                "separator",
                "String Separator",
                "string",
                default="",
                show_when={"field": "mode", "equals": "concat"},
            ),
        ],
        "output_schema": [],
    },
    "transform_split": {
        "stage": "process",
        "subcategory": "Flow Control",
        "icon": "Split",
        "color": "#f59e0b",
        "config_schema": [
            _cf(
                "mode",
                "Split Mode",
                "select",
                default="array",
                options=["array", "string", "chunks", "field"],
            ),
            _cf(
                "delimiter",
                "Delimiter",
                "string",
                required=True,
                default=",",
                show_when={"field": "mode", "equals": "string"},
            ),
            _cf(
                "trim",
                "Trim Items",
                "boolean",
                default=True,
                show_when={"field": "mode", "equals": "string"},
            ),
            _cf(
                "removeEmpty",
                "Remove Empty Items",
                "boolean",
                default=False,
                show_when={"field": "mode", "equals": "string"},
            ),
            _cf(
                "chunkSize",
                "Chunk Size",
                "number",
                required=True,
                default=10,
                show_when={"field": "mode", "equals": "chunks"},
            ),
            _cf(
                "field",
                "Array Field Path",
                "string",
                required=True,
                placeholder="items",
                supports_expressions=True,
                show_when={"field": "mode", "equals": "field"},
            ),
        ],
        "output_schema": [
            {"name": "count", "type": "number", "description": "Number of split items"},
            {"name": "first", "type": "any", "description": "First split item"},
            {"name": "last", "type": "any", "description": "Last split item"},
        ],
    },
    "transform_delay": {
        "stage": "process",
        "subcategory": "Flow Control",
        "icon": "Timer",
        "color": "#f59e0b",
        "config_schema": [
            _cf(
                "delaySeconds",
                "Delay (Seconds)",
                "number",
                default=1,
                description="Pause execution (max 300 seconds).",
            ),
            _cf(
                "delayMs",
                "Delay (Milliseconds override)",
                "number",
                description="Optional millisecond delay (takes precedence over seconds).",
            ),
        ],
        "output_schema": [],
    },
    "conditional_if_then": {
        "stage": "process",
        "subcategory": "Logic & Branching",
        "icon": "GitBranch",
        "color": "#8b5cf6",
        "config_schema": [
            _cf(
                "logic",
                "Match Logic",
                "select",
                default="and",
                options=[
                    {"value": "and", "label": "All conditions match (AND)"},
                    {"value": "or", "label": "Any condition matches (OR)"},
                ],
            ),
            _cf(
                "conditions",
                "Conditions",
                "conditions",
                default=[],
                supports_expressions=True,
                description="Evaluate 1 or more conditions against upstream fields/freeform values OR associated resource states and relationships (e.g. user is_locked = true). Routes full payload to 'true' or 'false' branch.",
            ),
        ],
        "output_schema": [],
    },
    "conditional_switch": {
        "stage": "process",
        "subcategory": "Logic & Branching",
        "icon": "GitFork",
        "color": "#8b5cf6",
        "config_schema": [
            _cf(
                "field",
                "Field Path",
                "string",
                required=True,
                placeholder="severity",
                supports_expressions=True,
            ),
            _cf(
                "cases",
                "Switch Cases",
                "switch_cases",
                required=True,
                default=[{"value": "", "output": "case1"}],
                description="Match values to output ports case1-case4 or default.",
            ),
        ],
        "output_schema": [],
    },
    "conditional_equals": {
        "stage": "process",
        "subcategory": "Logic & Branching",
        "icon": "Equal",
        "color": "#8b5cf6",
        "config_schema": [
            _cf(
                "field",
                "Field Path (when using 'in' wire)",
                "string",
                placeholder="status",
                supports_expressions=True,
            ),
            _cf(
                "expected",
                "Expected Value",
                "string",
                placeholder="open",
                supports_expressions=True,
            ),
        ],
        "output_schema": [],
    },
    "conditional_greater_than": {
        "stage": "process",
        "subcategory": "Logic & Branching",
        "icon": "ChevronRight",
        "color": "#8b5cf6",
        "config_schema": [
            _cf(
                "field",
                "Numeric Field Path",
                "string",
                placeholder="severity_score",
                supports_expressions=True,
            ),
            _cf(
                "threshold",
                "Threshold",
                "number",
                default=0,
                supports_expressions=True,
            ),
        ],
        "output_schema": [],
    },
    "conditional_less_than": {
        "stage": "process",
        "subcategory": "Logic & Branching",
        "icon": "ChevronLeft",
        "color": "#8b5cf6",
        "config_schema": [
            _cf(
                "field",
                "Numeric Field Path",
                "string",
                placeholder="score",
                supports_expressions=True,
            ),
            _cf(
                "threshold",
                "Threshold",
                "number",
                default=0,
                supports_expressions=True,
            ),
        ],
        "output_schema": [],
    },
    "conditional_contains": {
        "stage": "process",
        "subcategory": "Logic & Branching",
        "icon": "Search",
        "color": "#8b5cf6",
        "config_schema": [
            _cf(
                "field",
                "Text / Array Field Path",
                "string",
                placeholder="title",
                supports_expressions=True,
            ),
            _cf(
                "needle",
                "Substring or Value",
                "string",
                placeholder="critical",
                supports_expressions=True,
            ),
        ],
        "output_schema": [],
    },
    "conditional_regex": {
        "stage": "process",
        "subcategory": "Logic & Branching",
        "icon": "Regex",
        "color": "#8b5cf6",
        "config_schema": [
            _cf(
                "field",
                "Text Field Path",
                "string",
                placeholder="hostname",
                supports_expressions=True,
            ),
            _cf(
                "pattern",
                "Regex Pattern",
                "string",
                placeholder="^prod-[0-9]+",
                supports_expressions=True,
            ),
        ],
        "output_schema": [],
    },
    "conditional_and": {
        "stage": "process",
        "subcategory": "Logic Gates",
        "icon": "Workflow",
        "color": "#8b5cf6",
        "config_schema": [
            _cf(
                "conditions",
                "Conditions (ALL must match)",
                "conditions",
                default=[],
                supports_expressions=True,
                description="Add 1 or more conditions comparing an upstream field (or {{expression}}) against a freeform value or upstream field. Leave empty to evaluate wired boolean ports in1-in4.",
            ),
        ],
        "output_schema": [],
    },
    "conditional_or": {
        "stage": "process",
        "subcategory": "Logic Gates",
        "icon": "Workflow",
        "color": "#8b5cf6",
        "config_schema": [
            _cf(
                "conditions",
                "Conditions (ANY must match)",
                "conditions",
                default=[],
                supports_expressions=True,
                description="Add 1 or more conditions comparing an upstream field (or {{expression}}) against a freeform value or upstream field. Leave empty to evaluate wired boolean ports in1-in4.",
            ),
        ],
        "output_schema": [],
    },
    "conditional_not": {
        "stage": "process",
        "subcategory": "Logic Gates",
        "icon": "Ban",
        "color": "#8b5cf6",
        "config_schema": [
            _cf(
                "logic",
                "Negation Mode",
                "select",
                default="and",
                options=[
                    {
                        "value": "and",
                        "label": "Invert when ALL conditions match (NOT AND)",
                    },
                    {
                        "value": "or",
                        "label": "Invert when ANY condition matches (NOT OR)",
                    },
                ],
                description="Applies when 1 or more conditions are configured below.",
            ),
            _cf(
                "conditions",
                "Conditions to Negate",
                "conditions",
                default=[],
                supports_expressions=True,
                description="Routes payload to 'true' when the condition(s) do NOT match. Compare an upstream field (or {{expression}}) against a freeform value or upstream field. Leave empty to invert wired boolean input.",
            ),
        ],
        "output_schema": [],
    },
    "conditional_for_each": {
        "stage": "process",
        "subcategory": "Loops",
        "icon": "Repeat",
        "color": "#8b5cf6",
        "config_schema": [
            _cf(
                "field",
                "Array Field Path",
                "string",
                placeholder="items",
                supports_expressions=True,
            ),
        ],
        "output_schema": [
            {"name": "item", "type": "any", "description": "Current item"},
            {"name": "index", "type": "number", "description": "0-based index"},
            {"name": "done", "type": "boolean", "description": "Completion flag"},
        ],
    },
    "conditional_while": {
        "stage": "process",
        "subcategory": "Loops",
        "icon": "RotateCw",
        "color": "#8b5cf6",
        "config_schema": [
            _cf(
                "logic",
                "Match Logic",
                "select",
                default="and",
                options=[
                    {
                        "value": "and",
                        "label": "Continue while ALL conditions match (AND)",
                    },
                    {
                        "value": "or",
                        "label": "Continue while ANY condition matches (OR)",
                    },
                ],
            ),
            _cf(
                "conditions",
                "Loop Conditions",
                "conditions",
                default=[],
                supports_expressions=True,
                description="Continue looping while these conditions match (supports upstream/prior fields, bare string search, or associated resource states & relationships).",
            ),
            _cf("maxIterations", "Max Iterations", "number", default=100),
        ],
        "output_schema": [],
    },
    "action_http_request": {
        "stage": "actions",
        "subcategory": "HTTP & Webhooks",
        "icon": "Globe",
        "color": "#3b82f6",
        "config_schema": [
            _cf(
                "method",
                "HTTP Method",
                "select",
                default="GET",
                options=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
            ),
            _cf(
                "url",
                "Request URL",
                "string",
                required=True,
                placeholder="https://api.example.com/v1/items/{{$json.id}}",
                supports_expressions=True,
            ),
            _cf(
                "authType",
                "Authentication",
                "select",
                default="none",
                options=_AUTH_TYPE_OPTIONS,
            ),
            _cf(
                "bearerToken",
                "Bearer Token",
                "password",
                supports_expressions=True,
                show_when={"field": "authType", "equals": "bearer"},
            ),
            _cf(
                "basicUsername",
                "Username",
                "string",
                show_when={"field": "authType", "equals": "basic"},
            ),
            _cf(
                "basicPassword",
                "Password",
                "password",
                show_when={"field": "authType", "equals": "basic"},
            ),
            _cf(
                "apiKeyHeader",
                "API Key Header",
                "string",
                default="X-API-Key",
                show_when={"field": "authType", "equals": "api_key"},
            ),
            _cf(
                "apiKeyValue",
                "API Key Value",
                "password",
                show_when={"field": "authType", "equals": "api_key"},
            ),
            _cf("headers", "Headers", "kv", default={}, supports_expressions=True),
            _cf(
                "params",
                "Query Parameters",
                "kv",
                default={},
                supports_expressions=True,
            ),
            _cf(
                "body",
                "JSON Body",
                "json",
                supports_expressions=True,
                show_when={"field": "method", "in": ["POST", "PUT", "PATCH"]},
            ),
            _cf("timeout", "Timeout (s)", "number", default=30),
            _cf("maxRetries", "Max Retries", "number", default=3),
        ],
        "output_schema": [
            {"name": "status", "type": "number", "description": "HTTP status code"},
            {"name": "headers", "type": "object", "description": "Response headers"},
            {"name": "body", "type": "any", "description": "Response body"},
            {
                "name": "response",
                "type": "object",
                "description": "Structured HTTP response envelope",
            },
        ],
    },
    "action_webhook_out": {
        "stage": "actions",
        "subcategory": "HTTP & Webhooks",
        "icon": "Send",
        "color": "#3b82f6",
        "config_schema": [
            _cf(
                "url",
                "Webhook URL",
                "string",
                required=True,
                placeholder="https://hooks.example.com/services/...",
                supports_expressions=True,
            ),
            _cf(
                "payload",
                "Webhook Payload",
                "json",
                supports_expressions=True,
                description="JSON payload sent to target URL (defaults to upstream input if empty).",
            ),
            _cf(
                "authType",
                "Authentication",
                "select",
                default="none",
                options=_AUTH_TYPE_OPTIONS,
            ),
            _cf(
                "bearerToken",
                "Bearer Token",
                "password",
                supports_expressions=True,
                show_when={"field": "authType", "equals": "bearer"},
            ),
            _cf(
                "basicUsername",
                "Username",
                "string",
                show_when={"field": "authType", "equals": "basic"},
            ),
            _cf(
                "basicPassword",
                "Password",
                "password",
                show_when={"field": "authType", "equals": "basic"},
            ),
            _cf(
                "apiKeyHeader",
                "API Key Header",
                "string",
                default="X-API-Key",
                show_when={"field": "authType", "equals": "api_key"},
            ),
            _cf(
                "apiKeyValue",
                "API Key Value",
                "password",
                show_when={"field": "authType", "equals": "api_key"},
            ),
            _cf(
                "headers", "Custom Headers", "kv", default={}, supports_expressions=True
            ),
            _cf("timeout", "Timeout (s)", "number", default=30),
            _cf("maxRetries", "Max Retries", "number", default=3),
        ],
        "output_schema": [
            {"name": "success", "type": "boolean", "description": "Delivery success"},
            {"name": "status", "type": "number", "description": "HTTP status code"},
            {"name": "message", "type": "string", "description": "Status message"},
        ],
    },
    "action_log": {
        "stage": "actions",
        "subcategory": "Utilities & Response",
        "icon": "FileText",
        "color": "#3b82f6",
        "config_schema": [
            _cf(
                "level",
                "Log Level",
                "select",
                default="INFO",
                options=["DEBUG", "INFO", "WARNING", "ERROR"],
            ),
            _cf(
                "message",
                "Message",
                "textarea",
                required=True,
                placeholder="Issue {{$json.title}} opened for {{$json.entity_id}}",
                supports_expressions=True,
            ),
            _cf("includeData", "Include Upstream Payload", "boolean", default=True),
        ],
        "output_schema": [
            {"name": "logged", "type": "boolean", "description": "Log write status"}
        ],
    },
    "action_respond": {
        "stage": "actions",
        "subcategory": "Utilities & Response",
        "icon": "CheckCircle2",
        "color": "#3b82f6",
        "config_schema": [
            _cf("status_code", "Status Code", "number", default=200),
            _cf(
                "summary",
                "Response Summary",
                "string",
                placeholder="Completed remediation for {{$json.id}}",
                supports_expressions=True,
            ),
            _cf(
                "response_body",
                "Response Body JSON",
                "json",
                supports_expressions=True,
                description="Explicit structured response body (defaults to upstream payload).",
            ),
        ],
        "output_schema": [
            {"name": "status_code", "type": "number", "description": "HTTP status"},
            {"name": "summary", "type": "string", "description": "Summary string"},
            {"name": "body", "type": "any", "description": "Formatted response body"},
        ],
    },
    "action_s3": {
        "stage": "actions",
        "subcategory": "Storage & Files",
        "icon": "HardDrive",
        "color": "#10b981",
        "config_schema": [
            _cf(
                "operation",
                "Operation",
                "select",
                required=True,
                default="fetch",
                options=[
                    {"value": "fetch", "label": "Fetch JSON"},
                    {"value": "push", "label": "Push JSON"},
                ],
                description="Fetch a .json object from S3 or push a JSON payload to S3.",
            ),
            _cf(
                "bucket",
                "Bucket",
                "string",
                required=True,
                placeholder="my-elder-bucket",
                supports_expressions=True,
                description="Target S3 bucket name.",
            ),
            _cf(
                "key",
                "Object Key",
                "string",
                required=True,
                placeholder="exports/{{$json.id}}.json",
                supports_expressions=True,
                description="S3 object key (must end with .json for fetch).",
            ),
            _cf(
                "region",
                "Region",
                "string",
                default="us-east-1",
                placeholder="us-east-1",
                supports_expressions=True,
                description="AWS region (default: us-east-1).",
            ),
            _cf(
                "endpoint_url",
                "Custom Endpoint URL",
                "string",
                placeholder="https://minio.example.com",
                supports_expressions=True,
                description=(
                    "Optional S3-compatible endpoint URL (MinIO, Cloudflare R2, "
                    "DigitalOcean Spaces). Must start with http:// or https://."
                ),
            ),
            _cf(
                "access_key_id",
                "Access Key ID",
                "string",
                placeholder="Leave blank to use default AWS/IRSA credentials",
                supports_expressions=True,
                description="Optional static AWS access key ID.",
            ),
            _cf(
                "secret_access_key",
                "Secret Access Key",
                "password",
                placeholder="Leave blank to use default AWS/IRSA credentials",
                supports_expressions=True,
                description="Optional static AWS secret access key (never logged).",
            ),
            _cf(
                "payload",
                "JSON Payload",
                "json",
                supports_expressions=True,
                show_when={"field": "operation", "equals": "push"},
                description=(
                    "JSON payload to upload (defaults to upstream 'in' payload if empty)."
                ),
            ),
        ],
        "output_schema": [
            {
                "name": "operation",
                "type": "string",
                "description": "Executed S3 operation ('fetch' or 'push')",
            },
            {"name": "bucket", "type": "string", "description": "Target S3 bucket"},
            {"name": "key", "type": "string", "description": "Target S3 object key"},
            {"name": "etag", "type": "string", "description": "S3 object ETag"},
            {
                "name": "data",
                "type": "any",
                "description": "Parsed JSON document returned on fetch",
            },
            {
                "name": "bytes_read",
                "type": "number",
                "description": "Bytes downloaded on fetch",
            },
            {
                "name": "bytes_written",
                "type": "number",
                "description": "UTF-8 JSON bytes uploaded on push",
            },
        ],
    },
}

BUILTIN_OPERATOR_OPTIONS = _FILTER_OPERATOR_OPTIONS


def _category_to_stage(category: str) -> str:
    if category == "triggers":
        return "triggers"
    if category in ("transforms", "conditionals"):
        return "process"
    return "actions"


def _module_display_name(
    module_name: str, installed_modules: dict[str, Any] | None
) -> str:
    if module_name == "core":
        return "Core Platform"
    if installed_modules and module_name in installed_modules:
        return str(installed_modules[module_name].title)
    if module_name in _MODULES_BY_NAME:
        return _MODULES_BY_NAME[module_name].title
    return module_name.replace("_", " ").title()


def _serialize_config_schema(raw_schema: Any) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for item in raw_schema or []:
        if isinstance(item, dict):
            serialized.append(item)
            continue
        raw_options = getattr(item, "options", None)
        normalized_options = None
        if raw_options is not None:
            normalized_options = [
                opt if isinstance(opt, dict) else {"value": str(opt), "label": str(opt)}
                for opt in raw_options
            ]
        serialized.append(
            {
                "field": item.field,
                "label": item.label,
                "type": item.type,
                "required": bool(item.required),
                "default": item.default,
                "placeholder": item.placeholder or "",
                "description": item.description or "",
                "options": normalized_options,
                "supports_expressions": True,
                "show_when": getattr(item, "show_when", None),
            }
        )
    return serialized


def _strip_placeholder(seg: str) -> bool:
    return (
        (seg.startswith("{{") and seg.endswith("}}"))
        or (seg.startswith("{") and seg.endswith("}"))
        or (seg.startswith("<") and seg.endswith(">"))
    )


def _match_route_segments(rule_rule: str, endpoint: str) -> bool:
    clean_endpoint = endpoint.split("?", 1)[0].strip("/")
    clean_rule = rule_rule.split("?", 1)[0].strip("/")
    ep_segs = [s for s in clean_endpoint.split("/") if s]
    rule_segs = [s for s in clean_rule.split("/") if s]

    idx = 0
    while idx < len(rule_segs):
        r_seg = rule_segs[idx]
        if r_seg.startswith("<path:") and r_seg.endswith(">"):
            # Matches one or more remaining endpoint segments
            return idx < len(ep_segs)
        if idx >= len(ep_segs):
            return False
        e_seg = ep_segs[idx]
        if r_seg.startswith("<") and r_seg.endswith(">"):
            if not e_seg:
                return False
        elif _strip_placeholder(e_seg):
            return False
        elif r_seg != e_seg:
            return False
        idx += 1

    return len(ep_segs) == len(rule_segs)


def is_route_mounted(app: Any, method: str, endpoint: str) -> bool:
    """Verify against `app.url_map` that an Elder API endpoint & method is mounted.

    Explicitly excludes `web.spa_catch_all` (`/<path:path>`) so unmounted
    `/api/v1/...` routes cannot be masked by the frontend SPA fallback.
    """
    if not endpoint:
        return True
    url_map = getattr(app, "url_map", None)
    if url_map is None:
        return True

    target_method = (method or "GET").upper()
    for rule in url_map.iter_rules():
        if rule.endpoint == "web.spa_catch_all":
            continue
        if not rule.rule.startswith("/api/"):
            continue
        methods = {m.upper() for m in (rule.methods or set())}
        if target_method not in methods:
            continue
        if _match_route_segments(rule.rule, endpoint):
            return True
    return False


def _evaluate_node_availability(
    app: Any,
    db: Any,
    tenant_id: int | None,
    *,
    required_module: str | None,
    required_license_feature: str | None,
    required_feature_flag: str | None,
    endpoint: str | None,
    method: str | None,
) -> tuple[bool, str | None, str | None]:
    """Return `(enabled, disabled_code, disabled_reason)` for one node."""
    extensions = getattr(app, "extensions", None) or {}
    installed_modules: dict[str, Any] | None = extensions.get("elder_modules")

    if required_module and required_module != "core":
        if installed_modules is not None:
            manifest = installed_modules.get(required_module)
            if manifest is None:
                return (
                    False,
                    "MODULE_NOT_INSTALLED",
                    f"Module '{required_module}' is not installed in this deployment",
                )
        else:
            env_enabled = {m.name: m for m in resolve_enabled(os.environ)}
            manifest = env_enabled.get(required_module)
            if manifest is None:
                return (
                    False,
                    "MODULE_NOT_INSTALLED",
                    f"Module '{required_module}' is not installed in this deployment",
                )

        if manifest is not None and not module_licensed(app, manifest):
            return (
                False,
                "MODULE_UNLICENSED",
                (
                    f"Module '{manifest.title}' requires license feature "
                    f"'{manifest.license_feature}'"
                ),
            )

        if required_license_feature and not is_bypass_domain():
            license_client = extensions.get("license_client")
            if license_client is not None and not bool(
                license_client.check_feature(required_license_feature)
            ):
                return (
                    False,
                    "MODULE_UNLICENSED",
                    f"Node requires license feature '{required_license_feature}'",
                )

        if db is not None and tenant_id is not None:
            module_redis = extensions.get("module_redis")
            default_enabled = bool(getattr(manifest, "default_enabled", True))
            if not is_module_enabled(
                db, module_redis, tenant_id, required_module, default_enabled
            ):
                return (
                    False,
                    "MODULE_DISABLED",
                    f"Module '{required_module}' is disabled for tenant {tenant_id}",
                )
    elif required_license_feature and not is_bypass_domain():
        license_client = extensions.get("license_client")
        if license_client is not None and not bool(
            license_client.check_feature(required_license_feature)
        ):
            return (
                False,
                "MODULE_UNLICENSED",
                f"Node requires license feature '{required_license_feature}'",
            )

    if required_feature_flag:
        distinct_id = str(tenant_id or "global")
        ph = extensions.get("posthog")
        is_flag_on = (
            bool(ph.flag_enabled(required_feature_flag, distinct_id, default=False))
            if ph is not None
            else bool(flag_enabled(required_feature_flag, distinct_id, default=False))
        )
        if not is_flag_on:
            return (
                False,
                "FLAG_DISABLED",
                f"Feature flag '{required_feature_flag}' is disabled",
            )

    if endpoint and not is_route_mounted(app, method or "GET", endpoint):
        return (
            False,
            "ROUTE_NOT_MOUNTED",
            f"Endpoint {(method or 'GET').upper()} {endpoint} is not mounted",
        )

    return True, None, None


def _build_elder_definition_lookup() -> dict[str, dict[str, Any]]:
    """Index raw Trigger/Transform/Action definitions from `elder` manifest."""
    discover_connectors()
    import apps.worker.streams.connectors.registry as conn_registry

    elder_yaml = Path(conn_registry.__file__).parent / "manifests" / "elder.yaml"
    if not elder_yaml.exists():
        return {}
    manifest = ConnectorManifest.from_yaml(str(elder_yaml))

    lookup: dict[str, dict[str, Any]] = {}
    for trig in manifest.triggers:
        nt = f"trigger_elder_{trig.id}"
        lookup[nt] = {
            "definition": trig,
            "endpoint": None,
            "method": None,
        }
    for trans in manifest.transforms:
        nt = f"transform_elder_{trans.id}"
        lookup[nt] = {
            "definition": trans,
            "endpoint": trans.endpoint,
            "method": trans.method,
        }
    for act in manifest.actions:
        nt = f"action_elder_{act.id}"
        lookup[nt] = {
            "definition": act,
            "endpoint": act.endpoint,
            "method": act.method,
        }
    return lookup


def get_node_catalog(
    app: Any,
    db: Any,
    tenant_id: int | None,
) -> dict[str, Any]:
    """Return the unified Generic + Elder node catalog evaluated live for a tenant."""
    discover_nodes()
    elder_lookup = _build_elder_definition_lookup()
    extensions = getattr(app, "extensions", None) or {}
    installed_modules: dict[str, Any] | None = extensions.get("elder_modules")

    nodes: list[dict[str, Any]] = []

    for node_type in sorted(NodeRegistry.list_all()):
        node_cls: Any = NodeRegistry.get(node_type)
        if node_cls is None:
            continue
        connector_id = getattr(node_cls, "connector_id_attr", None)

        if connector_id is None:
            if node_type not in BUILTIN_UI_META:
                continue
            ui_meta = BUILTIN_UI_META[node_type]
            enabled, disabled_code, disabled_reason = _evaluate_node_availability(
                app,
                db,
                tenant_id,
                required_module=getattr(node_cls, "required_module", None),
                required_license_feature=getattr(
                    node_cls, "required_license_feature", None
                ),
                required_feature_flag=getattr(node_cls, "required_feature_flag", None),
                endpoint=None,
                method=None,
            )
            nodes.append(
                {
                    "node_type": node_type,
                    "name": node_cls.name,
                    "description": node_cls.description,
                    "category": node_cls.category,
                    "stage": ui_meta["stage"],
                    "source": "builtin",
                    "connector_id": None,
                    "module": "core",
                    "required_module": None,
                    "module_label": "Core & Generic",
                    "subcategory": ui_meta["subcategory"],
                    "icon": ui_meta["icon"],
                    "color": ui_meta["color"],
                    "inputs": node_cls.inputs(),
                    "outputs": node_cls.outputs(),
                    "config_schema": ui_meta["config_schema"],
                    "output_schema": ui_meta.get("output_schema", []),
                    "endpoint": None,
                    "method": None,
                    "enabled": enabled,
                    "disabled_code": disabled_code,
                    "disabled_reason": disabled_reason,
                }
            )
            continue

        if connector_id != "elder":
            continue

        elder_entry = elder_lookup.get(node_type)
        definition = elder_entry["definition"] if elder_entry else None
        req_module = getattr(node_cls, "required_module", None) or "core"
        req_lic = getattr(node_cls, "required_license_feature", None)
        req_flag = getattr(node_cls, "required_feature_flag", None)
        endpoint = elder_entry["endpoint"] if elder_entry else None
        method = elder_entry["method"] if elder_entry else None
        config_schema = _serialize_config_schema(
            getattr(definition, "config_schema", None)
            or getattr(node_cls, "config_schema_attr", None)
        )
        output_schema = list(
            getattr(definition, "output_schema", None)
            or getattr(node_cls, "output_schema_attr", None)
            or []
        )
        subcategory = (
            getattr(definition, "subcategory", None)
            or getattr(node_cls, "subcategory", None)
            or _module_display_name(req_module, installed_modules)
        )
        icon = (
            getattr(definition, "icon", None)
            or getattr(node_cls, "icon", None)
            or "Box"
        )

        enabled, disabled_code, disabled_reason = _evaluate_node_availability(
            app,
            db,
            tenant_id,
            required_module=req_module,
            required_license_feature=req_lic,
            required_feature_flag=req_flag,
            endpoint=endpoint,
            method=method,
        )

        nodes.append(
            {
                "node_type": node_type,
                "name": node_cls.name.removeprefix("Elder Platform: "),
                "description": node_cls.description,
                "category": node_cls.category,
                "stage": _category_to_stage(node_cls.category),
                "source": "elder",
                "connector_id": "elder",
                "module": req_module,
                "required_module": req_module,
                "module_label": _module_display_name(req_module, installed_modules),
                "subcategory": subcategory,
                "icon": icon,
                "color": _ELDER_MODULE_COLORS.get(req_module, "#6366f1"),
                "inputs": node_cls.inputs(),
                "outputs": node_cls.outputs(),
                "config_schema": config_schema,
                "output_schema": output_schema,
                "endpoint": endpoint,
                "method": method,
                "enabled": enabled,
                "disabled_code": disabled_code,
                "disabled_reason": disabled_reason,
            }
        )

    # Summary module map for palette headers / disabled modules disclosure
    modules_summary: list[dict[str, Any]] = [
        {
            "name": "core",
            "display_name": "Core & Generic",
            "enabled": True,
            "node_count": sum(1 for n in nodes if n["module"] == "core"),
            "disabled_reason": None,
        }
    ]
    for m in MODULES:
        mod_nodes = [n for n in nodes if n["module"] == m.name]
        enabled_flag, _, reason = _evaluate_node_availability(
            app,
            db,
            tenant_id,
            required_module=m.name,
            required_license_feature=None,
            required_feature_flag=None,
            endpoint=None,
            method=None,
        )
        modules_summary.append(
            {
                "name": m.name,
                "display_name": _module_display_name(m.name, installed_modules),
                "enabled": enabled_flag,
                "node_count": len(mod_nodes),
                "disabled_reason": reason,
            }
        )

    return {
        "nodes": nodes,
        "modules": modules_summary,
        "operators": BUILTIN_OPERATOR_OPTIONS,
        "total": len(nodes),
        "enabled_count": sum(1 for n in nodes if n["enabled"]),
    }


def _should_show_field(field_schema: dict[str, Any], config: dict[str, Any]) -> bool:
    show_when = field_schema.get("show_when")
    if not isinstance(show_when, dict) or not show_when.get("field"):
        return True
    target_val = config.get(show_when["field"])
    if "equals" in show_when:
        return target_val == show_when["equals"]
    if "not_equals" in show_when:
        return target_val != show_when["not_equals"]
    if "in" in show_when and isinstance(show_when["in"], list):
        return target_val in show_when["in"]
    return True


def _is_empty_required(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, dict)) and len(value) == 0:
        return True
    return False


def _extract_node_parts(raw: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    raw_data = raw.get("data")
    data_obj: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
    node_id = str(raw.get("id") or raw.get("node_id") or "")
    node_type = str(
        raw.get("nodeType")
        or raw.get("node_type")
        or data_obj.get("nodeType")
        or data_obj.get("node_type")
        or raw.get("type")
        or ""
    )
    label = str(raw.get("label") or data_obj.get("label") or node_type or node_id)
    config = raw.get("config")
    if config is None:
        config = data_obj.get("config")
    return node_id, node_type, label, dict(config) if isinstance(config, dict) else {}


def validate_playbook_nodes(
    app: Any,
    db: Any,
    tenant_id: int | None,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate playbook graph, node types, live module availability, and required config."""
    if not nodes:
        return {
            "valid": True,
            "errors": [],
            "warnings": [
                {
                    "code": "EMPTY_PLAYBOOK",
                    "message": "Playbook currently has no nodes.",
                }
            ],
            "disabled_modules": [],
            "has_disabled_modules": False,
        }

    discover_nodes()
    catalog = get_node_catalog(app, db, tenant_id)
    catalog_by_type = {n["node_type"]: n for n in catalog["nodes"]}
    elder_lookup = _build_elder_definition_lookup()

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    disabled_modules: list[str] = []
    seen_ids: set[str] = set()
    normalized_nodes: list[dict[str, Any]] = []

    # Pre-index incoming target handles per node so wired input ports satisfy inputs
    incoming_handles: dict[str, set[str]] = {}
    for edge in edges or []:
        if not isinstance(edge, dict):
            continue
        tgt = str(edge.get("target") or edge.get("target_node_id") or "")
        th = str(edge.get("targetHandle") or edge.get("target_handle") or "")
        if tgt and th:
            incoming_handles.setdefault(tgt, set()).add(th)

    for raw in nodes:
        if not isinstance(raw, dict):
            continue
        node_id, node_type, label, config = _extract_node_parts(raw)
        if not node_id:
            errors.append(
                {
                    "code": "INVALID_NODE_ID",
                    "node_id": "",
                    "node_type": node_type,
                    "message": "Every node must have a non-empty id.",
                }
            )
            continue
        if node_id in seen_ids:
            errors.append(
                {
                    "code": "DUPLICATE_NODE_ID",
                    "node_id": node_id,
                    "node_type": node_type,
                    "message": f"Duplicate node id '{node_id}'.",
                }
            )
            continue
        seen_ids.add(node_id)
        normalized_nodes.append({"id": node_id, "type": node_type, "label": label})

        node_cls = NodeRegistry.get(node_type, raise_on_missing=False)
        cat_item = catalog_by_type.get(node_type)
        if node_cls is None and cat_item is None:
            errors.append(
                {
                    "code": "UNKNOWN_NODE_TYPE",
                    "node_id": node_id,
                    "node_type": node_type,
                    "message": f"Unknown node type '{node_type}' on node '{label}'.",
                }
            )
            continue

        # Live availability validation
        if cat_item is not None:
            if not cat_item["enabled"]:
                mod_name = cat_item.get("required_module") or cat_item.get("module")
                if mod_name and mod_name not in disabled_modules:
                    disabled_modules.append(mod_name)
                errors.append(
                    {
                        "code": cat_item.get("disabled_code") or "MODULE_DISABLED",
                        "node_id": node_id,
                        "node_type": node_type,
                        "module": mod_name,
                        "message": f"{label}: {cat_item.get('disabled_reason')}",
                    }
                )
        elif node_cls is not None:
            elder_entry = elder_lookup.get(node_type)
            enabled, disabled_code, disabled_reason = _evaluate_node_availability(
                app,
                db,
                tenant_id,
                required_module=getattr(node_cls, "required_module", None),
                required_license_feature=getattr(
                    node_cls, "required_license_feature", None
                ),
                required_feature_flag=getattr(node_cls, "required_feature_flag", None),
                endpoint=elder_entry["endpoint"] if elder_entry else None,
                method=elder_entry["method"] if elder_entry else None,
            )
            if not enabled:
                mod_name = getattr(node_cls, "required_module", None) or "core"
                if mod_name not in disabled_modules:
                    disabled_modules.append(mod_name)
                errors.append(
                    {
                        "code": disabled_code or "MODULE_DISABLED",
                        "node_id": node_id,
                        "node_type": node_type,
                        "module": mod_name,
                        "message": f"{label}: {disabled_reason}",
                    }
                )

        # Required config schema validation (respecting show_when & wired ports)
        schema_fields: list[dict[str, Any]] = (
            cat_item["config_schema"] if cat_item else []
        )
        hidden_required: set[str] = set()
        node_wired_handles = incoming_handles.get(node_id, set())

        for fs in schema_fields:
            fname = fs.get("field", "")
            if not _should_show_field(fs, config):
                hidden_required.add(fname)
                continue
            if fs.get("required"):
                val = config.get(fname)
                if val is None and fs.get("default") is not None:
                    val = fs.get("default")
                if _is_empty_required(val) and fname not in node_wired_handles:
                    errors.append(
                        {
                            "code": "MISSING_REQUIRED_CONFIG",
                            "node_id": node_id,
                            "node_type": node_type,
                            "field": fname,
                            "message": (
                                f"{label}: required field '{fs.get('label') or fname}' "
                                "is missing"
                            ),
                        }
                    )

        # Run node_cls.validate_config for structural checks not already masked
        if node_cls is not None:
            try:
                node_inst = node_cls({"config": config, "node_id": node_id})
                cfg_errors = node_inst.validate_config(config)
                for err_msg in cfg_errors:
                    # Skip duplicate missing-field errors for hidden conditional fields
                    if any(
                        re.search(rf"\b{re.escape(hf)}\b", err_msg)
                        for hf in hidden_required
                    ):
                        continue
                    if any(
                        e.get("node_id") == node_id
                        and e.get("code") == "MISSING_REQUIRED_CONFIG"
                        and str(e.get("field", "")) in err_msg
                        for e in errors
                    ):
                        continue
                    errors.append(
                        {
                            "code": "INVALID_CONFIG",
                            "node_id": node_id,
                            "node_type": node_type,
                            "message": f"{label}: {err_msg}",
                        }
                    )
            except Exception as exc:
                errors.append(
                    {
                        "code": "CONFIG_VALIDATION_ERROR",
                        "node_id": node_id,
                        "node_type": node_type,
                        "message": f"{label}: {exc}",
                    }
                )

    # Edge integrity & cycle check
    normalized_edges: list[dict[str, Any]] = []
    for edge in edges or []:
        if not isinstance(edge, dict):
            continue
        src = str(edge.get("source") or edge.get("source_node_id") or "")
        tgt = str(edge.get("target") or edge.get("target_node_id") or "")
        if src not in seen_ids or tgt not in seen_ids:
            errors.append(
                {
                    "code": "DANGLING_EDGE",
                    "message": f"Edge '{src}' -> '{tgt}' references an unknown node.",
                }
            )
            continue
        normalized_edges.append({"source": src, "target": tgt})

    if normalized_nodes and not any(
        e.get("code") in ("DUPLICATE_NODE_ID", "INVALID_NODE_ID") for e in errors
    ):
        try:
            TopologicalSorter(normalized_nodes, normalized_edges).sort()
        except CycleDetectedError as exc:
            errors.append(
                {
                    "code": "CYCLE_DETECTED",
                    "message": str(exc),
                }
            )

    has_disabled = bool(disabled_modules) or any(
        e.get("code")
        in (
            "MODULE_DISABLED",
            "MODULE_UNLICENSED",
            "MODULE_NOT_INSTALLED",
            "FLAG_DISABLED",
            "ROUTE_NOT_MOUNTED",
        )
        for e in errors
    )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "disabled_modules": disabled_modules,
        "has_disabled_modules": has_disabled,
    }
