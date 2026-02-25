"""Base sync framework for external platform integration.

This module provides abstract base classes and data structures for implementing
two-way synchronization with external project management platforms (GitHub, GitLab,
Jira, Trello, OpenProject).

All platform-specific sync clients should inherit from BaseSyncClient and implement
the required abstract methods.
"""

# flake8: noqa: E501


import abc
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydal import DAL


class SyncDirection(Enum):
    """Direction of synchronization."""

    ELDER_TO_EXTERNAL = "elder_to_external"
    EXTERNAL_TO_ELDER = "external_to_elder"
    BIDIRECTIONAL = "bidirectional"


class SyncStatus(Enum):
    """Status of a sync operation."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"
    CONFLICT = "conflict"


class ResourceType(Enum):
    """Type of resource being synced."""

    ISSUE = "issue"
    PROJECT = "project"
    MILESTONE = "milestone"
    LABEL = "label"
    ORGANIZATION = "organization"
    COMMENT = "comment"


@dataclass
class SyncMapping:
    """Mapping between Elder resource and external platform resource.

    Attributes:
        elder_type: Type of Elder resource (issue, project, etc.)
        elder_id: Elder resource ID
        external_platform: External platform name (github, jira, etc.)
        external_id: External platform resource ID
        sync_config_id: Reference to sync configuration
        last_synced_at: Last successful sync timestamp
        elder_updated_at: Elder resource last modified timestamp
        external_updated_at: External resource last modified timestamp
    """

    elder_type: str
    elder_id: int
    external_platform: str
    external_id: str
    sync_config_id: int
    last_synced_at: Optional[datetime] = None
    elder_updated_at: Optional[datetime] = None
    external_updated_at: Optional[datetime] = None


@dataclass
class SyncOperation:
    """Represents a single synchronization operation.

    Attributes:
        operation_type: Type of operation (create, update, delete)
        resource_type: Type of resource being synced
        direction: Sync direction
        elder_data: Elder resource data
        external_data: External platform resource data
        mapping: Sync mapping information
        correlation_id: Correlation ID for distributed tracing
        metadata: Additional operation-specific metadata
    """

    operation_type: str  # create, update, delete
    resource_type: ResourceType
    direction: SyncDirection
    elder_data: Optional[Dict[str, Any]] = None
    external_data: Optional[Dict[str, Any]] = None
    mapping: Optional[SyncMapping] = None
    correlation_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SyncResult:
    """Result of a synchronization operation.

    Attributes:
        status: Overall sync status
        operation: The sync operation that was performed
        items_synced: Number of items successfully synced
        items_failed: Number of items that failed to sync
        conflicts: List of conflicts encountered
        errors: List of errors encountered
        created_mappings: Newly created sync mappings
        updated_mappings: Updated sync mappings
        metadata: Additional result metadata
    """

    status: SyncStatus
    operation: SyncOperation
    items_synced: int = 0
    items_failed: int = 0
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    created_mappings: List[SyncMapping] = field(default_factory=list)
    updated_mappings: List[SyncMapping] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        """Check if sync was successful."""
        return self.status in [SyncStatus.SUCCESS, SyncStatus.PARTIAL_SUCCESS]

    @property
    def has_conflicts(self) -> bool:
        """Check if sync encountered conflicts."""
        return len(self.conflicts) > 0


@dataclass
class ConflictResolution:
    """Represents a conflict and its resolution strategy.

    Attributes:
        conflict_type: Type of conflict (timestamp, field_mismatch, etc.)
        elder_data: Elder's version of the data
        external_data: External platform's version of the data
        resolution_strategy: How to resolve the conflict
        resolved: Whether conflict has been resolved
        resolution_data: Final resolved data
    """

    conflict_type: str
    elder_data: Dict[str, Any]
    external_data: Dict[str, Any]
    resolution_strategy: str = "manual"  # manual, elder_wins, external_wins, merge
    resolved: bool = False
    resolution_data: Optional[Dict[str, Any]] = None


class BaseSyncClient(abc.ABC):
    """Abstract base class for all platform sync clients.

    All platform-specific sync implementations (GitHub, GitLab, Jira, etc.)
    should inherit from this class and implement the required abstract methods.

    Attributes:
        platform_name: Name of the platform (github, gitlab, jira, etc.)
        config: Platform-specific configuration
        db: PyDAL database instance
        sync_config_id: ID of the sync configuration
        logger: Logger instance with correlation ID support
    """

    def __init__(
        self,
        platform_name: str,
        config: Dict[str, Any],
        db: DAL,
        sync_config_id: int,
        logger: Any,
    ):
        """Initialize sync client.

        Args:
            platform_name: Name of the platform
            config: Platform-specific configuration (API keys, URLs, etc.)
            db: PyDAL database instance
            sync_config_id: Sync configuration ID
            logger: Logger instance
        """
        self.platform_name = platform_name
        self.config = config
        self.db = db
        self.sync_config_id = sync_config_id
        self.logger = logger

    @abc.abstractmethod
    def validate_config(self) -> bool:
        """Validate platform configuration.

        Returns:
            True if configuration is valid, False otherwise
        """

    @abc.abstractmethod
    def test_connection(self) -> bool:
        """Test connection to external platform.

        Returns:
            True if connection successful, False otherwise
        """

    @abc.abstractmethod
    def sync_issue(
        self,
        operation: SyncOperation,
    ) -> SyncResult:
        """Sync a single issue.

        Args:
            operation: Sync operation details

        Returns:
            Result of the sync operation
        """

    @abc.abstractmethod
    def sync_project(
        self,
        operation: SyncOperation,
    ) -> SyncResult:
        """Sync a single project.

        Args:
            operation: Sync operation details

        Returns:
            Result of the sync operation
        """

    @abc.abstractmethod
    def sync_milestone(
        self,
        operation: SyncOperation,
    ) -> SyncResult:
        """Sync a single milestone.

        Args:
            operation: Sync operation details

        Returns:
            Result of the sync operation
        """

    @abc.abstractmethod
    def sync_label(
        self,
        operation: SyncOperation,
    ) -> SyncResult:
        """Sync a single label.

        Args:
            operation: Sync operation details

        Returns:
            Result of the sync operation
        """

    @abc.abstractmethod
    def batch_sync(
        self,
        resource_type: ResourceType,
        since: Optional[datetime] = None,
    ) -> SyncResult:
        """Perform batch synchronization for a resource type.

        Args:
            resource_type: Type of resources to sync
            since: Only sync resources modified after this timestamp

        Returns:
            Aggregate result of batch sync
        """

    @abc.abstractmethod
    def handle_webhook(
        self,
        webhook_data: Dict[str, Any],
    ) -> SyncResult:
        """Handle incoming webhook from external platform.

        Args:
            webhook_data: Webhook payload data

        Returns:
            Result of processing the webhook
        """

    def get_mapping(
        self,
        resource_type: ResourceType,
        elder_id: Optional[int] = None,
        external_id: Optional[str] = None,
    ) -> Optional[SyncMapping]:
        """Get sync mapping for a resource.

        Args:
            resource_type: Type of resource
            elder_id: Elder resource ID (if known)
            external_id: External resource ID (if known)

        Returns:
            SyncMapping if found, None otherwise
        """
        query = (
            (self.db.sync_mappings.elder_type == resource_type.value)
            & (self.db.sync_mappings.external_platform == self.platform_name)
            & (self.db.sync_mappings.sync_config_id == self.sync_config_id)
        )

        if elder_id is not None:
            query &= self.db.sync_mappings.elder_id == elder_id
        if external_id is not None:
            query &= self.db.sync_mappings.external_id == external_id

        mapping_row = self.db(query).select().first()

        if mapping_row:
            return SyncMapping(
                elder_type=mapping_row.elder_type,
                elder_id=mapping_row.elder_id,
                external_platform=mapping_row.external_platform,
                external_id=mapping_row.external_id,
                sync_config_id=mapping_row.sync_config_id,
                last_synced_at=mapping_row.last_synced_at,
                elder_updated_at=mapping_row.elder_updated_at,
                external_updated_at=mapping_row.external_updated_at,
            )

        return None

    def create_mapping(
        self,
        mapping: SyncMapping,
        sync_method: str = "webhook",
    ) -> int:
        """Create a new sync mapping.

        Args:
            mapping: Sync mapping to create
            sync_method: Method used for sync (webhook, poll, batch, manual)

        Returns:
            ID of created mapping
        """
        mapping_id = self.db.sync_mappings.insert(
            elder_type=mapping.elder_type,
            elder_id=mapping.elder_id,
            external_platform=mapping.external_platform,
            external_id=mapping.external_id,
            sync_config_id=mapping.sync_config_id,
            sync_status="synced",
            sync_method=sync_method,
            last_synced_at=datetime.now(),
            elder_updated_at=mapping.elder_updated_at,
            external_updated_at=mapping.external_updated_at,
        )
        self.db.commit()

        self.logger.info(
            f"Created sync mapping: {mapping.elder_type} {mapping.elder_id} <-> {mapping.external_id}",
            extra={"mapping_id": mapping_id},
        )

        return mapping_id

    def update_mapping(
        self,
        mapping: SyncMapping,
        sync_status: str = "synced",
    ) -> None:
        """Update an existing sync mapping.

        Args:
            mapping: Updated sync mapping
            sync_status: New sync status
        """
        query = (
            (self.db.sync_mappings.elder_type == mapping.elder_type)
            & (self.db.sync_mappings.elder_id == mapping.elder_id)
            & (self.db.sync_mappings.external_platform == mapping.external_platform)
            & (self.db.sync_mappings.sync_config_id == mapping.sync_config_id)
        )

        self.db(query).update(
            sync_status=sync_status,
            last_synced_at=datetime.now(),
            elder_updated_at=mapping.elder_updated_at,
            external_updated_at=mapping.external_updated_at,
        )
        self.db.commit()

    def record_sync_history(
        self,
        result: SyncResult,
        sync_type: str = "webhook",
    ) -> None:
        """Record sync operation in history.

        Args:
            result: Sync result to record
            sync_type: Type of sync operation
        """
        self.db.sync_history.insert(
            sync_config_id=self.sync_config_id,
            correlation_id=result.operation.correlation_id,
            sync_type=sync_type,
            items_synced=result.items_synced,
            items_failed=result.items_failed,
            started_at=datetime.now(),
            completed_at=datetime.now(),
            success=result.is_success,
            error_message="; ".join(result.errors) if result.errors else None,
            sync_metadata=result.metadata,
        )
        self.db.commit()

    def record_conflict(
        self,
        conflict: ConflictResolution,
        mapping_id: int,
    ) -> int:
        """Record a sync conflict for manual resolution.

        Args:
            conflict: Conflict details
            mapping_id: ID of the sync mapping with conflict

        Returns:
            ID of created conflict record
        """
        conflict_id = self.db.sync_conflicts.insert(
            mapping_id=mapping_id,
            conflict_type=conflict.conflict_type,
            elder_data=conflict.elder_data,
            external_data=conflict.external_data,
            resolution_strategy=conflict.resolution_strategy,
            resolved=conflict.resolved,
        )
        self.db.commit()

        self.logger.warning(
            f"Sync conflict recorded: {conflict.conflict_type}",
            extra={"conflict_id": conflict_id, "mapping_id": mapping_id},
        )

        return conflict_id
