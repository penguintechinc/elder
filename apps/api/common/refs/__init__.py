"""Cross-reference management and resolution.

Provides:
- registry: ResolvableType registry for all resolvable resource types
- service: CRUD operations on references (penguin-dal)
"""

from apps.api.common.refs.registry import get_registry, get_type, register
from apps.api.common.refs.service import (
    backlinks_for,
    create_reference,
    delete_references_for_source,
    outbound_for,
)

__all__ = [
    "get_registry",
    "get_type",
    "register",
    "create_reference",
    "delete_references_for_source",
    "backlinks_for",
    "outbound_for",
]
