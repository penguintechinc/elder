"""Node Registry for Streams Playbook Executor.

Maps node_type strings to their implementation classes.
Thread-safe singleton pattern.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Dict, Optional, Type

logger = logging.getLogger(__name__)

NodeClassType = type


@dataclass(slots=True, frozen=True)
class NodeInfo:
    """Metadata about a registered node type."""

    node_type: str
    node_class: NodeClassType
    category: str
    display_name: str
    description: str


class NodeRegistryError(Exception):
    """Base exception for node registry errors."""

    pass


class NodeNotFoundError(NodeRegistryError):
    """Raised when a requested node type is not registered."""

    pass


class DuplicateNodeError(NodeRegistryError):
    """Raised when attempting to register a node type that already exists."""

    pass


class NodeRegistry:
    """Thread-safe singleton registry for node types."""

    _registry: dict[str, NodeInfo] = {}
    _lock = threading.Lock()
    _initialized = False

    @classmethod
    def register(
        cls,
        node_type: str,
        node_class: NodeClassType,
        category: str,
        display_name: str | None = None,
        description: str | None = None,
        allow_override: bool = False,
    ) -> None:
        """Register a node class with the registry."""
        if not node_type or not isinstance(node_type, str):
            raise ValueError(f"Invalid node_type: {node_type}")
        if not node_class:
            raise ValueError(f"Invalid node_class for node_type '{node_type}'")
        if not category or not isinstance(category, str):
            raise ValueError(f"Invalid category for node_type '{node_type}'")

        with cls._lock:
            if node_type in cls._registry and not allow_override:
                existing = cls._registry[node_type]
                raise DuplicateNodeError(
                    f"Node type '{node_type}' is already registered "
                    f"to {existing.node_class.__name__}. "
                    f"Use allow_override=True to override."
                )

            final_display_name = display_name or node_type.replace("_", " ").title()
            final_description = description or (
                node_class.__doc__.strip().split("\n")[0]
                if node_class.__doc__
                else f"{node_type} node"
            )

            node_info = NodeInfo(
                node_type=node_type,
                node_class=node_class,
                category=category,
                display_name=final_display_name,
                description=final_description,
            )

            cls._registry[node_type] = node_info
            logger.info(
                f"Registered node type '{node_type}' -> {node_class.__name__} "
                f"(category: {category})"
            )

    @classmethod
    def get(cls, node_type: str, raise_on_missing: bool = True) -> NodeClassType | None:
        """Get a node class by its type identifier."""
        with cls._lock:
            node_info = cls._registry.get(node_type)
            if node_info is None:
                if raise_on_missing:
                    available = ", ".join(sorted(cls._registry.keys()))
                    raise NodeNotFoundError(
                        f"Node type '{node_type}' is not registered. "
                        f"Available types: {available or 'none'}"
                    )
                return None
            return node_info.node_class

    @classmethod
    def get_info(cls, node_type: str) -> NodeInfo | None:
        """Get full node information by type identifier."""
        with cls._lock:
            return cls._registry.get(node_type)

    @classmethod
    def is_registered(cls, node_type: str) -> bool:
        """Check if a node type is registered."""
        with cls._lock:
            return node_type in cls._registry

    @classmethod
    def count(cls) -> int:
        """Get the total number of registered nodes."""
        with cls._lock:
            return len(cls._registry)

    @classmethod
    def clear(cls) -> int:
        """Clear all registered nodes."""
        with cls._lock:
            count = len(cls._registry)
            cls._registry.clear()
            cls._initialized = False
            logger.warning(f"Cleared {count} registered node types")
            return count

    @classmethod
    def is_initialized(cls) -> bool:
        """Check if registry has been initialized."""
        with cls._lock:
            return cls._initialized

    @classmethod
    def mark_initialized(cls) -> None:
        """Mark the registry as initialized."""
        with cls._lock:
            cls._initialized = True
            logger.info(
                f"Node registry marked as initialized ({len(cls._registry)} nodes)"
            )


def register_node(
    node_type: str,
    category: str,
    display_name: str | None = None,
    description: str | None = None,
) -> Callable:
    """Decorator to automatically register a node class."""

    def decorator(node_class: NodeClassType) -> NodeClassType:
        NodeRegistry.register(
            node_type=node_type,
            node_class=node_class,
            category=category,
            display_name=display_name,
            description=description,
        )
        return node_class

    return decorator


def discover_nodes() -> int:
    """Discover and import all node modules to trigger registration."""
    if NodeRegistry.is_initialized():
        logger.warning("Node registry already initialized, skipping discovery")
        return NodeRegistry.count()

    initial_count = NodeRegistry.count()
    logger.info("Starting node discovery...")

    node_modules = [
        "apps.worker.streams.nodes.actions",
        "apps.worker.streams.nodes.transforms",
        "apps.worker.streams.nodes.conditionals",
    ]

    for module_path in node_modules:
        try:
            __import__(module_path)
            logger.info(f"Successfully imported {module_path}")
        except ImportError as e:
            logger.debug(f"Module {module_path} not found: {e}")
        except Exception as e:
            logger.error(f"Error importing {module_path}: {e}", exc_info=True)

    NodeRegistry.mark_initialized()

    discovered_count = NodeRegistry.count() - initial_count
    total_count = NodeRegistry.count()

    logger.info(
        f"Node discovery complete. Discovered {discovered_count} new nodes. "
        f"Total registered: {total_count}"
    )

    return total_count
