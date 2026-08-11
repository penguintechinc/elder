"""Graph utilities for playbook execution ordering and analysis.

Ported from icestreams-worker; async-agnostic.
Provides topological sorting, node relationships, and graph validation.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple


@dataclass(slots=True, frozen=True)
class GraphError(Exception):
    """Base exception for graph-related errors."""

    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(slots=True, frozen=True)
class CycleDetectedError(GraphError):
    """Raised when a cycle is detected in the graph."""

    cycle_nodes: tuple[str, ...]

    def __str__(self) -> str:
        cycle_str = " -> ".join(self.cycle_nodes)
        return f"{self.message}: {cycle_str}"


class TopologicalSorter:
    """Topological sorting using Kahn's algorithm with cycle detection."""

    def __init__(self, nodes: list[dict], edges: list[dict]) -> None:
        self.nodes = nodes
        self.edges = edges
        self._node_ids = {node["id"] for node in nodes}
        self._adjacency_list = self._build_adjacency_list()
        self._in_degree = self._calculate_in_degree()

    def _build_adjacency_list(self) -> dict[str, set[str]]:
        """Build adjacency list representation."""
        adj_list = defaultdict(set)
        for node_id in self._node_ids:
            adj_list[node_id] = set()
        for edge in self.edges:
            source = edge.get("source")
            target = edge.get("target")
            if (
                source
                and target
                and source in self._node_ids
                and target in self._node_ids
            ):
                adj_list[source].add(target)
        return dict(adj_list)

    def _calculate_in_degree(self) -> dict[str, int]:
        """Calculate in-degree for each node."""
        in_degree = {node_id: 0 for node_id in self._node_ids}
        for edge in self.edges:
            target = edge.get("target")
            if target and target in self._node_ids:
                in_degree[target] += 1
        return in_degree

    def sort(self) -> list[str]:
        """Perform topological sort. Returns node IDs in execution order."""
        in_degree = self._in_degree.copy()
        result = []
        queue = deque([node_id for node_id, degree in in_degree.items() if degree == 0])

        while queue:
            current = queue.popleft()
            result.append(current)
            for neighbor in self._adjacency_list.get(current, set()):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(result) != len(self._node_ids):
            remaining = [node_id for node_id in self._node_ids if node_id not in result]
            cycle = self._find_cycle(remaining)
            raise CycleDetectedError(
                message="Cycle detected in playbook graph", cycle_nodes=tuple(cycle)
            )

        return result

    def _find_cycle(self, remaining_nodes: list[str]) -> list[str]:
        """Find a cycle using DFS."""
        visited = set()
        rec_stack = set()
        parent = {}

        def dfs(node: str) -> str | None:
            visited.add(node)
            rec_stack.add(node)
            for neighbor in self._adjacency_list.get(node, set()):
                if neighbor not in remaining_nodes:
                    continue
                if neighbor not in visited:
                    parent[neighbor] = node
                    cycle_start = dfs(neighbor)
                    if cycle_start:
                        return cycle_start
                elif neighbor in rec_stack:
                    parent[neighbor] = node
                    return neighbor
            rec_stack.remove(node)
            return None

        for node in remaining_nodes:
            if node not in visited:
                cycle_start = dfs(node)
                if cycle_start:
                    cycle = [cycle_start]
                    current = parent[cycle_start]
                    while current != cycle_start:
                        cycle.append(current)
                        current = parent[current]
                    cycle.reverse()
                    return cycle

        return remaining_nodes


def get_upstream_nodes(node_id: str, edges: list[dict]) -> list[str]:
    """Get all nodes that feed into the specified node."""
    upstream = []
    for edge in edges:
        if edge.get("target") == node_id:
            source = edge.get("source")
            if source and source not in upstream:
                upstream.append(source)
    return upstream


def get_downstream_nodes(node_id: str, edges: list[dict]) -> list[str]:
    """Get all nodes that this node feeds into."""
    downstream = []
    for edge in edges:
        if edge.get("source") == node_id:
            target = edge.get("target")
            if target and target not in downstream:
                downstream.append(target)
    return downstream


def get_node_inputs(node_id: str, edges: list[dict]) -> dict[str, tuple[str, str]]:
    """Get input mapping for a node (targetHandle -> (source_node_id, sourceHandle))."""
    inputs = {}
    for edge in edges:
        if edge.get("target") == node_id:
            source = edge.get("source")
            source_handle = edge.get("sourceHandle", "out")
            target_handle = edge.get("targetHandle", "in")
            if source:
                inputs[target_handle] = (source, source_handle)
    return inputs


def get_node_outputs(
    node_id: str, edges: list[dict]
) -> dict[str, list[tuple[str, str]]]:
    """Get output routing for a node (sourceHandle -> [(target_node_id, targetHandle)])."""
    outputs = defaultdict(list)
    for edge in edges:
        if edge.get("source") == node_id:
            target = edge.get("target")
            source_handle = edge.get("sourceHandle", "out")
            target_handle = edge.get("targetHandle", "in")
            if target:
                outputs[source_handle].append((target, target_handle))
    return dict(outputs)


def find_trigger_nodes(nodes: list[dict]) -> list[str]:
    """Find all trigger nodes (category='triggers')."""
    triggers = []
    for node in nodes:
        if node.get("category") == "triggers":
            node_id = node.get("id")
            if node_id:
                triggers.append(node_id)
    return triggers


def validate_graph(nodes: list[dict], edges: list[dict]) -> list[str]:
    """Validate graph structure. Returns list of error messages."""
    errors = []

    node_ids = set()
    for i, node in enumerate(nodes):
        node_id = node.get("id")
        if not node_id:
            errors.append(f"Node at index {i} is missing 'id' field")
        else:
            node_ids.add(node_id)

    if len(node_ids) != len(nodes):
        id_counts = defaultdict(int)
        for node in nodes:
            node_id = node.get("id")
            if node_id:
                id_counts[node_id] += 1
        duplicates = [nid for nid, count in id_counts.items() if count > 1]
        for dup in duplicates:
            errors.append(f"Duplicate node ID found: '{dup}'")

    edge_connections = set()
    for i, edge in enumerate(edges):
        source = edge.get("source")
        target = edge.get("target")
        if not source:
            errors.append(f"Edge at index {i} is missing 'source' field")
            continue
        if not target:
            errors.append(f"Edge at index {i} is missing 'target' field")
            continue
        if source not in node_ids:
            errors.append(
                f"Invalid edge: source '{source}' -> target '{target}' "
                f"(source node not found)"
            )
            continue
        if target not in node_ids:
            errors.append(
                f"Invalid edge: source '{source}' -> target '{target}' "
                f"(target node not found)"
            )
            continue
        edge_connections.add(source)
        edge_connections.add(target)

    trigger_ids = set(find_trigger_nodes(nodes))
    for node_id in node_ids:
        if node_id not in edge_connections and node_id not in trigger_ids:
            errors.append(
                f"Orphan node detected: '{node_id}' has no incoming or outgoing edges "
                f"and is not a trigger"
            )

    if not errors and node_ids:
        try:
            sorter = TopologicalSorter(nodes, edges)
            sorter.sort()
        except CycleDetectedError as e:
            errors.append(str(e))
        except Exception as e:
            errors.append(f"Graph validation error: {str(e)}")

    return errors
