"""Run Function Action Node for Streams Workflow.

Invokes an Elder Runs serverless function via the Elder API and optionally
polls until the execution reaches a terminal status (completed, failed,
or timed_out).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import httpx

from ...executor.node_registry import register_node
from ..base import BaseNode
from ..template import interpolate_any, interpolate_value

TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "timed_out"})
DEFAULT_POLL_INTERVAL_SECONDS = 0.5


def _unwrap_input(value: Any) -> Any:
    """Unwrap a standard Streams port envelope ``{"data": ...}`` when present."""
    if (
        isinstance(value, dict)
        and "data" in value
        and set(value.keys())
        <= {
            "data",
            "metadata",
            "source_node_id",
        }
    ):
        return value["data"]
    return value


@register_node("action_run", "actions", "Run Function")
class RunFunctionAction(BaseNode):
    """Invoke an Elder Runs serverless function from a Streams playbook."""

    node_type = "action_run"
    name = "Run Function"
    description = (
        "Invoke an Elder Runs serverless function and optionally wait for its result"
    )
    category = "actions"
    required_module = "runs"

    @classmethod
    def inputs(cls) -> list[dict[str, Any]]:
        """Define input ports for the Run Function node."""
        return [
            {
                "name": "in",
                "description": "Default input payload forwarded to the serverless function",
                "required": False,
                "data_type": "any",
            },
            {
                "name": "function_id",
                "description": "Optional override for the target Run Function ID",
                "required": False,
                "data_type": "number",
            },
        ]

    @classmethod
    def outputs(cls) -> list[dict[str, Any]]:
        """Define output ports for the Run Function node."""
        return [
            {
                "name": "out",
                "description": "Execution result summary object",
                "data_type": "object",
            },
            {
                "name": "output",
                "description": "Structured JSON output returned by the function",
                "data_type": "any",
            },
            {
                "name": "stdout",
                "description": "Captured standard output log",
                "data_type": "string",
            },
            {
                "name": "stderr",
                "description": "Captured standard error log",
                "data_type": "string",
            },
            {
                "name": "status",
                "description": "Execution status (pending, completed, failed, timed_out)",
                "data_type": "string",
            },
            {
                "name": "exit_code",
                "description": "Process exit code",
                "data_type": "number",
            },
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """Validate required Run Function configuration fields."""
        errors: list[str] = []
        if not config.get("function_id"):
            errors.append("function_id is required")
        return errors

    def _resolve_api_url(self) -> str:
        """Resolve Elder API base URL from context or ELDER_API_URL env var."""
        global_cfg = self.context.get("global_config") or {}
        base = (
            global_cfg.get("elder_api_url")
            or os.environ.get("ELDER_API_URL")
            or "http://localhost:8000"
        )
        return str(base).rstrip("/")

    def _build_auth_headers(self) -> dict[str, str]:
        """Build Authorization / API key headers for internal Elder API calls."""
        headers: dict[str, str] = {}
        variables = self.context.get("variables") or {}
        bearer = (
            variables.get("_elder_auth_token")
            or self.context.get("_elder_auth_token")
            or os.environ.get("ELDER_TOKEN")
        )
        api_key = os.environ.get("ELDER_API_KEY")
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        elif api_key:
            headers["X-API-Key"] = api_key
        return headers

    def _resolve_payload(self, inputs: dict[str, Any]) -> Any:
        """Resolve and interpolate the function invocation payload."""
        cfg_payload = self.get_config_value("payload")
        if cfg_payload not in (None, ""):
            if isinstance(cfg_payload, str):
                evaluated = interpolate_value(
                    cfg_payload, self.context, inputs, preserve_type=True
                )
                if isinstance(evaluated, str):
                    try:
                        return json.loads(evaluated)
                    except json.JSONDecodeError:
                        return evaluated
                return evaluated
            return interpolate_any(
                cfg_payload, self.context, inputs, preserve_type=True
            )
        return _unwrap_input(inputs.get("in"))

    def _pack_outputs(self, result: dict[str, Any]) -> dict[str, Any]:
        """Wrap result fields into Streams port envelopes."""
        node_id = self.context.get("node_id", "")
        return {
            "out": {"data": result, "metadata": {}, "source_node_id": node_id},
            "output": {
                "data": result.get("output"),
                "metadata": {},
                "source_node_id": node_id,
            },
            "stdout": {
                "data": result.get("stdout"),
                "metadata": {},
                "source_node_id": node_id,
            },
            "stderr": {
                "data": result.get("stderr"),
                "metadata": {},
                "source_node_id": node_id,
            },
            "status": {
                "data": result.get("status"),
                "metadata": {},
                "source_node_id": node_id,
            },
            "exit_code": {
                "data": result.get("exit_code"),
                "metadata": {},
                "source_node_id": node_id,
            },
        }

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Invoke the configured Run Function and optionally poll until completion."""
        raw_func_id = _unwrap_input(inputs.get("function_id")) or self.get_config_value(
            "function_id"
        )
        if isinstance(raw_func_id, str):
            raw_func_id = interpolate_value(
                raw_func_id, self.context, inputs, preserve_type=True
            )
        if raw_func_id in (None, ""):
            raise ValueError("function_id is required")

        try:
            function_id = int(raw_func_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid function_id: {raw_func_id!r}") from exc

        payload = self._resolve_payload(inputs)
        wait_for_result = bool(self.get_config_value("wait_for_result", True))
        timeout_seconds = float(self.get_config_value("timeout_seconds", 60))
        poll_interval = float(
            self.get_config_value("poll_interval", DEFAULT_POLL_INTERVAL_SECONDS)
        )

        api_url = self._resolve_api_url()
        headers = self._build_auth_headers()
        invoke_url = f"{api_url}/api/v1/runs/{function_id}/invoke"

        self.log_info(
            f"Invoking Run Function {function_id} (wait_for_result={wait_for_result})"
        )

        async with httpx.AsyncClient(
            timeout=max(timeout_seconds, 10.0), follow_redirects=False
        ) as client:
            invoke_resp = await client.post(
                invoke_url,
                headers=headers,
                json={"trigger_type": "stream", "payload": payload},
            )
            if invoke_resp.status_code >= 400:
                raise RuntimeError(
                    f"Failed to invoke Run Function {function_id}: "
                    f"HTTP {invoke_resp.status_code} {invoke_resp.text}"
                )

            invoke_body = invoke_resp.json()
            execution_id = invoke_body.get("execution_id") or (
                invoke_body.get("execution") or {}
            ).get("execution_id")
            if not execution_id:
                raise RuntimeError("Invoke response missing execution_id")

            if not wait_for_result:
                result = {
                    "execution_id": execution_id,
                    "status": "pending",
                    "exit_code": None,
                    "output": None,
                    "stdout": None,
                    "stderr": None,
                    "error_message": None,
                }
                return self._pack_outputs(result)

            poll_url = f"{api_url}/api/v1/runs/executions/{execution_id}"
            deadline = time.monotonic() + timeout_seconds

            while True:
                poll_resp = await client.get(poll_url, headers=headers)
                if poll_resp.status_code >= 400:
                    raise RuntimeError(
                        f"Failed to poll execution {execution_id}: "
                        f"HTTP {poll_resp.status_code} {poll_resp.text}"
                    )

                exec_data = poll_resp.json()
                status = str(exec_data.get("status") or "").lower()
                if status in TERMINAL_STATUSES:
                    result = {
                        "execution_id": execution_id,
                        "status": status,
                        "exit_code": exec_data.get("exit_code"),
                        "output": exec_data.get(
                            "output_data", exec_data.get("output_json")
                        ),
                        "stdout": exec_data.get("stdout_log"),
                        "stderr": exec_data.get("stderr_log"),
                        "error_message": exec_data.get("error_message"),
                    }
                    self.log_info(
                        f"Run Function execution {execution_id} finished with status={status}"
                    )
                    return self._pack_outputs(result)

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Run Function execution {execution_id} did not complete "
                        f"within {timeout_seconds}s (last status={status!r})"
                    )
                await asyncio.sleep(min(poll_interval, remaining))
