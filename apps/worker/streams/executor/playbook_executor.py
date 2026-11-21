"""Playbook Executor - Executes Streams workflow playbooks.

Core execution engine for running playbook node graphs, handling topological
sorting, data flow between nodes, conditional branching, and error handling.

Ported from icestreams-worker; de-Flask'd for use in Elder worker.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from .graph_utils import TopologicalSorter
from .node_registry import NodeRegistry

logger = logging.getLogger(__name__)


class NodeStatus(str, Enum):
    """Execution status for individual nodes."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(slots=True)
class NodeData:
    """Standard data transfer object for workflow nodes."""

    data: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    source_node_id: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


def _serialize_output(value: Any) -> dict[str, Any]:
    """Serialize a single node output port to a JSON-safe dict.

    Nodes emit plain dicts ({"data", "metadata", "source_node_id"}); the
    executor may also hold NodeData objects. Accept either, plus a bare scalar
    as a last-resort fallback, so result serialization never crashes.
    """
    if isinstance(value, NodeData):
        return {
            "data": value.data,
            "metadata": value.metadata,
            "source_node_id": value.source_node_id,
            "timestamp": value.timestamp.isoformat(),
        }
    if isinstance(value, dict):
        ts = value.get("timestamp")
        ts_val = ts.isoformat() if isinstance(ts, datetime) else ts
        return {
            "data": value.get("data"),
            "metadata": value.get("metadata", {}),
            "source_node_id": value.get("source_node_id", ""),
            "timestamp": ts_val,
        }
    return {"data": value, "metadata": {}, "source_node_id": "", "timestamp": None}


def _unwrap_output(value: Any) -> Any:
    """Extract the raw payload from a node output port for downstream input.

    Nodes emit dict-wrapped ports ({"data", "metadata", "source_node_id"}) but
    consume RAW input values (inputs.get("field") -> the value itself). Unwrap
    the "data" field so a chained node receives the raw payload, not the
    envelope. NodeData objects and bare values pass through unchanged.
    """
    if isinstance(value, NodeData):
        return value.data
    if isinstance(value, dict) and "data" in value:
        return value["data"]
    return value


@dataclass(slots=True)
class NodeResult:
    """Result of a single node execution."""

    node_id: str
    status: NodeStatus
    outputs: dict[str, NodeData] = field(default_factory=dict)
    error: str | None = None
    execution_time_ms: float = 0.0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    node_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert NodeResult to dictionary for serialization."""
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "status": self.status.value,
            "outputs": {k: _serialize_output(v) for k, v in self.outputs.items()},
            "error": self.error,
            "execution_time_ms": self.execution_time_ms,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
        }


@dataclass(slots=True)
class ExecutionResult:
    """Overall result of playbook execution."""

    success: bool
    node_results: dict[str, NodeResult] = field(default_factory=dict)
    error: str | None = None
    execution_time_ms: float = 0.0
    completed_nodes: list[str] = field(default_factory=list)
    failed_nodes: list[str] = field(default_factory=list)
    skipped_nodes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert ExecutionResult to dictionary for serialization."""
        return {
            "success": self.success,
            "node_results": {k: v.to_dict() for k, v in self.node_results.items()},
            "error": self.error,
            "execution_time_ms": self.execution_time_ms,
            "completed_nodes": self.completed_nodes,
            "failed_nodes": self.failed_nodes,
            "skipped_nodes": self.skipped_nodes,
        }


class BaseNode:
    """Base class for all node implementations."""

    def __init__(self, context: dict[str, Any]):
        """Initialize the node with execution context."""
        self.context = context

    async def execute(self, inputs: dict[str, NodeData]) -> dict[str, NodeData]:
        """Execute the node with given inputs."""
        raise NotImplementedError("Node must implement execute method")

    def validate_config(self) -> None:
        """Validate node configuration."""
        pass


class PassThroughNode(BaseNode):
    """Default node for unregistered types."""

    async def execute(self, inputs: dict[str, NodeData]) -> dict[str, NodeData]:
        """Pass through inputs to outputs."""
        if "default" in inputs:
            return {"default": inputs["default"]}
        return inputs


def _resolve_source_handle(
    source_outputs: dict[str, Any], raw_source_handle: str | None
) -> str | None:
    """Resolve an edge's sourceHandle against a node's emitted output ports."""
    if raw_source_handle and raw_source_handle != "default":
        return raw_source_handle if raw_source_handle in source_outputs else None
    if "default" in source_outputs:
        return "default"
    if "out" in source_outputs:
        return "out"
    if source_outputs:
        return next(iter(source_outputs.keys()))
    return None


def _extract_primary_output(outputs: dict[str, Any]) -> Any:
    """Extract the primary payload from a node's output ports for $node[...] lookup."""
    unwrapped = {k: _unwrap_output(v) for k, v in outputs.items()}
    if "out" in unwrapped and unwrapped["out"] is not None:
        return unwrapped["out"]
    if "default" in unwrapped and unwrapped["default"] is not None:
        return unwrapped["default"]
    non_none = [v for v in unwrapped.values() if v is not None]
    if len(non_none) == 1 and isinstance(non_none[0], dict):
        return {**non_none[0], **unwrapped}
    if len(non_none) == 1:
        return non_none[0]
    return unwrapped


class PlaybookExecutor:
    """Main executor for running playbook node graphs."""

    DEFAULT_NODE_TIMEOUT_SECONDS = 30.0

    def __init__(
        self,
        execution_id: str,
        playbook_id: str,
        node_timeout_seconds: float = DEFAULT_NODE_TIMEOUT_SECONDS,
    ):
        """Initialize the playbook executor."""
        self.execution_id = execution_id
        self.playbook_id = playbook_id
        self.node_timeout_seconds = node_timeout_seconds
        self._node_outputs: dict[str, dict[str, NodeData]] = {}
        self._node_context_outputs: dict[str, Any] = {}
        self._execution_order: list[str] = []

        logger.info(
            f"PlaybookExecutor initialized: execution_id={execution_id}, "
            f"playbook_id={playbook_id}, timeout={node_timeout_seconds}s"
        )

    async def execute(self, playbook_data: dict[str, Any]) -> ExecutionResult:
        """Execute the playbook with given data."""
        start_time = time.time()
        logger.info(
            f"Starting playbook execution: execution_id={self.execution_id}, "
            f"playbook_id={self.playbook_id}"
        )

        try:
            nodes = playbook_data.get("nodes", [])
            edges = playbook_data.get("edges", [])
            raw_global_config = playbook_data.get("config", {})
            global_config = (
                dict(raw_global_config) if isinstance(raw_global_config, dict) else {}
            )

            if "trigger_data" not in global_config:
                trigger_payload = playbook_data.get("trigger_data")
                if trigger_payload is None:
                    trigger_payload = playbook_data.get("trigger_output")
                if trigger_payload is not None:
                    global_config["trigger_data"] = trigger_payload
            if (
                "tenant_id" not in global_config
                and playbook_data.get("tenant_id") is not None
            ):
                global_config["tenant_id"] = playbook_data.get("tenant_id")
            if (
                "enabled_modules" not in global_config
                and playbook_data.get("enabled_modules") is not None
            ):
                global_config["enabled_modules"] = playbook_data.get("enabled_modules")

            skip_inactive_branches = bool(
                global_config.get("skip_inactive_branches", False)
            )

            if not nodes:
                return ExecutionResult(
                    success=False,
                    error="No nodes in playbook",
                    execution_time_ms=0.0,
                )

            execution_order = self._get_execution_order(nodes, edges)
            self._execution_order = execution_order
            logger.info(f"Execution order: {execution_order}")

            self._node_outputs = {}
            self._node_context_outputs = {}

            node_results: dict[str, NodeResult] = {}
            completed_nodes: list[str] = []
            failed_nodes: list[str] = []
            skipped_nodes: list[str] = []

            nodes_by_id = {node["id"]: node for node in nodes}

            for node_id in execution_order:
                node_data = nodes_by_id.get(node_id)
                if not node_data:
                    logger.error(f"Node {node_id} not found in nodes list")
                    continue

                if skip_inactive_branches and not self._has_active_incoming_edge(
                    node_id, edges, node_results
                ):
                    now = datetime.now(UTC)
                    node_type = self._resolve_node_type(node_data)
                    result = NodeResult(
                        node_id=node_id,
                        node_type=node_type,
                        status=NodeStatus.SKIPPED,
                        outputs={},
                        execution_time_ms=0.0,
                        started_at=now,
                        completed_at=now,
                    )
                    node_results[node_id] = result
                    skipped_nodes.append(node_id)
                    logger.info(
                        f"Skipped node {node_id} ({node_type}): inactive branch"
                    )
                    continue

                inputs = self._gather_inputs(node_id, edges, self._node_outputs)

                result = await self._execute_node(
                    node_id, node_data, inputs, global_config
                )
                node_results[node_id] = result

                if result.status == NodeStatus.SUCCESS:
                    completed_nodes.append(node_id)
                    self._route_outputs(node_id, result, edges, node_data)
                elif result.status == NodeStatus.FAILED:
                    failed_nodes.append(node_id)
                    if not global_config.get("continue_on_error", False):
                        logger.error(
                            f"Node {node_id} failed, stopping execution: {result.error}"
                        )
                        break
                elif result.status == NodeStatus.SKIPPED:
                    skipped_nodes.append(node_id)

            execution_time_ms = (time.time() - start_time) * 1000

            success = len(failed_nodes) == 0 and len(completed_nodes) > 0

            exec_result = ExecutionResult(
                success=success,
                node_results=node_results,
                error=(f"{len(failed_nodes)} node(s) failed" if failed_nodes else None),
                execution_time_ms=execution_time_ms,
                completed_nodes=completed_nodes,
                failed_nodes=failed_nodes,
                skipped_nodes=skipped_nodes,
            )

            logger.info(
                f"Playbook execution completed: success={success}, "
                f"completed={len(completed_nodes)}, failed={len(failed_nodes)}, "
                f"skipped={len(skipped_nodes)}, time={execution_time_ms:.2f}ms"
            )

            return exec_result

        except Exception as e:
            execution_time_ms = (time.time() - start_time) * 1000
            logger.error(
                f"Playbook execution failed with exception: {e}", exc_info=True
            )
            return ExecutionResult(
                success=False,
                error=f"Execution failed: {str(e)}",
                execution_time_ms=execution_time_ms,
            )

    @staticmethod
    def _resolve_node_type(node_data: dict[str, Any]) -> str:
        """Resolve the Streams node type from backend or ReactFlow node dicts."""
        data_obj = node_data.get("data") or {}
        raw_type = node_data.get("type")
        data_node_type = data_obj.get("nodeType")
        if (
            raw_type in (None, "", "stream", "playbookNode", "default")
            and data_node_type
        ):
            return str(data_node_type)
        return str(raw_type or data_node_type or "unknown")

    def _has_active_incoming_edge(
        self,
        node_id: str,
        edges: list[dict[str, Any]],
        node_results: dict[str, NodeResult],
    ) -> bool:
        """Return True if node has no incoming edges or at least one active branch."""
        incoming_edges = [edge for edge in edges if edge.get("target") == node_id]
        if not incoming_edges:
            return True

        for edge in incoming_edges:
            source_node_id = edge.get("source")
            if not source_node_id:
                continue
            source_result = node_results.get(source_node_id)
            if not source_result or source_result.status != NodeStatus.SUCCESS:
                continue
            source_outputs = self._node_outputs.get(source_node_id, {})
            resolved_handle = _resolve_source_handle(
                source_outputs, edge.get("sourceHandle")
            )
            if resolved_handle is None:
                continue
            if _unwrap_output(source_outputs[resolved_handle]) is not None:
                return True

        return False

    def _get_execution_order(
        self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
    ) -> list[str]:
        """Compute topological execution order for nodes."""
        try:
            sorter = TopologicalSorter(nodes, edges)
            execution_order = sorter.sort()
            logger.debug(f"Computed execution order: {execution_order}")
            return execution_order
        except Exception as e:
            logger.error(f"Error computing execution order: {e}")
            raise ValueError(f"Failed to compute execution order: {str(e)}")

    async def _execute_node(
        self,
        node_id: str,
        node_data: dict[str, Any],
        inputs: dict[str, NodeData],
        global_config: dict[str, Any],
    ) -> NodeResult:
        """Execute a single node with timeout and error handling."""
        started_at = datetime.now(UTC)
        start_time = time.time()

        node_type = self._resolve_node_type(node_data)
        logger.info(f"Executing node: {node_id} ({node_type})")

        try:
            data_obj = node_data.get("data") or {}
            node_config = node_data.get("config") or data_obj.get("config") or {}
            node_label = node_data.get("label") or data_obj.get("label", "")

            context = {
                "execution_id": self.execution_id,
                "playbook_id": self.playbook_id,
                "node_id": node_id,
                "config": node_config,
                "global_config": global_config,
                "node_outputs": dict(self._node_context_outputs),
                "trigger_data": global_config.get("trigger_data", {}),
                "tenant_id": global_config.get("tenant_id"),
                "metadata": {
                    "label": node_label,
                    "category": node_data.get("category")
                    or data_obj.get("category", ""),
                },
            }

            node_class = NodeRegistry.get(node_type, raise_on_missing=False)
            if not node_class:
                # Unknown node type - fail gracefully
                error_msg = f"Node type '{node_type}' not yet implemented"
                logger.warning(error_msg)
                execution_time_ms = (time.time() - start_time) * 1000
                completed_at = datetime.now(UTC)
                return NodeResult(
                    node_id=node_id,
                    node_type=node_type,
                    status=NodeStatus.FAILED,
                    error=error_msg,
                    execution_time_ms=execution_time_ms,
                    started_at=started_at,
                    completed_at=completed_at,
                )

            required_module = getattr(node_class, "required_module", None)
            enabled_modules = global_config.get("enabled_modules")
            if (
                required_module
                and required_module != "core"
                and enabled_modules is not None
            ):
                if isinstance(enabled_modules, dict):
                    is_module_on = bool(enabled_modules.get(required_module, False))
                else:
                    is_module_on = required_module in enabled_modules
                if not is_module_on:
                    error_msg = f"Module '{required_module}' is disabled for this tenant/environment"
                    logger.warning(f"Node {node_id} blocked: {error_msg}")
                    execution_time_ms = (time.time() - start_time) * 1000
                    completed_at = datetime.now(UTC)
                    return NodeResult(
                        node_id=node_id,
                        node_type=node_type,
                        status=NodeStatus.FAILED,
                        error=error_msg,
                        execution_time_ms=execution_time_ms,
                        started_at=started_at,
                        completed_at=completed_at,
                    )

            node_instance = node_class(context)

            try:
                outputs = await asyncio.wait_for(
                    node_instance.execute(inputs), timeout=self.node_timeout_seconds
                )
            except TimeoutError:
                raise TimeoutError(
                    f"Node execution exceeded timeout of {self.node_timeout_seconds}s"
                )

            execution_time_ms = (time.time() - start_time) * 1000
            completed_at = datetime.now(UTC)

            logger.info(
                f"Node {node_id} completed successfully in {execution_time_ms:.2f}ms"
            )

            return NodeResult(
                node_id=node_id,
                node_type=node_type,
                status=NodeStatus.SUCCESS,
                outputs=outputs,
                execution_time_ms=execution_time_ms,
                started_at=started_at,
                completed_at=completed_at,
            )

        except Exception as e:
            execution_time_ms = (time.time() - start_time) * 1000
            completed_at = datetime.now(UTC)

            error_msg = f"Node execution failed: {str(e)}"
            logger.error(f"Node {node_id} failed: {error_msg}", exc_info=True)

            return NodeResult(
                node_id=node_id,
                node_type=node_type,
                status=NodeStatus.FAILED,
                error=error_msg,
                execution_time_ms=execution_time_ms,
                started_at=started_at,
                completed_at=completed_at,
            )

    def _gather_inputs(
        self,
        node_id: str,
        edges: list[dict[str, Any]],
        node_outputs: dict[str, dict[str, NodeData]],
    ) -> dict[str, NodeData]:
        """Gather input data for a node from upstream outputs."""
        inputs: dict[str, Any] = {}

        incoming_edges = [edge for edge in edges if edge.get("target") == node_id]

        for edge in incoming_edges:
            source_node_id = edge.get("source")
            raw_source_handle = edge.get("sourceHandle")
            raw_target_handle = edge.get("targetHandle")
            target_handle = raw_target_handle or "default"

            if source_node_id in node_outputs:
                source_outputs = node_outputs[source_node_id]
                resolved_source = _resolve_source_handle(
                    source_outputs, raw_source_handle
                )
                if resolved_source is not None and resolved_source in source_outputs:
                    unwrapped_val = _unwrap_output(source_outputs[resolved_source])
                    if target_handle not in inputs or (
                        inputs[target_handle] is None and unwrapped_val is not None
                    ):
                        inputs[target_handle] = unwrapped_val
                    if not raw_target_handle or raw_target_handle == "default":
                        if "in" not in inputs or (
                            inputs["in"] is None and unwrapped_val is not None
                        ):
                            inputs["in"] = unwrapped_val
                    logger.debug(
                        f"Gathered input for {node_id}[{target_handle}] "
                        f"from {source_node_id}[{resolved_source}]"
                    )

        return inputs

    def _route_outputs(
        self,
        node_id: str,
        result: NodeResult,
        edges: list[dict[str, Any]],
        node_data: dict[str, Any] | None = None,
    ) -> None:
        """Store node outputs for downstream consumption."""
        self._node_outputs[node_id] = result.outputs
        primary = _extract_primary_output(result.outputs)
        node_label = ""
        if node_data:
            node_label = node_data.get("label") or (node_data.get("data") or {}).get(
                "label", ""
            )
        if node_label:
            self._node_context_outputs[node_label] = primary
        self._node_context_outputs[node_id] = primary
        logger.debug(
            f"Routed {len(result.outputs)} output(s) from node {node_id}: "
            f"{list(result.outputs.keys())}"
        )
