"""Deprecated location for the aws_discovery discovery provider.

The implementation lives in ``apps.worker.discovery.aws_discovery``. This module only
re-exports it so the legacy synchronous API fallback (``?legacy=true``) keeps
working from its historical import path.

It deliberately re-exports rather than duplicating: the two copies drifted and
identical schema bugs had to be found and fixed twice in both.
"""

from apps.worker.discovery.aws_discovery import AWSDiscoveryClient

__all__ = ["AWSDiscoveryClient"]
