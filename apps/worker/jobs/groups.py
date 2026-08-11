"""Resolve worker job-bus groups from enabled modules."""

from __future__ import annotations

from collections.abc import Mapping

import structlog

logger = structlog.get_logger(__name__)


def resolve_worker_groups(env: Mapping[str, str]) -> set[str]:
    """Resolve worker job-bus consumer groups from enabled modules.

    Reads the enabled modules from the environment (via apps.api.modules.registry.resolve_enabled)
    and collects all worker_task_groups declared by those modules.

    Args:
        env: Environment variables (typically os.environ)

    Returns:
        Set of worker task group names (e.g., {"discovery", "sbom_scan"})

    Raises:
        ValueError: If module resolution fails (unknown module, missing dependency, etc.)
    """
    from apps.api.modules.registry import resolve_enabled

    try:
        enabled_manifests = resolve_enabled(env)
    except ValueError as e:
        logger.error("module_resolution_failed", error=str(e))
        raise

    groups: set[str] = set()
    for manifest in enabled_manifests:
        groups.update(manifest.worker_task_groups)

    logger.info(
        "worker_groups_resolved",
        group_count=len(groups),
        groups=sorted(groups),
    )

    return groups
