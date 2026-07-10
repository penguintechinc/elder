"""Connector Registry for Streams.

Loads connector manifests from YAML files and dynamically generates
node classes for each action, trigger, and transform.

Ported from icestreams-worker.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .base import ConnectorManifest
from .node_generator import generate_nodes_from_connector

logger = logging.getLogger(__name__)

_CONNECTORS_INITIALIZED = False


def discover_connectors() -> int:
    """Discover and load all connector manifests.

    Scans the connectors/manifests/ directory for YAML files, loads
    each manifest, and generates node classes for all triggers, actions,
    and transforms. Idempotent—safe to call multiple times.

    Returns:
        Total number of nodes generated across all connectors.
    """
    global _CONNECTORS_INITIALIZED
    if _CONNECTORS_INITIALIZED:
        logger.debug("Connector discovery already initialized, skipping")
        return 0

    _CONNECTORS_INITIALIZED = True
    manifest_dir = Path(__file__).parent / "manifests"

    if not manifest_dir.exists():
        logger.warning(f"Connector manifest directory not found: {manifest_dir}")
        return 0

    manifest_files = sorted(manifest_dir.glob("*.yaml"))
    if not manifest_files:
        logger.warning(f"No YAML manifest files found in {manifest_dir}")
        return 0

    total_generated = 0
    failed_manifests = []

    for manifest_file in manifest_files:
        try:
            manifest = ConnectorManifest.from_yaml(str(manifest_file))
            generated = generate_nodes_from_connector(manifest)
            total_generated += generated
            logger.info(
                f"Loaded connector {manifest.id!r} from {manifest_file.name}: "
                f"generated {generated} nodes"
            )
        except Exception as e:
            logger.error(f"Failed to load manifest {manifest_file.name}: {e}")
            failed_manifests.append(manifest_file.name)

    if failed_manifests:
        logger.warning(
            f"Failed to load {len(failed_manifests)} connector manifests: "
            f"{', '.join(failed_manifests)}"
        )

    logger.info(
        f"Connector discovery complete: {len(manifest_files)} manifests loaded, "
        f"{len(failed_manifests)} failed, {total_generated} nodes generated"
    )

    return total_generated
