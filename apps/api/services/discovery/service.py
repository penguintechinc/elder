"""Deprecated location for the discovery service layer.

The implementation lives in ``apps.worker.discovery.service``. This module only
re-exports it so the legacy synchronous API fallback (``?legacy=true``) keeps
working from its historical import path.

It deliberately re-exports rather than duplicating: the two copies drifted and
the same schema-mismatch bugs had to be found and fixed twice in both.
"""

from apps.worker.discovery.service import DiscoveryService

__all__ = ["DiscoveryService"]
