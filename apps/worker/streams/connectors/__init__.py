"""Connector framework for Streams workflow nodes.

Dynamically generates workflow nodes from connector manifests.
"""

from .registry import discover_connectors

__all__ = ["discover_connectors"]
