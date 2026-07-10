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

    data: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)
    source_node_id: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


def _serialize_output(value: Any) -> Dict[str, Any]:
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
        return {
            "data": value.get("data"),
            "metadata": value.get("metadata", {}),
            "source_node_id": value.get("source_node_id", ""),
            "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else ts,
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
    outputs: Dict[str, NodeData] = field(default_factory=dict)
    error: Optional[str] = None
    execution_time_ms: float = 0.0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert NodeResult to dictionary for serialization."""
        return {
            "node_id": self.node_id,
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
    node_results: Dict[str, NodeResult] = field(default_factory=dict)
    error: Optional[str] = None
    execution_time_ms: float = 0.0
    completed_nodes: List[str] = field(default_factory=list)
    failed_nodes: List[str] = field(default_factory=list)
    skipped_nodes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
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

    def __init__(self, context: Dict[str, Any]):
        """Initialize the node with execution context."""
        self.context = context

    async def execute(self, inputs: Dict[str, NodeData]) -> Dict[str, NodeData]:
        """Execute the node with given inputs."""
        raise NotImplementedError("Node must implement execute method")

    def validate_config(self) -> None:
        """Validate node configuration."""
        pass


class PassThroughNode(BaseNode):
    """Default node for unregistered types."""

    async def execute(self, inputs: Dict[str, NodeData]) -> Dict[str, NodeData]:
        """Pass through inputs to outputs."""
        if "default" in inputs:
            return {"default": inputs["default"]}
        return inputs


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
        self._node_outputs: Dict[str, Dict[str, NodeData]] = {}
        self._execution_order: List[str] = []

        logger.info(
            f"PlaybookExecutor initialized: execution_id={execution_id}, "
            f"playbook_id={playbook_id}, timeout={node_timeout_seconds}s"
        )

    async def execute(self, playbook_data: Dict[str, Any]) -> ExecutionResult:
        """Execute the playbook with given data."""
        start_time = time.time()
        logger.info(
            f"Starting playbook execution: execution_id={self.execution_id}, "
            f"playbook_id={self.playbook_id}"
        )

        try:
            nodes = playbook_data.get("nodes", [])
            edges = playbook_data.get("edges", [])
            global_config = playbook_data.get("config", {})

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

            node_results: Dict[str, NodeResult] = {}
            completed_nodes: List[str] = []
            failed_nodes: List[str] = []
            skipped_nodes: List[str] = []

            nodes_by_id = {node["id"]: node for node in nodes}

            for node_id in execution_order:
                node_data = nodes_by_id.get(node_id)
                if not node_data:
                    logger.error(f"Node {node_id} not found in nodes list")
                    continue

                inputs = self._gather_inputs(node_id, edges, self._node_outputs)

                result = await self._execute_node(
                    node_id, node_data, inputs, global_config
                )
                node_results[node_id] = result

                if result.status == NodeStatus.SUCCESS:
                    completed_nodes.append(node_id)
                    self._route_outputs(node_id, result, edges)
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

            result = ExecutionResult(
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

            return result

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

    def _get_execution_order(
        self, nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]
    ) -> List[str]:
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
        node_data: Dict[str, Any],
        inputs: Dict[str, NodeData],
        global_config: Dict[str, Any],
    ) -> NodeResult:
        """Execute a single node with timeout and error handling."""
        started_at = datetime.now(UTC)
        start_time = time.time()

        logger.info(f"Executing node: {node_id} ({node_data.get('type', 'unknown')})")

        try:
            node_type = node_data.get("type") or node_data.get("data", {}).get(
                "nodeType", "unknown"
            )
            node_config = node_data.get("config", {})

            context = {
                "execution_id": self.execution_id,
                "playbook_id": self.playbook_id,
                "node_id": node_id,
                "config": node_config,
                "global_config": global_config,
                "metadata": {
                    "label": node_data.get("data", {}).get("label", ""),
                    "category": node_data.get("data", {}).get("category", ""),
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
            except asyncio.TimeoutError:
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
                status=NodeStatus.FAILED,
                error=error_msg,
                execution_time_ms=execution_time_ms,
                started_at=started_at,
                completed_at=completed_at,
            )

    def _gather_inputs(
        self,
        node_id: str,
        edges: List[Dict[str, Any]],
        node_outputs: Dict[str, Dict[str, NodeData]],
    ) -> Dict[str, NodeData]:
        """Gather input data for a node from upstream outputs."""
        inputs: Dict[str, NodeData] = {}

        incoming_edges = [edge for edge in edges if edge.get("target") == node_id]

        for edge in incoming_edges:
            source_node_id = edge.get("source")
            source_handle = edge.get("sourceHandle", "default")
            target_handle = edge.get("targetHandle", "default")

            if source_node_id in node_outputs:
                source_outputs = node_outputs[source_node_id]
                if source_handle in source_outputs:
                    inputs[target_handle] = _unwrap_output(
                        source_outputs[source_handle]
                    )
                    logger.debug(
                        f"Gathered input for {node_id}[{target_handle}] "
                        f"from {source_node_id}[{source_handle}]"
                    )

        return inputs

    def _route_outputs(
        self,
        node_id: str,
        result: NodeResult,
        edges: List[Dict[str, Any]],
    ) -> None:
        """Store node outputs for downstream consumption."""
        self._node_outputs[node_id] = result.outputs
        logger.debug(
            f"Routed {len(result.outputs)} output(s) from node {node_id}: "
            f"{list(result.outputs.keys())}"
        )
