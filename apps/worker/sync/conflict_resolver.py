"""Conflict resolution engine for sync operations.

This module implements various conflict resolution strategies for handling
conflicts during two-way synchronization:

1. Last-Modified-Wins (timestamp-based)
2. Elder-Wins (local data takes precedence)
3. External-Wins (external platform data takes precedence)
4. Manual Resolution (requires human intervention)
5. Field-Level Merge (intelligent field merging)

The primary strategy is Last-Modified-Wins as specified in the v1.1.0 requirements.
"""

# flake8: noqa: E501


from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from apps.worker.sync.base import ConflictResolution, SyncMapping


class ResolutionStrategy(Enum):
    """Available conflict resolution strategies."""

    LAST_MODIFIED_WINS = "last_modified_wins"
    ELDER_WINS = "elder_wins"
    EXTERNAL_WINS = "external_wins"
    MANUAL = "manual"
    FIELD_MERGE = "field_merge"


class ConflictType(Enum):
    """Types of conflicts that can occur."""

    TIMESTAMP_CONFLICT = "timestamp"
    FIELD_MISMATCH = "field_mismatch"
    DELETED_EXTERNAL = "deleted_external"
    DELETED_LOCAL = "deleted_local"
    BOTH_MODIFIED = "both_modified"


class ConflictResolver:
    """Engine for resolving synchronization conflicts.

    Implements multiple resolution strategies with Last-Modified-Wins as the
    primary automatic resolution method.
    """

    def __init__(self, logger: Any):
        """Initialize conflict resolver.

        Args:
            logger: Logger instance for conflict logging
        """
        self.logger = logger

    def detect_conflict(
        self,
        elder_data: Dict[str, Any],
        external_data: Dict[str, Any],
        mapping: Optional[SyncMapping] = None,
    ) -> Optional[ConflictResolution]:
        """Detect if a conflict exists between Elder and external data.

        Args:
            elder_data: Elder resource data
            external_data: External platform resource data
            mapping: Existing sync mapping (if any)

        Returns:
            ConflictResolution if conflict detected, None otherwise
        """
        # If no mapping exists, no conflict (first-time sync)
        if not mapping:
            return None

        # Check for deletions
        if elder_data.get("deleted") and not external_data.get("deleted"):
            return ConflictResolution(
                conflict_type=ConflictType.DELETED_LOCAL.value,
                elder_data=elder_data,
                external_data=external_data,
                resolution_strategy=ResolutionStrategy.MANUAL.value,
            )

        if external_data.get("deleted") and not elder_data.get("deleted"):
            return ConflictResolution(
                conflict_type=ConflictType.DELETED_EXTERNAL.value,
                elder_data=elder_data,
                external_data=external_data,
                resolution_strategy=ResolutionStrategy.MANUAL.value,
            )

        # Check for timestamp conflicts (both modified since last sync)
        elder_modified = elder_data.get("updated_at")
        external_modified = external_data.get("updated_at")
        last_synced = mapping.last_synced_at

        if elder_modified and external_modified and last_synced:
            elder_modified_dt = self._parse_datetime(elder_modified)
            external_modified_dt = self._parse_datetime(external_modified)

            if elder_modified_dt > last_synced and external_modified_dt > last_synced:
                # Both modified since last sync - conflict!
                return ConflictResolution(
                    conflict_type=ConflictType.BOTH_MODIFIED.value,
                    elder_data=elder_data,
                    external_data=external_data,
                    resolution_strategy=ResolutionStrategy.LAST_MODIFIED_WINS.value,
                )

        # Check for field-level mismatches
        mismatched_fields = self._find_field_mismatches(elder_data, external_data)
        if mismatched_fields:
            return ConflictResolution(
                conflict_type=ConflictType.FIELD_MISMATCH.value,
                elder_data=elder_data,
                external_data=external_data,
                resolution_strategy=ResolutionStrategy.FIELD_MERGE.value,
            )

        return None

    def resolve_conflict(
        self,
        conflict: ConflictResolution,
        strategy: Optional[ResolutionStrategy] = None,
    ) -> ConflictResolution:
        """Resolve a conflict using specified or default strategy.

        Args:
            conflict: Conflict to resolve
            strategy: Resolution strategy to use (if None, uses conflict's default)

        Returns:
            Updated ConflictResolution with resolution_data populated
        """
        if strategy is None:
            strategy = ResolutionStrategy(conflict.resolution_strategy)

        self.logger.info(
            f"Resolving conflict with strategy: {strategy.value}",
            extra={
                "conflict_type": conflict.conflict_type,
                "strategy": strategy.value,
            },
        )

        if strategy == ResolutionStrategy.LAST_MODIFIED_WINS:
            conflict.resolution_data = self._resolve_last_modified_wins(conflict)
        elif strategy == ResolutionStrategy.ELDER_WINS:
            conflict.resolution_data = conflict.elder_data
        elif strategy == ResolutionStrategy.EXTERNAL_WINS:
            conflict.resolution_data = conflict.external_data
        elif strategy == ResolutionStrategy.FIELD_MERGE:
            conflict.resolution_data = self._resolve_field_merge(conflict)
        elif strategy == ResolutionStrategy.MANUAL:
            # Manual resolution requires human intervention
            self.logger.warning(
                "Conflict requires manual resolution",
                extra={"conflict_type": conflict.conflict_type},
            )
            return conflict

        conflict.resolved = True
        conflict.resolution_strategy = strategy.value

        return conflict

    def _resolve_last_modified_wins(
        self,
        conflict: ConflictResolution,
    ) -> Dict[str, Any]:
        """Resolve conflict using last-modified-wins strategy.

        Args:
            conflict: Conflict to resolve

        Returns:
            Resolved data (most recently modified version)
        """
        elder_modified = self._parse_datetime(conflict.elder_data.get("updated_at"))
        external_modified = self._parse_datetime(
            conflict.external_data.get("updated_at")
        )

        if not elder_modified or not external_modified:
            # Fallback to elder_wins if timestamps missing
            self.logger.warning(
                "Missing timestamps for last-modified-wins, defaulting to elder_wins"
            )
            return conflict.elder_data

        if elder_modified > external_modified:
            self.logger.info(
                f"Elder data is newer ({elder_modified} > {external_modified}), Elder wins"
            )
            return conflict.elder_data
        else:
            self.logger.info(
                f"External data is newer ({external_modified} > {elder_modified}), External wins"
            )
            return conflict.external_data

    def _resolve_field_merge(
        self,
        conflict: ConflictResolution,
    ) -> Dict[str, Any]:
        """Resolve conflict by merging fields intelligently.

        For each field:
        - If only one side has a value, use that value
        - If both have values but one is newer, use the newer value
        - If both have same timestamp, prefer Elder value

        Args:
            conflict: Conflict to resolve

        Returns:
            Merged data dictionary
        """
        merged = {}

        # Get all unique keys from both datasets
        all_keys = set(conflict.elder_data.keys()) | set(conflict.external_data.keys())

        for key in all_keys:
            elder_value = conflict.elder_data.get(key)
            external_value = conflict.external_data.get(key)

            # Skip metadata fields
            if key in ["id", "created_at", "updated_at"]:
                merged[key] = elder_value if elder_value else external_value
                continue

            # If only one has value, use it
            if elder_value is not None and external_value is None:
                merged[key] = elder_value
            elif external_value is not None and elder_value is None:
                merged[key] = external_value
            # If both have values and they differ
            elif elder_value != external_value:
                # Use last-modified-wins for individual fields if timestamps available
                elder_modified = self._parse_datetime(
                    conflict.elder_data.get("updated_at")
                )
                external_modified = self._parse_datetime(
                    conflict.external_data.get("updated_at")
                )

                if elder_modified and external_modified:
                    merged[key] = (
                        elder_value
                        if elder_modified > external_modified
                        else external_value
                    )
                else:
                    # Default to Elder value
                    merged[key] = elder_value
            else:
                # Values are the same
                merged[key] = elder_value

        self.logger.info(
            f"Field merge completed: {len(merged)} fields merged",
            extra={"merged_fields": list(merged.keys())},
        )

        return merged

    def _find_field_mismatches(
        self,
        elder_data: Dict[str, Any],
        external_data: Dict[str, Any],
    ) -> List[str]:
        """Find fields that have mismatched values.

        Args:
            elder_data: Elder resource data
            external_data: External platform resource data

        Returns:
            List of field names with mismatches
        """
        mismatches = []

        # Check common fields
        common_keys = set(elder_data.keys()) & set(external_data.keys())

        for key in common_keys:
            # Skip metadata fields
            if key in ["id", "created_at", "updated_at"]:
                continue

            if elder_data.get(key) != external_data.get(key):
                mismatches.append(key)

        return mismatches

    def _parse_datetime(self, dt: Any) -> Optional[datetime]:
        """Parse datetime from various formats.

        Args:
            dt: Datetime value (datetime object, ISO string, or timestamp)

        Returns:
            datetime object or None if parsing fails
        """
        if dt is None:
            return None

        if isinstance(dt, datetime):
            return dt

        if isinstance(dt, str):
            try:
                # Try ISO format
                return datetime.fromisoformat(dt.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        if isinstance(dt, (int, float)):
            try:
                # Try Unix timestamp
                return datetime.fromtimestamp(dt)
            except (ValueError, OSError):
                pass

        return None

    def get_conflict_summary(
        self,
        conflict: ConflictResolution,
    ) -> Dict[str, Any]:
        """Generate a human-readable summary of a conflict.

        Args:
            conflict: Conflict to summarize

        Returns:
            Dictionary with conflict summary
        """
        return {
            "conflict_type": conflict.conflict_type,
            "resolution_strategy": conflict.resolution_strategy,
            "resolved": conflict.resolved,
            "elder_modified": conflict.elder_data.get("updated_at"),
            "external_modified": conflict.external_data.get("updated_at"),
            "mismatched_fields": self._find_field_mismatches(
                conflict.elder_data,
                conflict.external_data,
            ),
            "resolution_data_set": conflict.resolution_data is not None,
        }
