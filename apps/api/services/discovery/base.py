"""Deprecated location for the base discovery provider.

The implementation lives in ``apps.worker.discovery.base``. This module only
re-exports it so the legacy synchronous API fallback (``?legacy=true``) keeps
working from its historical import path.

It deliberately re-exports rather than duplicating: the two copies drifted and
identical schema bugs had to be found and fixed twice in both.
"""

from apps.worker.discovery.base import BaseDiscoveryProvider

__all__ = ["BaseDiscoveryProvider"]
